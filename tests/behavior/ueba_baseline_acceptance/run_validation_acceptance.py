#!/usr/bin/env python3
"""UEBA validation 端到端验收入口。

仅供验收使用，不是正式应用入口。必须在本地或授权测试 ClickHouse 环境中运行。

正确运行命令：
    PYTHONPATH=$(pwd) .venv/bin/python -m tests.behavior.ueba_baseline_acceptance.run_validation_acceptance

执行流程：
1. 连接 ClickHouse
2. 校验 validation 检测窗口（独立于 May/June baseline 窗口）
3. 隔离 precheck：检测窗口内不得有非 fixture 日志
4. 生成 validation fixture 日志并写入 logs_structured
5. 校验场景用户 baseline 前置条件
6. 安全清理本轮旧 validation 结果
7. 调用正式 scripts/run_ueba_validation.py --write
8. DB validator
9. API validator
10. 输出完整结构化报告
"""

from __future__ import annotations

import sys
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

_PROJECT_ROOT = Path(__file__).resolve().parents[3]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from .config import AcceptanceConfig
from .report_writer import ensure_output_dir, write_json
from .validation_runner import run_validation_acceptance, write_validation_report
from .validation_validator import validate_validation_results, write_validation_db_report
from .validation_api_validator import validate_validation_api, write_validation_api_report
from .validation_cleanup import cleanup_validation_results
from .validation_fixture_generator import (
    generate_validation_fixture_logs,
    validation_fixture_expected_rows,
)

FINAL_REPORT_FILE = "validation_acceptance_report.json"

# 独立 validation 检测窗口（与 May/June baseline 窗口分离）
VALIDATION_START = "2026-07-01 00:00:00"
VALIDATION_END = "2026-07-02 00:00:00"


def _connect_clickhouse(config: AcceptanceConfig) -> Any:
    import clickhouse_connect
    return clickhouse_connect.get_client(
        host=config.clickhouse_host,
        port=config.clickhouse_port,
        username=config.clickhouse_user,
        password=config.clickhouse_password,
        database=config.clickhouse_database,
    )


def _generate_validation_run_id() -> str:
    ts = datetime.now().strftime("%Y%m%d%H%M%S")
    return f"fixture_validation_{ts}"


def _count_non_fixture_logs(client: Any, database: str, start: str, end: str, log_type: str = "vpn") -> int:
    """查询检测窗口内非 fixture 日志数量。"""
    sql = f"""
    SELECT count() AS cnt
    FROM {database}.logs_structured
    WHERE timestamp >= %(start_time)s
        AND timestamp < %(end_time)s
        AND log_type = %(log_type)s
        AND username NOT LIKE %(prefix)s
    """
    params = {"start_time": start, "end_time": end, "log_type": log_type, "prefix": "fixture_user_%"}
    rows = list(client.query(sql, parameters=params).named_results())
    if not rows:
        return 0
    return int(rows[0].get("cnt", 0) or 0)


def _check_baseline_precondition(
    client: Any,
    database: str,
    username: str,
    model_version: str,
    *,
    should_exist: bool = True,
) -> dict[str, Any]:
    """检查用户 baseline 前置条件。"""
    sql = f"""
    SELECT username, is_reliable, sample_count
    FROM {database}.user_behavior_baselines FINAL
    WHERE username = %(username)s
        AND model_version = %(model_version)s
    ORDER BY created_at DESC
    LIMIT 1
    """
    rows = list(client.query(sql, parameters={"username": username, "model_version": model_version}).named_results())
    exists = len(rows) > 0
    reliable = bool(rows[0].get("is_reliable", 0)) if exists else False
    sample_count = int(rows[0].get("sample_count", 0)) if exists else 0
    return {
        "username": username,
        "exists": exists,
        "reliable": reliable,
        "sample_count": sample_count,
        "ok": exists == should_exist and (not should_exist or reliable),
    }


def _insert_fixture_logs(client: Any, database: str, rows: list[dict]) -> int:
    """写入 fixture 日志到 logs_structured（使用 SQL INSERT）。"""
    total = 0
    for row in rows:
        cols = ", ".join(row.keys())
        placeholders = ", ".join(f"%(_{i})s" for i in range(len(row)))
        sql = f"INSERT INTO {database}.logs_structured ({cols}) VALUES ({placeholders})"
        params = {f"_{i}": v for i, v in enumerate(row.values())}
        client.command(sql, parameters=params)
        total += 1
    return total


def _cleanup_validation_fixture_logs(
    client: Any, database: str, start: str, end: str, log_type: str = "vpn"
) -> dict[str, Any]:
    """清理检测窗口中的旧 validation fixture 日志（仅 fixture_user_%）。"""
    count_sql = f"""
    SELECT count() AS cnt FROM {database}.logs_structured
    WHERE username LIKE %(prefix)s
        AND timestamp >= %(start_time)s AND timestamp < %(end_time)s
        AND log_type = %(log_type)s
    """
    params = {"prefix": "fixture_user_%", "start_time": start, "end_time": end, "log_type": log_type}
    before_rows = list(client.query(count_sql, parameters=params).named_results())
    before = int(before_rows[0].get("cnt", 0) or 0) if before_rows else 0

    if before > 0:
        delete_sql = f"""
        ALTER TABLE {database}.logs_structured
        DELETE WHERE username LIKE %(prefix)s
            AND timestamp >= %(start_time)s AND timestamp < %(end_time)s
            AND log_type = %(log_type)s
        SETTINGS mutations_sync = 1
        """
        client.command(delete_sql, parameters=params)

    after_rows = list(client.query(count_sql, parameters=params).named_results())
    after = int(after_rows[0].get("cnt", 0) or 0) if after_rows else 0
    return {"before": before, "after": after, "success": after == 0}


def _write_final_report(config: AcceptanceConfig, report: dict[str, Any]) -> str:
    """写入最终报告（覆盖旧报告），返回报告路径。"""
    from datetime import datetime
    report["attempted_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    report.setdefault("cli_executed", False)
    report.setdefault("written_count", 0)

    output_dir = ensure_output_dir(config)
    report_path = output_dir / FINAL_REPORT_FILE
    try:
        write_json(report_path, report)
        return str(report_path)
    except Exception as exc:
        return f"report_writer 失败: {exc}"


def run_full_validation_acceptance(config: AcceptanceConfig | None = None) -> dict[str, Any]:
    if config is None:
        config = AcceptanceConfig()

    report: dict[str, Any] = {
        "success": False,
        "validation_run_id": None,
        "model_version": config.model_version,
        "validation_start_time": VALIDATION_START,
        "validation_end_time": VALIDATION_END,
        "stage": "init",
        "errors": [],
        "cli_executed": False,
        "written_count": 0,
    }

    def _fail(msg: str) -> str:
        report["errors"].append(msg)
        return _write_final_report(config, report)

    # Step 1: connect
    report["stage"] = "connect"
    try:
        client = _connect_clickhouse(config)
        client.command("SELECT 1")
    except Exception as exc:
        report["report_path"] = _fail(f"ClickHouse 连接失败: {exc}")
        return report

    database = config.clickhouse_database

    # Step 2: isolation precheck
    report["stage"] = "isolation_precheck"
    non_fixture = _count_non_fixture_logs(client, database, VALIDATION_START, VALIDATION_END, config.log_type)
    report["isolation_precheck"] = {"non_fixture_count": non_fixture}
    if non_fixture != 0:
        report["report_path"] = _fail(
            f"检测窗口 [{VALIDATION_START}, {VALIDATION_END}) 存在 {non_fixture} 条非 fixture 日志，拒绝执行 validation"
        )
        return report

    # Step 3: generate + write validation fixture
    report["stage"] = "validation_fixture"
    fixture_id = config.fixture_id + "_validation"
    fixture_rows = generate_validation_fixture_logs(
        fixture_id, seed=config.seed,
        validation_start=VALIDATION_START, validation_end=VALIDATION_END,
    )
    expected = validation_fixture_expected_rows()

    cleanup_log_result = _cleanup_validation_fixture_logs(client, database, VALIDATION_START, VALIDATION_END, config.log_type)
    report["fixture_log_cleanup"] = cleanup_log_result

    inserted = _insert_fixture_logs(client, database, fixture_rows)
    report["fixture_insert"] = {"expected": expected, "inserted": inserted}
    if inserted != expected:
        report["report_path"] = _fail(f"fixture 日志写入不匹配: expected={expected} inserted={inserted}")
        return report

    # Step 4: baseline precondition check
    report["stage"] = "baseline_precheck"
    preconditions = [
        _check_baseline_precondition(client, database, "fixture_user_validation_normal", config.model_version, should_exist=True),
        _check_baseline_precondition(client, database, "fixture_user_validation_combo", config.model_version, should_exist=True),
        _check_baseline_precondition(client, database, "fixture_user_validation_nobase", config.model_version, should_exist=False),
    ]
    report["baseline_preconditions"] = preconditions
    for pc in preconditions:
        if not pc["ok"]:
            report["errors"].append(
                f"baseline 前置条件不满足: {pc['username']} exists={pc['exists']} reliable={pc['reliable']}"
            )
    if any(not pc["ok"] for pc in preconditions):
        report["report_path"] = _write_final_report(config, report)
        return report

    # Step 5: validation
    validation_run_id = _generate_validation_run_id()
    report["validation_run_id"] = validation_run_id

    report["stage"] = "cleanup"
    cleanup_result = cleanup_validation_results(
        client, config, validation_run_id, VALIDATION_START, VALIDATION_END,
        user_prefix="fixture_user_%",
    )
    report["cleanup"] = cleanup_result
    if not cleanup_result["success"]:
        report["report_path"] = _fail(f"清理失败: {cleanup_result.get('error')}")
        return report

    # Step 6: run CLI
    report["stage"] = "validation_cli"
    report["cli_executed"] = True
    cli_result = run_validation_acceptance(config, validation_run_id, VALIDATION_START, VALIDATION_END)
    report["written_count"] = cli_result.get("written_count", 0)
    report["cli"] = {
        "success": cli_result["success"],
        "written_count": cli_result.get("written_count", 0),
        "risk_counts": cli_result.get("risk_counts", {}),
        "status_counts": cli_result.get("status_counts", {}),
        "error": cli_result.get("error"),
    }
    write_validation_report(config, cli_result)
    if not cli_result["success"]:
        report["report_path"] = _fail(f"CLI 失败: {cli_result.get('error')}")
        return report

    # Step 7: DB validator
    report["stage"] = "db_validator"
    db_result = validate_validation_results(
        client, config, validation_run_id, VALIDATION_START, VALIDATION_END,
        expected_written_count=cli_result.get("written_count", 0),
        expected_normal_user="fixture_user_validation_normal",
        expected_high_risk_user="fixture_user_validation_combo",
        expected_no_baseline_user="fixture_user_validation_nobase",
    )
    report["db_validator"] = db_result
    write_validation_db_report(config, db_result)
    if not db_result["success"]:
        report["errors"].extend(db_result.get("errors", []))
        report["report_path"] = _write_final_report(config, report)
        return report

    # Step 8: API validator
    report["stage"] = "api_validator"
    api_result = validate_validation_api(
        client, config, validation_run_id, VALIDATION_START, VALIDATION_END,
        db_distributions=db_result.get("distributions", {}),
        all_result_count=db_result.get("all_result_count", 0),
        expected_high_risk_user="fixture_user_validation_combo",
    )
    report["api_validator"] = api_result
    write_validation_api_report(config, api_result)
    if not api_result["success"]:
        report["errors"].extend(api_result.get("errors", []))
        report["report_path"] = _write_final_report(config, report)
        return report

    # Step 9: done
    report["stage"] = "done"
    report["success"] = len(report["errors"]) == 0
    report["report_path"] = _write_final_report(config, report)
    return report


def main():
    print("=" * 60)
    print(" UEBA Validation 端到端验收")
    print("=" * 60)
    print()
    print("⚠️  本工具仅供验收使用，不是正式应用入口。")
    cfg = AcceptanceConfig()
    print(f"⚠️  Baseline 窗口:  {cfg.start_time} ~ {cfg.end_time}")
    print(f"⚠️  Validation 窗口: {VALIDATION_START} ~ {VALIDATION_END}")
    print(f"⚠️  ClickHouse: {cfg.clickhouse_database}")
    print(f"⚠️  用户前缀:   fixture_user_%")
    print()
    resp = input("确认继续？(yes/no): ").strip().lower()
    if resp != "yes":
        print("已取消。")
        sys.exit(0)

    result = run_full_validation_acceptance(cfg)
    success = result.get("success", False)

    print()
    print("=" * 60)
    print(f" 验收结果: {'PASS' if success else 'FAIL'}")
    print(f" 阶段:             {result.get('stage', '?')}")
    print(f" validation_run_id: {result.get('validation_run_id') or '(未生成)'}")
    print(f" model_version:     {result.get('model_version')}")
    print(f" written_count:     {result.get('written_count', 0)}")
    print(f" cli_executed:      {result.get('cli_executed', False)}")
    if result.get("errors"):
        print(f" 错误: {len(result['errors'])} 条")
        for err in result["errors"][:10]:
            print(f"    - {err}")
    report_path = result.get("report_path")
    if report_path:
        print(f" 报告路径:          {report_path}")
    print("=" * 60)
    sys.exit(0 if success else 1)


if __name__ == "__main__":
    main()
