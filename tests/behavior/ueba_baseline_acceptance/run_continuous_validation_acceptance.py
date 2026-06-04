"""持续流量与 Validation 联动验收 Runner。

本 Runner 串联 B 阶段的持续登录流量生成器与正式 Validation CLI，
验证正常日志评分 LOW、组合异常日志评分 HIGH/CRITICAL、幂等性与 baseline 不变性。

导入本模块或执行 --help 不会连接 ClickHouse、不启动线程、不写库、不清理数据。
"""

from __future__ import annotations

import argparse
from datetime import date, datetime, timedelta, timezone
import json
import os
from pathlib import Path
import subprocess
import sys
import time
from typing import Any, NamedTuple

from .clickhouse_writer import FixtureClickHouseWriter, create_clickhouse_client, validate_identifier
from .config import AcceptanceConfig
from .continuous_login_generator import ContinuousLoginGenerator, DEFAULT_LOGS_PER_SECOND
from .validation_cleanup import cleanup_validation_results

PROJECT_ROOT = Path(__file__).resolve().parents[3]
MARKER = "ueba_continuous_fixture"
USERNAME = "fixture_user_stable_0001"
CONTINUOUS_MARKER_CONDITION = f"position(raw_log, '{MARKER}') > 0"
DEFAULT_REPORT_PATH = Path(".tox/manual/continuous_validation_acceptance_report.json")

REQUIRED_REPORT_FIELDS = (
    "success",
    "started_at",
    "finished_at",
    "username",
    "model_version",
    "normal_window",
    "combo_window",
    "idempotency_window",
    "counts_before",
    "counts_after",
    "counts_after_cleanup",
    "normal_phase",
    "combo_phase",
    "idempotency_phase",
    "normal_average_score",
    "combo_average_score",
    "baseline_unchanged",
    "baseline_count_unchanged",
    "generator_stopped",
    "cleanup_performed",
    "validation_run_ids",
    "errors",
)


class ManualAcceptanceError(RuntimeError):
    """预期之内的手动验收失败。"""


# ---------------------------------------------------------------------------
# 统一无标注动态窗口
# ---------------------------------------------------------------------------


class _Window(NamedTuple):
    """有效验收窗口：start 在 generator 控制前记录，effective_end 包含 5s 缓冲。"""

    start: datetime
    effective_end: datetime

    @property
    def start_str(self) -> str:
        return _naive_str(self.start)

    @property
    def end_str(self) -> str:
        return _naive_str(self.effective_end)

    def to_dict(self) -> dict[str, str]:
        return {"start": self.start_str, "end": self.end_str}


# ---------------------------------------------------------------------------
# 时间工具
# ---------------------------------------------------------------------------


def _utc_now_naive() -> datetime:
    """返回 UTC 无时区标注的当前时间。"""
    return datetime.now(timezone.utc).replace(tzinfo=None, microsecond=0)


def _naive_str(value: datetime) -> str:
    """格式化无时区时间为 ClickHouse 兼容字符串。"""
    return value.strftime("%Y-%m-%d %H:%M:%S")


def _naive_iso(value: datetime) -> str:
    """ISO 格式，无 Z、无时区后缀。"""
    return value.isoformat(sep="T", timespec="seconds")


def _json_default(value: object) -> str:
    """datetime / date 稳定序列化，未知对象抛出 TypeError。"""
    if isinstance(value, datetime):
        return _naive_iso(value)
    if isinstance(value, date):
        return value.isoformat()
    raise TypeError(f"Object of type {type(value).__name__} is not JSON serializable")


def write_report(path: Path, report: dict[str, Any]) -> None:
    """写入项目本地的 JSON 验收报告。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True, default=_json_default) + "\n",
        encoding="utf-8",
    )


def _resolve_project_path(value: str | os.PathLike[str]) -> Path:
    """解析并校验报告路径必须在项目目录内。"""
    path = Path(value)
    if not path.is_absolute():
        path = PROJECT_ROOT / path
    resolved = path.resolve()
    if not resolved.is_relative_to(PROJECT_ROOT):
        raise ManualAcceptanceError(f"path must stay inside project directory: {value}")
    return resolved


# ---------------------------------------------------------------------------
# 查询辅助
# ---------------------------------------------------------------------------


def _scalar_query(client: Any, sql: str, parameters: dict[str, Any]) -> int:
    result = client.query(sql, parameters=parameters)
    if hasattr(result, "named_results"):
        rows = list(result.named_results() if callable(result.named_results) else result.named_results)
    elif hasattr(result, "result_rows"):
        rows = [dict(zip(result.column_names, r)) for r in result.result_rows]
    else:
        rows = list(result)
    if rows:
        first = rows[0]
        if isinstance(first, dict):
            return int(next(iter(first.values())) or 0)
        return int(first[0] if isinstance(first, (list, tuple)) else first)
    return 0


def _named_query(client: Any, sql: str, parameters: dict[str, Any]) -> list[dict[str, Any]]:
    result = client.query(sql, parameters=parameters)
    if hasattr(result, "named_results"):
        rows = result.named_results() if callable(result.named_results) else result.named_results
        return list(rows)
    if hasattr(result, "result_rows") and hasattr(result, "column_names"):
        return [dict(zip(result.column_names, row)) for row in result.result_rows]
    if isinstance(result, list):
        return [dict(row) for row in result]
    return []


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------


class ContinuousValidationRunner:
    """C 阶段持续流量与 Validation 联动验收编排器。"""

    def __init__(
        self,
        args: argparse.Namespace,
        *,
        client_factory=create_clickhouse_client,
        sleep=time.sleep,
        tick_seconds: float = 1.0,
        generator_factory=None,
    ) -> None:
        self.args = args
        self.client_factory = client_factory
        self.sleep = sleep
        self.tick_seconds = tick_seconds
        self.generator_factory = generator_factory
        self.config = AcceptanceConfig()
        self.database = validate_identifier(self.config.clickhouse_database)
        self.model_version = self.config.model_version
        self.report_path = _resolve_project_path(args.report_path)
        self.client: Any = None
        self.writer: FixtureClickHouseWriter | None = None
        self.generator: ContinuousLoginGenerator | None = None
        self.cleanup_performed = False
        self.validation_run_ids: list[str] = []
        self.validation_windows: dict[str, _Window] = {}
        self.baseline_count_before = 0
        self.baseline_hash_before = ""
        self._logs_per_second = DEFAULT_LOGS_PER_SECOND
        self.expected_logs_per_phase = self.args.run_seconds * self._logs_per_second
        self.report = _empty_report()
        self.report["model_version"] = self.model_version

    # ------------------------------------------------------------------
    # 主流程
    # ------------------------------------------------------------------

    def run(self) -> dict[str, Any]:
        self.report["started_at"] = _naive_iso(_utc_now_naive())
        try:
            validate_cli_gates(self.args)
            self.client = self.client_factory(self.config)
            self.writer = FixtureClickHouseWriter(
                client=self.client, database=self.database,
                batch_size=self.config.clickhouse_batch_size,
            )
            self._check_baseline_user()
            self._capture_baseline_before()
            self.report["counts_before"] = self._collect_counts()

            if self.args.cleanup_before:
                self._cleanup_continuous_logs(
                    USERNAME, _utc_now_naive() - timedelta(hours=24), _utc_now_naive(),
                )
                self.report["counts_before"] = self._collect_counts()

            self._make_generator(mode="normal")

            # ---- 第一段：正常流量 ----
            normal_start = _utc_now_naive()
            self.generator.start()
            self.sleep(self.args.run_seconds)
            self.generator.pause()
            normal_raw_end = _utc_now_naive()
            if normal_start >= normal_raw_end:
                raise ManualAcceptanceError("正常阶段: start_time 必须严格早于 raw_end_time")
            normal_window = _Window(normal_start, normal_raw_end + timedelta(seconds=5))
            normal_run_id = f"continuous_normal_{_utc_now_naive().strftime('%Y%m%d%H%M%S')}"
            self.validation_run_ids.append(normal_run_id)
            self.validation_windows[normal_run_id] = normal_window
            self.report["validation_run_ids"] = list(self.validation_run_ids)
            self.report["normal_window"] = normal_window.to_dict()

            self._wait_until_window_closed(normal_window)
            self._check_window_isolation(normal_window)
            self.report["normal_phase"] = self._execute_phase(normal_run_id, normal_window)
            self._validate_normal_phase(self.report["normal_phase"])

            # ---- 第二段：组合异常流量 ----
            combo_start = _utc_now_naive()
            self.generator.set_mode("combo_anomaly")
            self.generator.resume()
            self.sleep(self.args.run_seconds)
            self.generator.pause()
            combo_raw_end = _utc_now_naive()
            if combo_start >= combo_raw_end:
                raise ManualAcceptanceError("组合异常阶段: start_time 必须严格早于 raw_end_time")
            combo_window = _Window(combo_start, combo_raw_end + timedelta(seconds=5))
            combo_run_id = f"continuous_combo_{_utc_now_naive().strftime('%Y%m%d%H%M%S')}"
            self.validation_run_ids.append(combo_run_id)
            self.validation_windows[combo_run_id] = combo_window
            self.report["validation_run_ids"] = list(self.validation_run_ids)
            self.report["combo_window"] = combo_window.to_dict()

            self._wait_until_window_closed(combo_window)
            self._check_window_isolation(combo_window)
            self.report["combo_phase"] = self._execute_phase(combo_run_id, combo_window)
            self._validate_combo_phase(self.report["combo_phase"], self.report["normal_phase"])

            # ---- 第三段：幂等性 ----
            idem_start = min(normal_window.start, combo_window.start)
            idem_end = max(normal_window.effective_end, combo_window.effective_end)
            idem_window = _Window(idem_start, idem_end)
            idem_run_id = f"continuous_idempotent_{_utc_now_naive().strftime('%Y%m%d%H%M%S')}"
            self.validation_run_ids.append(idem_run_id)
            self.validation_windows[idem_run_id] = idem_window
            self.report["validation_run_ids"] = list(self.validation_run_ids)
            self.report["idempotency_window"] = idem_window.to_dict()

            self._wait_until_window_closed(idem_window)
            self._check_window_isolation(idem_window)
            self.report["idempotency_phase"] = self._execute_phase(idem_run_id, idem_window)
            self._validate_idempotency_phase(self.report["idempotency_phase"], idem_run_id)

            # ---- 收尾 ----
            self.report["normal_average_score"] = self.report["normal_phase"]["average_score"]
            self.report["combo_average_score"] = self.report["combo_phase"]["average_score"]
            self._verify_baseline_unchanged()
            if not self.report["baseline_unchanged"]:
                raise ManualAcceptanceError("baseline 内容在验收期间发生了变化")
            if not self.report["baseline_count_unchanged"]:
                raise ManualAcceptanceError("baseline 行数在验收期间发生了变化")
            self.report["counts_after"] = self._collect_counts()
            self.report["success"] = True

        except Exception as exc:
            self.report["errors"].append(f"{type(exc).__name__}: {exc}")

        finally:
            self._finalize()

        return self.report

    def _finalize(self) -> None:
        """finally 块：shutdown、cleanup、写报告、关闭 client。"""
        # ---- generator shutdown（try/except 保护） ----
        if self.generator is not None:
            try:
                self.generator.shutdown()
            except Exception as exc:
                self.report["success"] = False
                self.report["errors"].append(f"generator.shutdown() 异常: {type(exc).__name__}: {exc}")
            else:
                try:
                    st = self.generator.status()
                    self.report["generator_stopped"] = not st.get("running", True)
                    if not self.report["generator_stopped"]:
                        self.report["success"] = False
                        self.report["errors"].append("generator shutdown 后仍处于 running 状态")
                except Exception as exc:
                    self.report["success"] = False
                    self.report["errors"].append(f"generator.status() 异常: {type(exc).__name__}: {exc}")

        # ---- cleanup ----
        should_cleanup = self._should_cleanup()
        if should_cleanup and self.client is not None:
            try:
                self._run_cleanup()
            except Exception as exc:
                self.report["success"] = False
                self.report["errors"].append(f"cleanup 异常: {type(exc).__name__}: {exc}")

        # ---- 写报告 + 关 client（无论如何都要执行） ----
        self.report["cleanup_performed"] = self.cleanup_performed
        self.report["finished_at"] = _naive_iso(_utc_now_naive())

        if self.client is not None:
            try:
                self.report["counts_after_cleanup"] = self._collect_counts()
            except Exception:
                self.report["counts_after_cleanup"] = {}
            try:
                self.client.close()
            except Exception:
                pass

        try:
            write_report(self.report_path, self.report)
        except Exception as exc:
            self.report["success"] = False
            self.report["errors"].append(f"report write failed: {type(exc).__name__}: {exc}")
            print(f"report write failed: {type(exc).__name__}: {exc}", file=sys.stderr)

    # ------------------------------------------------------------------
    # 阶段严格校验
    # ------------------------------------------------------------------

    @staticmethod
    def _check_phase_consistency(phase: dict[str, Any], label: str) -> None:
        """验证 generated/written/processed/scored/validation_result 五值一致。"""
        generated = int(phase.get("generated_log_count", 0))
        written = int(phase.get("written_count", 0))
        processed = int(phase.get("processed_count", 0))
        scored = int(phase.get("scored_count", 0))
        vrc = int(phase.get("validation_result_count", 0))
        if not (generated == written == processed == scored == vrc):
            raise ManualAcceptanceError(
                f"{label}: 计数不一致 — "
                f"generated={generated} written={written} processed={processed} "
                f"scored={scored} validation_result={vrc}"
            )

    def _check_count_in_expected_range(self, phase: dict[str, Any], label: str) -> None:
        """验证 generated_log_count 在预期范围内；生成线程启动/暂停允许 ±1 tick。"""
        generated = int(phase.get("generated_log_count", 0))
        min_expected = self.expected_logs_per_phase - self._logs_per_second
        max_expected = self.expected_logs_per_phase + self._logs_per_second
        if not (min_expected <= generated <= max_expected):
            raise ManualAcceptanceError(
                f"{label}: generated_log_count={generated} 超出预期范围 "
                f"[{min_expected}, {max_expected}]"
            )

    def _validate_normal_phase(self, phase: dict[str, Any]) -> None:
        if not phase["validation_success"]:
            raise ManualAcceptanceError("正常阶段: Validation CLI 返回 success=false")
        self._check_phase_consistency(phase, "正常阶段")
        self._check_count_in_expected_range(phase, "正常阶段")
        vrc = int(phase.get("validation_result_count", 0))

        risk = phase.get("risk_level_counts", {})
        # 先检查各个非零风险级别 — 每个都独立抛出
        if int(risk.get("MEDIUM", 0)) != 0:
            raise ManualAcceptanceError(f"正常阶段: MEDIUM 必须为 0, 实际 {risk.get('MEDIUM')}")
        if int(risk.get("HIGH", 0)) != 0:
            raise ManualAcceptanceError(f"正常阶段: HIGH 必须为 0, 实际 {risk.get('HIGH')}")
        if int(risk.get("CRITICAL", 0)) != 0:
            raise ManualAcceptanceError(f"正常阶段: CRITICAL 必须为 0, 实际 {risk.get('CRITICAL')}")
        if int(risk.get("LOW", 0)) != vrc:
            raise ManualAcceptanceError(
                f"正常阶段: LOW 数量({risk.get('LOW')}) != validation_result_count({vrc})"
            )

        status = phase.get("validation_status_counts", {})
        # 先检查各个非 VALIDATED 状态 — 每个都独立抛出
        if int(status.get("NO_BASELINE", 0)) != 0:
            raise ManualAcceptanceError(f"正常阶段: NO_BASELINE 必须为 0, 实际 {status.get('NO_BASELINE')}")
        if int(status.get("UNRELIABLE_BASELINE", 0)) != 0:
            raise ManualAcceptanceError(
                f"正常阶段: UNRELIABLE_BASELINE 必须为 0, 实际 {status.get('UNRELIABLE_BASELINE')}"
            )
        if int(status.get("ERROR", 0)) != 0:
            raise ManualAcceptanceError(f"正常阶段: ERROR 必须为 0, 实际 {status.get('ERROR')}")
        if int(status.get("VALIDATED", 0)) != vrc:
            raise ManualAcceptanceError(
                f"正常阶段: VALIDATED({status.get('VALIDATED')}) != validation_result_count({vrc})"
            )

    def _validate_combo_phase(self, combo: dict[str, Any], normal: dict[str, Any]) -> None:
        if not combo["validation_success"]:
            raise ManualAcceptanceError("组合异常阶段: Validation CLI 返回 success=false")
        self._check_phase_consistency(combo, "组合异常阶段")
        self._check_count_in_expected_range(combo, "组合异常阶段")
        vrc = int(combo.get("validation_result_count", 0))

        risk = combo.get("risk_level_counts", {})
        high_critical = int(risk.get("HIGH", 0)) + int(risk.get("CRITICAL", 0))
        if high_critical < 1:
            raise ManualAcceptanceError(
                f"组合异常阶段: HIGH + CRITICAL 必须 >= 1, 实际 risk={risk}"
            )
        if int(risk.get("CRITICAL", 0)) <= 0:
            raise ManualAcceptanceError(
                f"组合异常阶段: CRITICAL 必须 > 0, 实际 CRITICAL={risk.get('CRITICAL')}"
            )
        if combo["average_score"] <= normal["average_score"]:
            raise ManualAcceptanceError(
                f"组合异常 avg({combo['average_score']}) 未高于正常 avg({normal['average_score']})"
            )

        status = combo.get("validation_status_counts", {})
        # 先检查各个非 VALIDATED 状态 — 每个都独立抛出
        if int(status.get("NO_BASELINE", 0)) != 0:
            raise ManualAcceptanceError(
                f"组合异常阶段: NO_BASELINE 必须为 0, 实际 {status.get('NO_BASELINE')}"
            )
        if int(status.get("UNRELIABLE_BASELINE", 0)) != 0:
            raise ManualAcceptanceError(
                f"组合异常阶段: UNRELIABLE_BASELINE 必须为 0, 实际 {status.get('UNRELIABLE_BASELINE')}"
            )
        if int(status.get("ERROR", 0)) != 0:
            raise ManualAcceptanceError(f"组合异常阶段: ERROR 必须为 0, 实际 {status.get('ERROR')}")
        if int(status.get("VALIDATED", 0)) != vrc:
            raise ManualAcceptanceError(
                f"组合异常阶段: VALIDATED({status.get('VALIDATED')}) != validation_result_count({vrc})"
            )

    def _validate_idempotency_phase(self, phase: dict[str, Any], run_id: str) -> None:
        if not phase["validation_success"]:
            raise ManualAcceptanceError("幂等阶段: Validation CLI 返回 success=false")
        if phase["written_count"] != 0:
            raise ManualAcceptanceError(
                f"幂等阶段: written_count 必须为 0, 实际 {phase['written_count']}"
            )
        if phase["processed_count"] != 0:
            raise ManualAcceptanceError(
                f"幂等阶段: processed_count 必须为 0, 实际 {phase['processed_count']}"
            )
        if phase["scored_count"] != 0:
            raise ManualAcceptanceError(
                f"幂等阶段: scored_count 必须为 0, 实际 {phase['scored_count']}"
            )
        if phase["generated_log_count"] <= 0:
            raise ManualAcceptanceError("幂等阶段: generated_log_count 必须 > 0")
        vrc = int(phase.get("validation_result_count", 0))
        if vrc != 0:
            raise ManualAcceptanceError(
                f"幂等阶段: validation_result_count 必须为 0, 实际 {vrc}"
            )
        db_count = self._validation_result_total(run_id)
        if db_count != 0:
            raise ManualAcceptanceError(
                f"幂等阶段: run_id {run_id} 在 DB 中有 {db_count} 条结果, 期望 0"
            )

    # ------------------------------------------------------------------
    # 阶段执行
    # ------------------------------------------------------------------

    def _execute_phase(self, run_id: str, window: _Window) -> dict[str, Any]:
        """执行一轮 Validation CLI 调用并返回受控阶段字典。"""
        command = [
            sys.executable,
            str(PROJECT_ROOT / "scripts" / "run_ueba_validation.py"),
            "--start-time", window.start_str,
            "--end-time", window.end_str,
            "--log-type", self.config.log_type,
            "--model-version", self.model_version,
            "--validation-run-id", run_id,
            "--write",
            "--host", self.config.clickhouse_host,
            "--port", str(self.config.clickhouse_port),
            "--username", self.config.clickhouse_user,
            "--password", self.config.clickhouse_password,
            "--database", self.database,
        ]

        phase: dict[str, Any] = {
            "success": False,
            "validation_run_id": run_id,
            "generated_log_count": self._continuous_log_count(window),
            "validation_success": False,
            "processed_count": 0,
            "scored_count": 0,
            "written_count": 0,
            "validation_result_count": 0,
            "risk_level_counts": {},
            "validation_status_counts": {},
            "average_score": 0.0,
            "error": None,
        }

        try:
            proc = subprocess.run(command, cwd=str(PROJECT_ROOT), capture_output=True, text=True, timeout=120)
        except subprocess.TimeoutExpired:
            phase["error"] = "Validation CLI 超时 (120s)"
            return phase
        except Exception as exc:
            phase["error"] = f"subprocess 异常: {type(exc).__name__}: {exc}"
            return phase

        if proc.returncode != 0:
            phase["error"] = f"CLI exit {proc.returncode}: {proc.stderr[:300]}"
            return phase

        try:
            payload = json.loads(proc.stdout)
        except json.JSONDecodeError as exc:
            phase["error"] = f"CLI stdout 非有效 JSON: {exc}"
            return phase

        phase["validation_success"] = bool(payload.get("success"))
        phase["processed_count"] = int(payload.get("processed_count", 0))
        phase["scored_count"] = int(payload.get("scored_count", 0))
        phase["written_count"] = int(payload.get("written_count", 0))
        phase["error"] = payload.get("error")

        # 从 ueba_validation_results 直接聚合
        agg = self._query_validation_aggregate(run_id, window)
        phase["validation_result_count"] = agg["total"]
        phase["risk_level_counts"] = agg["risk_level_counts"]
        phase["validation_status_counts"] = agg["validation_status_counts"]
        phase["average_score"] = agg["average_score"]
        phase["success"] = True
        return phase

    def _query_validation_aggregate(self, run_id: str, window: _Window) -> dict[str, Any]:
        """从 ueba_validation_results 读取完整聚合统计。"""
        sql = f"""
        SELECT
            count() AS total,
            avgOrNull(ueba_score) AS avg_score,
            countIf(ueba_risk_level = 'LOW') AS risk_low,
            countIf(ueba_risk_level = 'MEDIUM') AS risk_medium,
            countIf(ueba_risk_level = 'HIGH') AS risk_high,
            countIf(ueba_risk_level = 'CRITICAL') AS risk_critical,
            countIf(validation_status = 'VALIDATED') AS status_validated,
            countIf(validation_status = 'NO_BASELINE') AS status_no_baseline,
            countIf(validation_status = 'UNRELIABLE_BASELINE') AS status_unreliable,
            countIf(validation_status = 'ERROR') AS status_error
        FROM {self.database}.ueba_validation_results
        WHERE username = %(username)s
          AND validation_run_id = %(run_id)s
          AND baseline_model_version = %(mv)s
          AND log_type = %(log_type)s
          AND timestamp >= %(start_time)s
          AND timestamp < %(end_time)s
        """
        rows = _named_query(
            self.client, sql,
            {
                "username": USERNAME, "run_id": run_id, "mv": self.model_version,
                "log_type": self.config.log_type,
                "start_time": window.start_str, "end_time": window.end_str,
            },
        )
        if not rows:
            return {"total": 0, "average_score": 0.0, "risk_level_counts": {}, "validation_status_counts": {}}
        r = rows[0]
        return {
            "total": int(r.get("total", 0) or 0),
            "average_score": round(float(r.get("avg_score", 0) or 0), 2),
            "risk_level_counts": {
                "LOW": int(r.get("risk_low", 0) or 0),
                "MEDIUM": int(r.get("risk_medium", 0) or 0),
                "HIGH": int(r.get("risk_high", 0) or 0),
                "CRITICAL": int(r.get("risk_critical", 0) or 0),
            },
            "validation_status_counts": {
                "VALIDATED": int(r.get("status_validated", 0) or 0),
                "NO_BASELINE": int(r.get("status_no_baseline", 0) or 0),
                "UNRELIABLE_BASELINE": int(r.get("status_unreliable", 0) or 0),
                "ERROR": int(r.get("status_error", 0) or 0),
            },
        }

    def _validation_result_total(self, run_id: str) -> int:
        """查询某个 validation_run_id 在 DB 中的结果总数。"""
        sql = f"""
        SELECT count() AS cnt
        FROM {self.database}.ueba_validation_results
        WHERE username = %(username)s
          AND validation_run_id = %(run_id)s
          AND baseline_model_version = %(mv)s
          AND log_type = %(log_type)s
        """
        return _scalar_query(
            self.client, sql,
            {"username": USERNAME, "run_id": run_id, "mv": self.model_version, "log_type": self.config.log_type},
        )

    # ------------------------------------------------------------------
    # 窗口关闭等待
    # ------------------------------------------------------------------

    def _wait_until_window_closed(self, window: _Window) -> None:
        """等待 window.effective_end 真正到达。

        generator 必须已经处于 pause 状态。
        最多重试 3 次，防止系统时钟回退或 sleep 提前返回导致死循环。
        所有时间比较使用无标注 naive datetime。
        """
        for _ in range(3):
            remaining = (window.effective_end - _utc_now_naive()).total_seconds()
            if remaining <= 0:
                return
            self.sleep(remaining)
        raise ManualAcceptanceError("等待动态窗口关闭失败")

    # ------------------------------------------------------------------
    # 窗口隔离
    # ------------------------------------------------------------------

    def _check_window_isolation(self, window: _Window) -> None:
        """确保动态窗口内只有本轮验收目标日志。"""
        total = _scalar_query(
            self.client,
            f"SELECT count() AS cnt FROM {self.database}.logs_structured "
            f"WHERE log_type = %(lt)s AND timestamp >= %(s)s AND timestamp < %(e)s",
            {"lt": self.config.log_type, "s": window.start_str, "e": window.end_str},
        )
        fixture = _scalar_query(
            self.client,
            f"SELECT count() AS cnt FROM {self.database}.logs_structured "
            f"WHERE log_type = %(lt)s AND username = %(u)s AND {CONTINUOUS_MARKER_CONDITION} "
            f"AND timestamp >= %(s)s AND timestamp < %(e)s",
            {"lt": self.config.log_type, "u": USERNAME, "s": window.start_str, "e": window.end_str},
        )
        if total != fixture:
            raise ManualAcceptanceError(
                f"窗口隔离失败: total={total} fixture={fixture} — 窗口内存在非 fixture 日志，拒绝调用 Validation CLI"
            )

    # ------------------------------------------------------------------
    # baseline
    # ------------------------------------------------------------------

    def _check_baseline_user(self) -> None:
        cnt = _scalar_query(
            self.client,
            f"SELECT count() AS cnt FROM {self.database}.user_behavior_baselines WHERE username = %(username)s",
            {"username": USERNAME},
        )
        if cnt <= 0:
            raise ManualAcceptanceError(f"未找到 baseline: username={USERNAME!r}")

    def _capture_baseline_before(self) -> None:
        import hashlib
        self.baseline_count_before = _scalar_query(
            self.client,
            f"SELECT count() AS cnt FROM {self.database}.user_behavior_baselines WHERE username = %(u)s",
            {"u": USERNAME},
        )
        rows = _named_query(
            self.client,
            f"SELECT baseline_json FROM {self.database}.user_behavior_baselines FINAL "
            f"WHERE username = %(u)s AND model_version = %(mv)s ORDER BY created_at DESC LIMIT 1",
            {"u": USERNAME, "mv": self.model_version},
        )
        if rows:
            payload = rows[0].get("baseline_json", "")
            self.baseline_hash_before = hashlib.sha256((payload or "").encode("utf-8")).hexdigest()

    def _verify_baseline_unchanged(self) -> None:
        import hashlib
        count_after = _scalar_query(
            self.client,
            f"SELECT count() AS cnt FROM {self.database}.user_behavior_baselines WHERE username = %(u)s",
            {"u": USERNAME},
        )
        self.report["baseline_count_unchanged"] = count_after == self.baseline_count_before
        rows = _named_query(
            self.client,
            f"SELECT baseline_json FROM {self.database}.user_behavior_baselines FINAL "
            f"WHERE username = %(u)s AND model_version = %(mv)s ORDER BY created_at DESC LIMIT 1",
            {"u": USERNAME, "mv": self.model_version},
        )
        hash_after = ""
        if rows:
            payload = rows[0].get("baseline_json", "")
            hash_after = hashlib.sha256((payload or "").encode("utf-8")).hexdigest()
        self.report["baseline_unchanged"] = hash_after == self.baseline_hash_before

    # ------------------------------------------------------------------
    # 查询辅助
    # ------------------------------------------------------------------

    def _continuous_log_count(self, window: _Window) -> int:
        return _scalar_query(
            self.client,
            f"SELECT count() AS cnt FROM {self.database}.logs_structured "
            f"WHERE username = %(u)s AND {CONTINUOUS_MARKER_CONDITION} "
            f"AND timestamp >= %(s)s AND timestamp < %(e)s",
            {"u": USERNAME, "s": window.start_str, "e": window.end_str},
        )

    def _collect_counts(self) -> dict[str, int]:
        return {
            "continuous_logs": _scalar_query(
                self.client,
                f"SELECT count() AS cnt FROM {self.database}.logs_structured "
                f"WHERE username = %(u)s AND {CONTINUOUS_MARKER_CONDITION}",
                {"u": USERNAME},
            ),
            "baselines": _scalar_query(
                self.client,
                f"SELECT count() AS cnt FROM {self.database}.user_behavior_baselines WHERE username = %(u)s",
                {"u": USERNAME},
            ),
            "training_logs": _scalar_query(
                self.client,
                f"SELECT count() AS cnt FROM {self.database}.ueba_baseline_training_logs", {},
            ),
            "validation_results": _scalar_query(
                self.client,
                f"SELECT count() AS cnt FROM {self.database}.ueba_validation_results WHERE username = %(u)s",
                {"u": USERNAME},
            ),
        }

    # ------------------------------------------------------------------
    # 清理
    # ------------------------------------------------------------------

    def _cleanup_continuous_logs(self, username: str, start_time: datetime, end_time: datetime) -> None:
        """清理持续日志并验证无残留。"""
        self.client.command(
            f"ALTER TABLE {self.database}.logs_structured DELETE WHERE username = %(username)s "
            f"AND {CONTINUOUS_MARKER_CONDITION} "
            f"AND timestamp >= %(start_time)s AND timestamp < %(end_time)s "
            f"SETTINGS mutations_sync = 1",
            parameters={"username": username, "start_time": _naive_str(start_time), "end_time": _naive_str(end_time)},
        )
        # 验证残留
        remaining = _scalar_query(
            self.client,
            f"SELECT count() AS cnt FROM {self.database}.logs_structured "
            f"WHERE username = %(u)s AND {CONTINUOUS_MARKER_CONDITION} "
            f"AND timestamp >= %(s)s AND timestamp < %(e)s",
            {"u": username, "s": _naive_str(start_time), "e": _naive_str(end_time)},
        )
        if remaining > 0:
            raise ManualAcceptanceError(f"持续日志清理后仍有 {remaining} 行残留")
        self.cleanup_performed = True

    def _run_cleanup(self) -> None:
        """执行 validation cleanup + 持续日志 cleanup，失败会令报告失败。"""
        # validation cleanup
        for run_id in self.validation_run_ids:
            window = self.validation_windows.get(run_id)
            if window is None:
                continue
            result = cleanup_validation_results(
                self.client, self.config, run_id,
                start_time=window.start_str, end_time=window.end_str,
                username=USERNAME,
            )
            if not result.get("success"):
                self.report["success"] = False
                self.report["errors"].append(
                    f"validation cleanup 失败 run_id={run_id}: {result.get('error', 'unknown')}"
                )

        # 持续日志 cleanup
        earliest = min(w.start for w in self.validation_windows.values()) if self.validation_windows else _utc_now_naive()
        latest = max(w.effective_end for w in self.validation_windows.values()) if self.validation_windows else _utc_now_naive()
        self._cleanup_continuous_logs(USERNAME, earliest, latest)

    def _should_cleanup(self) -> bool:
        if self.report["success"] and self.args.cleanup_after:
            return True
        if (not self.report["success"]) and self.args.cleanup_on_failure and not self.args.keep_data_on_failure:
            return True
        return False

    def _make_generator(self, *, mode: str) -> None:
        factory = self.generator_factory or (lambda **kw: ContinuousLoginGenerator(**kw))
        self.generator = factory(
            writer_callback=self.writer.insert_logs,
            username=USERNAME, mode=mode, logs_per_second=self._logs_per_second,
            tick_seconds=self.tick_seconds,
        )


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="UEBA 持续流量与 Validation 联动验收")
    parser.add_argument("--confirm-write", action="store_true", help="必须提供才能执行真实写库验收")
    parser.add_argument("--confirm-cleanup", action="store_true", help="使用 cleanup 时必须提供")
    parser.add_argument("--cleanup-before", action="store_true", help="运行前清理旧持续 fixture 日志")
    parser.add_argument("--cleanup-after", action="store_true", help="成功后清理本轮数据")
    parser.add_argument("--cleanup-on-failure", action="store_true", help="失败后清理本轮数据（需 --confirm-cleanup）")
    parser.add_argument("--keep-data-on-failure", action="store_true", default=False,
                        help="失败后保留数据用于排查（优先于 --cleanup-on-failure）")
    parser.add_argument("--run-seconds", type=int, default=10, help="每个阶段生成器运行秒数")
    parser.add_argument("--report-path", default=str(DEFAULT_REPORT_PATH), help="项目本地 JSON 报告路径")
    return parser


def validate_cli_gates(args: argparse.Namespace) -> None:
    if not args.confirm_write:
        raise ManualAcceptanceError("缺少 --confirm-write, 拒绝真实写库")
    if (args.cleanup_before or args.cleanup_after or args.cleanup_on_failure) and not args.confirm_cleanup:
        raise ManualAcceptanceError("cleanup 选项需要 --confirm-cleanup")
    if isinstance(args.run_seconds, bool) or args.run_seconds <= 0:
        raise ManualAcceptanceError("--run-seconds 必须为正整数")
    if args.cleanup_on_failure and args.keep_data_on_failure:
        raise ManualAcceptanceError("--cleanup-on-failure 与 --keep-data-on-failure 互斥")


def _empty_report() -> dict[str, Any]:
    report = {
        "success": False, "started_at": "", "finished_at": None,
        "username": USERNAME, "model_version": "",
        "normal_window": {}, "combo_window": {}, "idempotency_window": {},
        "counts_before": {}, "counts_after": {}, "counts_after_cleanup": {},
        "normal_phase": {}, "combo_phase": {}, "idempotency_phase": {},
        "normal_average_score": 0.0, "combo_average_score": 0.0,
        "baseline_unchanged": False, "baseline_count_unchanged": False,
        "generator_stopped": False, "cleanup_performed": False,
        "validation_run_ids": [], "errors": [],
    }
    missing = [f for f in REQUIRED_REPORT_FIELDS if f not in report]
    if missing:
        raise RuntimeError(f"report 缺少必需字段: {missing}")
    return report


def main(argv: list[str] | None = None, **runner_kwargs: Any) -> int:
    parser = build_arg_parser()
    args = parser.parse_args(argv)
    runner = ContinuousValidationRunner(args, **runner_kwargs)
    report = runner.run()
    if report["success"]:
        print("===== CONTINUOUS VALIDATION ACCEPTANCE PASSED =====")
        return 0
    print("===== CONTINUOUS VALIDATION ACCEPTANCE FAILED =====")
    for error in report.get("errors", []):
        print(f"- {error}")
    print(f"Report: {runner.report_path}")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = [
    "ContinuousValidationRunner", "ManualAcceptanceError",
    "build_arg_parser", "main", "validate_cli_gates", "write_report",
]
