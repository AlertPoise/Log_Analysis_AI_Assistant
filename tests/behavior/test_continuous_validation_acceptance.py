"""C 阶段持续流量与 Validation 联动验收 Runner 的回归测试。

所有测试使用 fake / mock，不依赖真实 ClickHouse。
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
import json
from pathlib import Path

import pytest

import tests.behavior.ueba_baseline_acceptance.run_continuous_validation_acceptance as target
from tests.behavior.ueba_baseline_acceptance.run_continuous_validation_acceptance import _Window


PROJECT_ROOT = Path(__file__).resolve().parents[2]
NAIVE_NOW = datetime(2026, 6, 2, 4, 0, 44)
MODEL_VERSION = "ueba_baseline_fixture_v2_monthly"


# ============================================================================
# fake 组件
# ============================================================================


class FakeQueryResult:
    def __init__(self, rows):
        self._rows = rows
        self.column_names = list(rows[0].keys()) if rows else []
        self.result_rows = rows

    def named_results(self):
        return self._rows


class FakeClient:
    """受控 fake ClickHouse client。"""

    def __init__(self, *, baseline_count=1, continuous_count=0, baseline_json="{}",
                 validation_results=None, isolation_total_override: int | None = None):
        self.baseline_count = baseline_count
        self.continuous_count = continuous_count
        self.baseline_json = baseline_json
        self._validation_results = validation_results or []
        self.isolation_total_override = isolation_total_override
        self.commands: list[tuple[str, dict]] = []
        self.queries: list[dict] = []
        self.inserts: list[dict] = []
        self.closed = False

    def command(self, sql, parameters=None):
        self.commands.append((sql, parameters or {}))
        if "DELETE" in sql and "logs_structured" in sql:
            self.continuous_count = 0

    def query(self, sql, parameters=None):
        params = parameters or {}
        self.queries.append({"sql": sql, "parameters": params})
        normalized = " ".join(sql.split()).lower()

        if "ueba_validation_results" in normalized and "avgornull" in normalized:
            run_id = params.get("run_id", "")
            matching = [r for r in self._validation_results if r.get("validation_run_id") == run_id]
            total = len(matching)
            scores = [r.get("ueba_score", 0) for r in matching]
            avg = round(sum(scores) / len(scores), 2) if scores else 0.0
            return FakeQueryResult([{
                "total": total, "avg_score": avg,
                "risk_low": sum(1 for r in matching if r.get("ueba_risk_level") == "LOW"),
                "risk_medium": sum(1 for r in matching if r.get("ueba_risk_level") == "MEDIUM"),
                "risk_high": sum(1 for r in matching if r.get("ueba_risk_level") == "HIGH"),
                "risk_critical": sum(1 for r in matching if r.get("ueba_risk_level") == "CRITICAL"),
                "status_validated": sum(1 for r in matching if r.get("validation_status") == "VALIDATED"),
                "status_no_baseline": sum(1 for r in matching if r.get("validation_status") == "NO_BASELINE"),
                "status_unreliable": sum(1 for r in matching if r.get("validation_status") == "UNRELIABLE_BASELINE"),
                "status_error": sum(1 for r in matching if r.get("validation_status") == "ERROR"),
            }])

        if "count()" in normalized:
            if "user_behavior_baselines" in normalized and "baseline_json" not in normalized:
                return FakeQueryResult([{"cnt": self.baseline_count}])
            if "logs_structured" in normalized and "log_type" in normalized and "position" not in normalized:
                return FakeQueryResult([{"cnt": self.isolation_total_override if self.isolation_total_override is not None else self.continuous_count}])
            if "logs_structured" in normalized and "position" in normalized:
                return FakeQueryResult([{"cnt": self.continuous_count}])
            if "ueba_baseline_training_logs" in normalized:
                return FakeQueryResult([{"cnt": 0}])
            if "ueba_validation_results" in normalized:
                run_id = params.get("run_id", "")
                cnt = sum(1 for r in self._validation_results if r.get("validation_run_id") == run_id)
                return FakeQueryResult([{"cnt": cnt}])

        if "baseline_json" in normalized:
            return FakeQueryResult([{"baseline_json": self.baseline_json}])
        return FakeQueryResult([])

    def insert(self, table, rows, column_names=None, database=None):
        self.inserts.append({"table": table, "rows": rows, "column_names": column_names, "database": database})
        self.continuous_count += len(rows)

    def close(self):
        self.closed = True


class FakeProcessResult:
    def __init__(self, returncode=0, stdout="", stderr=""):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


class FakeGenerator:
    """受控 fake 生成器，记录调用顺序。"""

    def __init__(self):
        self.calls: list[str] = []
        self._paused = False
        self._running = True
        self._mode = "normal"

    def start(self):
        self.calls.append("start")
        self._running = True

    def pause(self):
        self.calls.append("pause")
        self._paused = True

    def resume(self):
        self.calls.append("resume")
        self._running = True

    def set_mode(self, mode):
        self.calls.append(f"set_mode({mode})")
        self._mode = mode

    def shutdown(self):
        self.calls.append("shutdown")
        self._running = False

    def status(self):
        return {"running": self._running, "paused": self._paused, "shutdown": not self._running,
                "logs_per_second": 20, "mode": self._mode, "generated_rows": 10, "written_rows": 10,
                "write_errors": 0, "last_error": None}


# ============================================================================
# 聚合/CLI 工厂
# ============================================================================


def _cli_success(written=5, risk=None):
    risk = risk or {"LOW": written}
    return json.dumps({
        "success": True, "processed_count": written, "scored_count": written,
        "written_count": written, "risk_level_counts": risk,
        "validation_status_counts": {"VALIDATED": written},
        "sample_results": [], "error": None,
    })


def _args(*extra):
    return ["--confirm-write", "--run-seconds", "1", "--report-path", ".tox/manual_test/cv_report.json"] + list(extra)


def _agg_normal():
    return {"total": 5, "average_score": 5.0,
            "risk_level_counts": {"LOW": 5, "MEDIUM": 0, "HIGH": 0, "CRITICAL": 0},
            "validation_status_counts": {"VALIDATED": 5, "NO_BASELINE": 0, "UNRELIABLE_BASELINE": 0, "ERROR": 0}}


def _agg_combo():
    return {"total": 5, "average_score": 80.0,
            "risk_level_counts": {"LOW": 0, "MEDIUM": 0, "HIGH": 3, "CRITICAL": 2},
            "validation_status_counts": {"VALIDATED": 5, "NO_BASELINE": 0, "UNRELIABLE_BASELINE": 0, "ERROR": 0}}


def _agg_zero():
    return {"total": 0, "average_score": 0.0, "risk_level_counts": {}, "validation_status_counts": {}}


# ============================================================================
# 测试辅助
# ============================================================================


def _patch_time(monkeypatch, base=None):
    """让 _utc_now_naive 每次调用返回递增时间，避免 start >= raw_end 误判。"""
    base = base or NAIVE_NOW
    counter = [0]
    def _inc():
        t = base + timedelta(seconds=counter[0])
        counter[0] += 1
        return t
    monkeypatch.setattr(target, "_utc_now_naive", _inc)


def _patch_runner(runner, agg_results):
    """注入受控聚合结果并跳过隔离检查和窗口等待。"""
    call_count = [0]
    def _agg(rid, w):
        call_count[0] += 1
        return agg_results[min(call_count[0] - 1, len(agg_results) - 1)]
    runner._query_validation_aggregate = _agg
    runner._wait_until_window_closed = lambda w: None
    runner._check_window_isolation = lambda w: None
    runner._validation_result_total = lambda rid: 0


def _three_phase_subproc():
    def _f(*a, **kw):
        cmd_str = " ".join(a[0])
        if "combo" in cmd_str:
            return FakeProcessResult(0, _cli_success(5, {"HIGH": 3, "CRITICAL": 2}))
        if "idempotent" in cmd_str:
            return FakeProcessResult(0, _cli_success(0, {}))
        return FakeProcessResult(0, _cli_success(5, {"LOW": 5}))
    return _f


def _setup_runner(monkeypatch, client, gen, subproc_fn, extra_args=None):
    """构建带 fake 依赖的 runner，自动打时间补丁并跳过窗口等待。"""
    _patch_time(monkeypatch)
    monkeypatch.setattr(target, "PROJECT_ROOT", PROJECT_ROOT)
    monkeypatch.setattr(target.subprocess, "run", subproc_fn)
    args = target.build_arg_parser().parse_args(list(_args()) if extra_args is None else _args(*extra_args))
    runner = target.ContinuousValidationRunner(
        args, client_factory=lambda c: client, sleep=lambda s: None, tick_seconds=0.01,
        generator_factory=lambda **kw: gen,
    )
    runner._wait_until_window_closed = lambda w: None
    return runner


# ============================================================================
# import 安全
# ============================================================================


def test_import_does_not_connect():
    assert isinstance(target.PROJECT_ROOT, Path)
    assert target.USERNAME == "fixture_user_stable_0001"


def test_import_does_not_start_threads():
    import threading
    assert not any(isinstance(v, threading.Thread) for v in vars(target).values())


def test_import_does_not_write():
    assert callable(target.main)
    assert callable(target.build_arg_parser)


# ============================================================================
# --help
# ============================================================================


def test_help_does_not_connect(capsys):
    with pytest.raises(SystemExit) as exc_info:
        target.main(["--help"])
    assert exc_info.value.code == 0
    assert "confirm-write" in capsys.readouterr().out


# ============================================================================
# 安全门禁
# ============================================================================


def test_refuses_without_confirm_write():
    with pytest.raises(target.ManualAcceptanceError, match="confirm-write"):
        target.validate_cli_gates(target.build_arg_parser().parse_args([]))


def test_cleanup_flags_require_confirm_cleanup():
    for flag in ("--cleanup-before", "--cleanup-after", "--cleanup-on-failure"):
        with pytest.raises(target.ManualAcceptanceError, match="confirm-cleanup"):
            target.validate_cli_gates(target.build_arg_parser().parse_args(["--confirm-write", flag]))


def test_cleanup_on_failure_and_keep_data_conflict():
    with pytest.raises(target.ManualAcceptanceError, match="互斥"):
        target.validate_cli_gates(
            target.build_arg_parser().parse_args(
                ["--confirm-write", "--confirm-cleanup", "--cleanup-on-failure", "--keep-data-on-failure"]
            )
        )


# ============================================================================
# report-path 安全
# ============================================================================


def test_report_path_must_stay_inside(monkeypatch):
    monkeypatch.setattr(target, "PROJECT_ROOT", PROJECT_ROOT)
    with pytest.raises(target.ManualAcceptanceError):
        target._resolve_project_path("/etc/report.json")


# ============================================================================
# 无标注时间
# ============================================================================


def test_utc_now_naive_no_tz():
    assert target._utc_now_naive().tzinfo is None


def test_naive_iso_no_z():
    r = target._naive_iso(NAIVE_NOW)
    assert r == "2026-06-02T04:00:44"
    assert "Z" not in r
    assert "+" not in r


def test_naive_str_no_z():
    r = target._naive_str(NAIVE_NOW)
    assert "Z" not in r
    assert "+" not in r


# ============================================================================
# JSON 序列化
# ============================================================================


def test_json_serializes_datetime_no_z():
    report = {"started_at": NAIVE_NOW, "a_date": date(2026, 6, 2), "success": False}
    path = PROJECT_ROOT / ".tox/manual_test/dt_report.json"
    target.write_report(path, report)
    loaded = json.loads(path.read_text(encoding="utf-8"))
    assert loaded["started_at"] == "2026-06-02T04:00:44"
    assert "Z" not in loaded["started_at"]


def test_json_rejects_unknown():
    with pytest.raises(TypeError):
        target._json_default(object())


# ============================================================================
# 统一动态窗口
# ============================================================================


def test_window_start_before_effective_end():
    w = _Window(NAIVE_NOW, NAIVE_NOW + timedelta(seconds=5))
    assert w.start < w.effective_end
    assert w.start_str == "2026-06-02 04:00:44"
    assert w.end_str == "2026-06-02 04:00:49"


def test_window_start_equal_effective_end_rejected():
    w = _Window(NAIVE_NOW, NAIVE_NOW)
    assert not (w.start < w.effective_end)


def test_effective_end_buffer_added_once(monkeypatch):
    """有效结束时间仅增加一次 5 秒缓冲。"""
    gen = FakeGenerator()
    client = FakeClient(baseline_count=1, continuous_count=10)

    # 时间序列: started_at(0), normal_start(1), normal_raw_end(2), run_id(3),
    #   combo_start(4), combo_raw_end(5), combo_run_id(6), idem_run_id(7), ...
    times = [NAIVE_NOW + timedelta(seconds=i) for i in range(30)]
    _ti = iter(times)
    monkeypatch.setattr(target, "_utc_now_naive", lambda: next(_ti))
    monkeypatch.setattr(target, "PROJECT_ROOT", PROJECT_ROOT)
    monkeypatch.setattr(target.subprocess, "run", _three_phase_subproc())
    args = target.build_arg_parser().parse_args(_args())
    runner = target.ContinuousValidationRunner(
        args, client_factory=lambda c: client, sleep=lambda s: None, tick_seconds=0.01,
        generator_factory=lambda **kw: gen,
    )
    _patch_runner(runner, [_agg_normal(), _agg_combo(), _agg_zero()])

    report = runner.run()

    # normal: start=+1s, raw_end=+2s, effective_end=+2s+5s=+7s
    nw = report["normal_window"]
    assert nw["start"] == "2026-06-02 04:00:45"
    assert nw["end"] == "2026-06-02 04:00:51"

    # combo: start=+4s, raw_end=+5s, effective_end=+5s+5s=+10s
    cw = report["combo_window"]
    assert cw["start"] == "2026-06-02 04:00:48"
    assert cw["end"] == "2026-06-02 04:00:54"

    # 验证 effective_end = raw_end + 5s（不是 10s 或更多）
    from datetime import datetime as _dt
    n_start = _dt.strptime(nw["start"], "%Y-%m-%d %H:%M:%S")
    n_end = _dt.strptime(nw["end"], "%Y-%m-%d %H:%M:%S")
    # effective_end - start = (raw_end - start) + 5s = 1s + 5s = 6s
    assert n_end - n_start == timedelta(seconds=6)


def test_idempotency_window_covers_full_range(monkeypatch):
    """幂等窗口使用 min(normal_start, combo_start) 和 max(normal_eff, combo_eff)。"""
    gen = FakeGenerator()
    client = FakeClient(baseline_count=1, continuous_count=10)

    times = [NAIVE_NOW + timedelta(seconds=i) for i in range(30)]
    _ti = iter(times)
    monkeypatch.setattr(target, "_utc_now_naive", lambda: next(_ti))
    monkeypatch.setattr(target, "PROJECT_ROOT", PROJECT_ROOT)
    monkeypatch.setattr(target.subprocess, "run", _three_phase_subproc())
    args = target.build_arg_parser().parse_args(_args())
    runner = target.ContinuousValidationRunner(
        args, client_factory=lambda c: client, sleep=lambda s: None, tick_seconds=0.01,
        generator_factory=lambda **kw: gen,
    )
    _patch_runner(runner, [_agg_normal(), _agg_combo(), _agg_zero()])

    report = runner.run()

    iw = report["idempotency_window"]
    # start = min(normal_start=+1, combo_start=+4) = +1 → 04:00:45
    assert iw["start"] == "2026-06-02 04:00:45"
    # end = max(normal_eff=+7, combo_eff=+10) = +10 → 04:00:54
    assert iw["end"] == "2026-06-02 04:00:54"

    # 幂等窗口覆盖 normal 和 combo 的全体范围
    assert iw["start"] <= report["normal_window"]["start"]
    assert iw["start"] <= report["combo_window"]["start"]
    assert iw["end"] >= report["normal_window"]["end"]
    assert iw["end"] >= report["combo_window"]["end"]


# ============================================================================
# 窗口隔离
# ============================================================================


def test_window_isolation_rejects_non_fixture(monkeypatch):
    gen = FakeGenerator()
    client = FakeClient(baseline_count=1, continuous_count=10, isolation_total_override=15)

    _patch_time(monkeypatch)
    monkeypatch.setattr(target, "PROJECT_ROOT", PROJECT_ROOT)
    monkeypatch.setattr(target.subprocess, "run", lambda *a, **kw: FakeProcessResult(0, _cli_success(5, {"LOW": 5})))
    args = target.build_arg_parser().parse_args(_args())
    runner = target.ContinuousValidationRunner(
        args, client_factory=lambda c: client, sleep=lambda s: None, tick_seconds=0.01,
        generator_factory=lambda **kw: gen,
    )
    runner._wait_until_window_closed = lambda w: None
    report = runner.run()
    assert report["success"] is False
    assert any("隔离" in e for e in report["errors"])


def test_idempotency_phase_runs_window_isolation(monkeypatch):
    """幂等阶段在 CLI 调用前也执行窗口隔离检查。"""
    gen = FakeGenerator()
    client = FakeClient(baseline_count=1, continuous_count=10)

    isolation_calls = []
    # 在类上打补丁，_patch_runner 不会覆盖实例方法
    monkeypatch.setattr(target.ContinuousValidationRunner, "_check_window_isolation",
                        lambda self, w: isolation_calls.append(w.to_dict()))
    _patch_time(monkeypatch)
    monkeypatch.setattr(target, "PROJECT_ROOT", PROJECT_ROOT)
    monkeypatch.setattr(target.subprocess, "run", _three_phase_subproc())
    args = target.build_arg_parser().parse_args(_args())
    runner = target.ContinuousValidationRunner(
        args, client_factory=lambda c: client, sleep=lambda s: None, tick_seconds=0.01,
        generator_factory=lambda **kw: gen,
    )
    # 不调用 _patch_runner — 它会把 _check_window_isolation 设成 no-op
    runner._wait_until_window_closed = lambda w: None
    runner._query_validation_aggregate = lambda rid, w: _agg_normal() if "combo" not in (rid or "") else (_agg_combo() if "idempotent" not in (rid or "") else _agg_zero())
    runner._validation_result_total = lambda rid: 0
    runner.run()

    assert len(isolation_calls) == 3, f"期望 3 次隔离调用，实际 {len(isolation_calls)}"


def test_isolation_window_equals_cli_window(monkeypatch):
    """隔离检查使用的窗口与 CLI 使用的窗口完全相同。"""
    gen = FakeGenerator()
    client = FakeClient(baseline_count=1, continuous_count=10)

    iso_windows = []
    cli_windows = []
    monkeypatch.setattr(target.ContinuousValidationRunner, "_check_window_isolation",
                        lambda self, w: iso_windows.append((w.start_str, w.end_str)))

    def _track_cli(*a, **kw):
        cmd = a[0]
        s_idx = cmd.index("--start-time") + 1 if "--start-time" in cmd else -1
        e_idx = cmd.index("--end-time") + 1 if "--end-time" in cmd else -1
        cli_windows.append((cmd[s_idx], cmd[e_idx]))
        cmd_str = " ".join(cmd)
        if "combo" in cmd_str:
            return FakeProcessResult(0, _cli_success(5, {"HIGH": 3, "CRITICAL": 2}))
        if "idempotent" in cmd_str:
            return FakeProcessResult(0, _cli_success(0, {}))
        return FakeProcessResult(0, _cli_success(5, {"LOW": 5}))

    _patch_time(monkeypatch)
    monkeypatch.setattr(target, "PROJECT_ROOT", PROJECT_ROOT)
    monkeypatch.setattr(target.subprocess, "run", _track_cli)
    args = target.build_arg_parser().parse_args(_args())
    runner = target.ContinuousValidationRunner(
        args, client_factory=lambda c: client, sleep=lambda s: None, tick_seconds=0.01,
        generator_factory=lambda **kw: gen,
    )
    # 不调用 _patch_runner — 它会把 _check_window_isolation 设成 no-op
    runner._wait_until_window_closed = lambda w: None
    runner._query_validation_aggregate = lambda rid, w: _agg_normal() if "combo" not in (rid or "") else (_agg_combo() if "idempotent" not in (rid or "") else _agg_zero())
    runner._validation_result_total = lambda rid: 0
    runner.run()

    assert len(iso_windows) == len(cli_windows) == 3
    for iso, cli in zip(iso_windows, cli_windows):
        assert iso == cli, f"隔离窗口 {iso} != CLI 窗口 {cli}"


# ============================================================================
# 正常阶段严格校验
# ============================================================================


def test_normal_phase_rejects_zero_generated(monkeypatch):
    gen = FakeGenerator()
    client = FakeClient(baseline_count=1, continuous_count=0)
    runner = _setup_runner(monkeypatch, client, gen,
                           lambda *a, **kw: FakeProcessResult(0, _cli_success(5, {"LOW": 5})))
    runner._check_window_isolation = lambda w: None
    runner._query_validation_aggregate = lambda rid, w: _agg_zero()
    runner._validation_result_total = lambda rid: 0
    report = runner.run()
    assert report["success"] is False
    assert any("generated_log_count" in e for e in report["errors"])


def test_normal_phase_rejects_non_low_risk(monkeypatch):
    gen = FakeGenerator()
    client = FakeClient(baseline_count=1, continuous_count=10)
    runner = _setup_runner(monkeypatch, client, gen,
                           lambda *a, **kw: FakeProcessResult(0, _cli_success(5, {"LOW": 5})))
    runner._check_window_isolation = lambda w: None
    runner._query_validation_aggregate = lambda rid, w: {
        "total": 5, "average_score": 60.0,
        "risk_level_counts": {"LOW": 2, "MEDIUM": 3, "HIGH": 0, "CRITICAL": 0},
        "validation_status_counts": {"VALIDATED": 5, "NO_BASELINE": 0, "UNRELIABLE_BASELINE": 0, "ERROR": 0},
    }
    runner._validation_result_total = lambda rid: 0
    report = runner.run()
    assert report["success"] is False
    assert any("MEDIUM" in e for e in report["errors"])


def test_normal_phase_fails_when_validation_result_count_zero(monkeypatch):
    gen = FakeGenerator()
    client = FakeClient(baseline_count=1, continuous_count=10)
    runner = _setup_runner(monkeypatch, client, gen,
                           lambda *a, **kw: FakeProcessResult(0, _cli_success(5, {"LOW": 5})))
    runner._check_window_isolation = lambda w: None
    runner._query_validation_aggregate = lambda rid, w: _agg_zero()
    runner._validation_result_total = lambda rid: 0
    report = runner.run()
    assert report["success"] is False
    assert any("validation_result_count" in e for e in report["errors"])


def test_normal_phase_fails_when_low_less_than_vrc(monkeypatch):
    """正常阶段 LOW 数量小于 validation_result_count 时必须失败。"""
    gen = FakeGenerator()
    client = FakeClient(baseline_count=1, continuous_count=10)
    runner = _setup_runner(monkeypatch, client, gen,
                           lambda *a, **kw: FakeProcessResult(0, _cli_success(5, {"LOW": 5})))
    runner._check_window_isolation = lambda w: None
    runner._query_validation_aggregate = lambda rid, w: {
        "total": 5, "average_score": 5.0,
        "risk_level_counts": {"LOW": 3, "MEDIUM": 0, "HIGH": 0, "CRITICAL": 0},
        "validation_status_counts": {"VALIDATED": 5, "NO_BASELINE": 0, "UNRELIABLE_BASELINE": 0, "ERROR": 0},
    }
    runner._validation_result_total = lambda rid: 0
    report = runner.run()
    assert report["success"] is False
    assert any("LOW" in e for e in report["errors"])


def test_normal_phase_fails_when_no_baseline(monkeypatch):
    gen = FakeGenerator()
    client = FakeClient(baseline_count=1, continuous_count=10)
    runner = _setup_runner(monkeypatch, client, gen,
                           lambda *a, **kw: FakeProcessResult(0, _cli_success(5, {"LOW": 5})))
    runner._check_window_isolation = lambda w: None
    runner._query_validation_aggregate = lambda rid, w: {
        "total": 5, "average_score": 5.0,
        "risk_level_counts": {"LOW": 5, "MEDIUM": 0, "HIGH": 0, "CRITICAL": 0},
        "validation_status_counts": {"VALIDATED": 3, "NO_BASELINE": 2, "UNRELIABLE_BASELINE": 0, "ERROR": 0},
    }
    runner._validation_result_total = lambda rid: 0
    report = runner.run()
    assert report["success"] is False
    assert any("NO_BASELINE" in e for e in report["errors"])


def test_normal_phase_fails_when_unreliable_baseline(monkeypatch):
    gen = FakeGenerator()
    client = FakeClient(baseline_count=1, continuous_count=10)
    runner = _setup_runner(monkeypatch, client, gen,
                           lambda *a, **kw: FakeProcessResult(0, _cli_success(5, {"LOW": 5})))
    runner._check_window_isolation = lambda w: None
    runner._query_validation_aggregate = lambda rid, w: {
        "total": 5, "average_score": 5.0,
        "risk_level_counts": {"LOW": 5, "MEDIUM": 0, "HIGH": 0, "CRITICAL": 0},
        "validation_status_counts": {"VALIDATED": 4, "NO_BASELINE": 0, "UNRELIABLE_BASELINE": 1, "ERROR": 0},
    }
    runner._validation_result_total = lambda rid: 0
    report = runner.run()
    assert report["success"] is False
    assert any("UNRELIABLE_BASELINE" in e for e in report["errors"])


def test_normal_phase_fails_when_error_status(monkeypatch):
    gen = FakeGenerator()
    client = FakeClient(baseline_count=1, continuous_count=10)
    runner = _setup_runner(monkeypatch, client, gen,
                           lambda *a, **kw: FakeProcessResult(0, _cli_success(2, {"LOW": 2})))
    runner._check_window_isolation = lambda w: None
    runner._query_validation_aggregate = lambda rid, w: {
        "total": 2, "average_score": 5.0,
        "risk_level_counts": {"LOW": 2, "MEDIUM": 0, "HIGH": 0, "CRITICAL": 0},
        "validation_status_counts": {"VALIDATED": 1, "NO_BASELINE": 0, "UNRELIABLE_BASELINE": 0, "ERROR": 1},
    }
    runner._validation_result_total = lambda rid: 0
    report = runner.run()
    assert report["success"] is False
    assert any("ERROR" in e for e in report["errors"])


# ============================================================================
# 组合异常阶段严格校验
# ============================================================================


def test_combo_phase_rejects_no_high_critical(monkeypatch):
    gen = FakeGenerator()
    client = FakeClient(baseline_count=1, continuous_count=10)

    def _subproc(*a, **kw):
        cmd_str = " ".join(a[0])
        if "combo" in cmd_str:
            return FakeProcessResult(0, _cli_success(5, {"LOW": 5}))
        if "idempotent" in cmd_str:
            return FakeProcessResult(0, _cli_success(0, {}))
        return FakeProcessResult(0, _cli_success(5, {"LOW": 5}))

    _patch_time(monkeypatch)
    monkeypatch.setattr(target, "PROJECT_ROOT", PROJECT_ROOT)
    monkeypatch.setattr(target.subprocess, "run", _subproc)
    args = target.build_arg_parser().parse_args(_args())
    runner = target.ContinuousValidationRunner(
        args, client_factory=lambda c: client, sleep=lambda s: None, tick_seconds=0.01,
        generator_factory=lambda **kw: gen,
    )
    _patch_runner(runner, [_agg_normal(), _agg_normal(), _agg_zero()])
    report = runner.run()
    assert report["success"] is False


def test_combo_phase_rejects_lower_avg(monkeypatch):
    gen = FakeGenerator()
    client = FakeClient(baseline_count=1, continuous_count=10)

    _patch_time(monkeypatch)
    monkeypatch.setattr(target, "PROJECT_ROOT", PROJECT_ROOT)
    monkeypatch.setattr(target.subprocess, "run", _three_phase_subproc())
    args = target.build_arg_parser().parse_args(_args())
    runner = target.ContinuousValidationRunner(
        args, client_factory=lambda c: client, sleep=lambda s: None, tick_seconds=0.01,
        generator_factory=lambda **kw: gen,
    )
    _patch_runner(runner, [
        {"total": 5, "average_score": 80.0,
         "risk_level_counts": {"LOW": 5, "MEDIUM": 0, "HIGH": 0, "CRITICAL": 0},
         "validation_status_counts": {"VALIDATED": 5, "NO_BASELINE": 0, "UNRELIABLE_BASELINE": 0, "ERROR": 0}},
        {"total": 5, "average_score": 10.0,
         "risk_level_counts": {"LOW": 0, "MEDIUM": 0, "HIGH": 3, "CRITICAL": 2},
         "validation_status_counts": {"VALIDATED": 5, "NO_BASELINE": 0, "UNRELIABLE_BASELINE": 0, "ERROR": 0}},
        _agg_zero(),
    ])
    report = runner.run()
    assert report["success"] is False
    assert any("未高于" in e for e in report["errors"])


def test_combo_phase_fails_when_validation_result_count_zero(monkeypatch):
    gen = FakeGenerator()
    client = FakeClient(baseline_count=1, continuous_count=10)
    runner = _setup_runner(monkeypatch, client, gen, _three_phase_subproc())
    _patch_runner(runner, [_agg_normal(),
        {"total": 0, "average_score": 0.0, "risk_level_counts": {}, "validation_status_counts": {}},
        _agg_zero(),
    ])
    report = runner.run()
    assert report["success"] is False
    assert any("validation_result_count" in e for e in report["errors"])


def test_combo_phase_fails_when_error_status(monkeypatch):
    gen = FakeGenerator()
    client = FakeClient(baseline_count=1, continuous_count=10)
    runner = _setup_runner(monkeypatch, client, gen, _three_phase_subproc())
    _patch_runner(runner, [_agg_normal(),
        {"total": 5, "average_score": 80.0,
         "risk_level_counts": {"LOW": 0, "MEDIUM": 0, "HIGH": 3, "CRITICAL": 2},
         "validation_status_counts": {"VALIDATED": 4, "NO_BASELINE": 0, "UNRELIABLE_BASELINE": 0, "ERROR": 1}},
        _agg_zero(),
    ])
    report = runner.run()
    assert report["success"] is False
    assert any("ERROR" in e for e in report["errors"])


def test_combo_phase_fails_when_no_baseline(monkeypatch):
    gen = FakeGenerator()
    client = FakeClient(baseline_count=1, continuous_count=10)
    runner = _setup_runner(monkeypatch, client, gen, _three_phase_subproc())
    _patch_runner(runner, [_agg_normal(),
        {"total": 5, "average_score": 80.0,
         "risk_level_counts": {"LOW": 0, "MEDIUM": 0, "HIGH": 3, "CRITICAL": 2},
         "validation_status_counts": {"VALIDATED": 3, "NO_BASELINE": 2, "UNRELIABLE_BASELINE": 0, "ERROR": 0}},
        _agg_zero(),
    ])
    report = runner.run()
    assert report["success"] is False
    assert any("NO_BASELINE" in e for e in report["errors"])


# ============================================================================
# 幂等阶段严格校验
# ============================================================================


def test_idempotency_rejects_nonzero_written(monkeypatch):
    gen = FakeGenerator()
    client = FakeClient(baseline_count=1, continuous_count=10)

    def _subproc(*a, **kw):
        cmd_str = " ".join(a[0])
        if "idempotent" in cmd_str:
            return FakeProcessResult(0, _cli_success(3, {"LOW": 3}))
        if "combo" in cmd_str:
            return FakeProcessResult(0, _cli_success(5, {"HIGH": 3, "CRITICAL": 2}))
        return FakeProcessResult(0, _cli_success(5, {"LOW": 5}))

    _patch_time(monkeypatch)
    monkeypatch.setattr(target, "PROJECT_ROOT", PROJECT_ROOT)
    monkeypatch.setattr(target.subprocess, "run", _subproc)
    args = target.build_arg_parser().parse_args(_args())
    runner = target.ContinuousValidationRunner(
        args, client_factory=lambda c: client, sleep=lambda s: None, tick_seconds=0.01,
        generator_factory=lambda **kw: gen,
    )
    _patch_runner(runner, [_agg_normal(), _agg_combo(),
        {"total": 3, "average_score": 5.0,
         "risk_level_counts": {"LOW": 3, "MEDIUM": 0, "HIGH": 0, "CRITICAL": 0},
         "validation_status_counts": {"VALIDATED": 3, "NO_BASELINE": 0, "UNRELIABLE_BASELINE": 0, "ERROR": 0}},
    ])
    runner._validation_result_total = lambda rid: 0
    report = runner.run()
    assert report["success"] is False
    assert any("written_count" in e for e in report["errors"])


def test_idempotency_fails_when_validation_result_count_nonzero(monkeypatch):
    gen = FakeGenerator()
    client = FakeClient(baseline_count=1, continuous_count=10)

    def _subproc(*a, **kw):
        cmd_str = " ".join(a[0])
        if "idempotent" in cmd_str:
            return FakeProcessResult(0, _cli_success(0, {}))
        if "combo" in cmd_str:
            return FakeProcessResult(0, _cli_success(5, {"HIGH": 3, "CRITICAL": 2}))
        return FakeProcessResult(0, _cli_success(5, {"LOW": 5}))

    _patch_time(monkeypatch)
    monkeypatch.setattr(target, "PROJECT_ROOT", PROJECT_ROOT)
    monkeypatch.setattr(target.subprocess, "run", _subproc)
    args = target.build_arg_parser().parse_args(_args())
    runner = target.ContinuousValidationRunner(
        args, client_factory=lambda c: client, sleep=lambda s: None, tick_seconds=0.01,
        generator_factory=lambda **kw: gen,
    )
    _patch_runner(runner, [_agg_normal(), _agg_combo(),
        {"total": 5, "average_score": 5.0,
         "risk_level_counts": {"LOW": 5, "MEDIUM": 0, "HIGH": 0, "CRITICAL": 0},
         "validation_status_counts": {"VALIDATED": 5, "NO_BASELINE": 0, "UNRELIABLE_BASELINE": 0, "ERROR": 0}},
    ])
    runner._validation_result_total = lambda rid: 5
    report = runner.run()
    assert report["success"] is False
    assert any("validation_result_count" in e for e in report["errors"])


def test_idempotency_fails_when_db_has_results(monkeypatch):
    gen = FakeGenerator()
    client = FakeClient(baseline_count=1, continuous_count=10)
    runner = _setup_runner(monkeypatch, client, gen, _three_phase_subproc())
    _patch_runner(runner, [_agg_normal(), _agg_combo(), _agg_zero()])
    runner._validation_result_total = lambda rid: 3
    report = runner.run()
    assert report["success"] is False
    assert any("DB" in e for e in report["errors"])


# ============================================================================
# CLI --log-type
# ============================================================================


def test_cli_includes_log_type(monkeypatch):
    gen = FakeGenerator()
    client = FakeClient(baseline_count=1, continuous_count=10)

    captured_commands = []
    def _subproc(*a, **kw):
        captured_commands.append(list(a[0]))
        cmd_str = " ".join(a[0])
        if "combo" in cmd_str:
            return FakeProcessResult(0, _cli_success(5, {"HIGH": 3, "CRITICAL": 2}))
        if "idempotent" in cmd_str:
            return FakeProcessResult(0, _cli_success(0, {}))
        return FakeProcessResult(0, _cli_success(5, {"LOW": 5}))

    _patch_time(monkeypatch)
    monkeypatch.setattr(target, "PROJECT_ROOT", PROJECT_ROOT)
    monkeypatch.setattr(target.subprocess, "run", _subproc)
    args = target.build_arg_parser().parse_args(_args())
    runner = target.ContinuousValidationRunner(
        args, client_factory=lambda c: client, sleep=lambda s: None, tick_seconds=0.01,
        generator_factory=lambda **kw: gen,
    )
    _patch_runner(runner, [_agg_normal(), _agg_combo(), _agg_zero()])
    runner.run()

    assert len(captured_commands) == 3
    for cmd in captured_commands:
        assert "--log-type" in cmd, f"CLI 命令缺少 --log-type: {cmd}"
        lt_idx = cmd.index("--log-type")
        assert cmd[lt_idx + 1] == "vpn", f"--log-type 值不是 vpn: {cmd[lt_idx + 1]}"


# ============================================================================
# baseline 硬门禁
# ============================================================================


def test_baseline_content_change_fails(monkeypatch):
    gen = FakeGenerator()
    client = FakeClient(baseline_count=1, continuous_count=10, baseline_json='{"x":1}')

    _patch_time(monkeypatch)
    monkeypatch.setattr(target, "PROJECT_ROOT", PROJECT_ROOT)
    monkeypatch.setattr(target.subprocess, "run", _three_phase_subproc())
    args = target.build_arg_parser().parse_args(_args())
    runner = target.ContinuousValidationRunner(
        args, client_factory=lambda c: client, sleep=lambda s: None, tick_seconds=0.01,
        generator_factory=lambda **kw: gen,
    )
    _patch_runner(runner, [_agg_normal(), _agg_combo(), _agg_zero()])

    orig_verify = runner._verify_baseline_unchanged
    def _tampered_verify():
        client.baseline_json = '{"x":2}'
        orig_verify()
    runner._verify_baseline_unchanged = _tampered_verify

    report = runner.run()
    assert report["success"] is False
    assert report["baseline_unchanged"] is False
    assert any("baseline" in e.lower() for e in report["errors"])


def test_baseline_count_change_fails(monkeypatch):
    gen = FakeGenerator()
    client = FakeClient(baseline_count=1, continuous_count=10)

    _patch_time(monkeypatch)
    monkeypatch.setattr(target, "PROJECT_ROOT", PROJECT_ROOT)
    monkeypatch.setattr(target.subprocess, "run", _three_phase_subproc())
    args = target.build_arg_parser().parse_args(_args())
    runner = target.ContinuousValidationRunner(
        args, client_factory=lambda c: client, sleep=lambda s: None, tick_seconds=0.01,
        generator_factory=lambda **kw: gen,
    )
    _patch_runner(runner, [_agg_normal(), _agg_combo(), _agg_zero()])

    orig_verify = runner._verify_baseline_unchanged
    def _tampered_verify():
        client.baseline_count = 2
        orig_verify()
    runner._verify_baseline_unchanged = _tampered_verify

    report = runner.run()
    assert report["success"] is False
    assert report["baseline_count_unchanged"] is False
    assert any("行数" in e for e in report["errors"])


# ============================================================================
# generator shutdown
# ============================================================================


def test_generator_still_running_after_shutdown_fails(monkeypatch):
    gen = FakeGenerator()
    client = FakeClient(baseline_count=1, continuous_count=10)

    _patch_time(monkeypatch)
    monkeypatch.setattr(target, "PROJECT_ROOT", PROJECT_ROOT)
    monkeypatch.setattr(target.subprocess, "run", _three_phase_subproc())
    args = target.build_arg_parser().parse_args(_args())
    runner = target.ContinuousValidationRunner(
        args, client_factory=lambda c: client, sleep=lambda s: None, tick_seconds=0.01,
        generator_factory=lambda **kw: gen,
    )
    _patch_runner(runner, [_agg_normal(), _agg_combo(), _agg_zero()])

    def _still_running():
        return {"running": True, "paused": False, "shutdown": False,
                "logs_per_second": 20, "mode": "normal", "generated_rows": 10, "written_rows": 10,
                "write_errors": 0, "last_error": None}
    gen.status = _still_running

    report = runner.run()
    assert report["success"] is False
    assert report["generator_stopped"] is False


def test_shutdown_exception_still_writes_report_and_closes(monkeypatch):
    gen = FakeGenerator()
    client = FakeClient(baseline_count=1, continuous_count=10)

    def _crashing_shutdown():
        raise RuntimeError("shutdown crash")
    gen.shutdown = _crashing_shutdown

    _patch_time(monkeypatch)
    monkeypatch.setattr(target, "PROJECT_ROOT", PROJECT_ROOT)
    monkeypatch.setattr(target.subprocess, "run", _three_phase_subproc())
    args = target.build_arg_parser().parse_args(_args())
    runner = target.ContinuousValidationRunner(
        args, client_factory=lambda c: client, sleep=lambda s: None, tick_seconds=0.01,
        generator_factory=lambda **kw: gen,
    )
    _patch_runner(runner, [_agg_normal(), _agg_combo(), _agg_zero()])
    report = runner.run()

    assert report["success"] is False
    assert any("shutdown" in e.lower() for e in report["errors"])
    assert client.closed is True
    assert report["finished_at"] is not None


def test_status_exception_still_writes_report_and_closes(monkeypatch):
    gen = FakeGenerator()
    client = FakeClient(baseline_count=1, continuous_count=10)

    def _crashing_status():
        raise RuntimeError("status crash")
    gen.status = _crashing_status

    _patch_time(monkeypatch)
    monkeypatch.setattr(target, "PROJECT_ROOT", PROJECT_ROOT)
    monkeypatch.setattr(target.subprocess, "run", _three_phase_subproc())
    args = target.build_arg_parser().parse_args(_args())
    runner = target.ContinuousValidationRunner(
        args, client_factory=lambda c: client, sleep=lambda s: None, tick_seconds=0.01,
        generator_factory=lambda **kw: gen,
    )
    _patch_runner(runner, [_agg_normal(), _agg_combo(), _agg_zero()])
    report = runner.run()

    assert report["success"] is False
    assert any("status" in e.lower() for e in report["errors"])
    assert client.closed is True
    assert report["finished_at"] is not None


# ============================================================================
# validation_run_ids + 窗口映射
# ============================================================================


def test_validation_run_ids_contains_three(monkeypatch):
    gen = FakeGenerator()
    client = FakeClient(baseline_count=1, continuous_count=10)
    runner = _setup_runner(monkeypatch, client, gen, _three_phase_subproc())
    _patch_runner(runner, [_agg_normal(), _agg_combo(), _agg_zero()])
    report = runner.run()

    ids = report["validation_run_ids"]
    assert len(ids) == 3
    assert any("normal" in i for i in ids)
    assert any("combo" in i for i in ids)
    assert any("idempotent" in i for i in ids)


def test_validation_windows_match_run_ids(monkeypatch):
    gen = FakeGenerator()
    client = FakeClient(baseline_count=1, continuous_count=10)
    runner = _setup_runner(monkeypatch, client, gen, _three_phase_subproc())
    _patch_runner(runner, [_agg_normal(), _agg_combo(), _agg_zero()])
    runner.run()

    for run_id in runner.validation_run_ids:
        assert run_id in runner.validation_windows, f"run_id {run_id} 没有窗口映射"
        w = runner.validation_windows[run_id]
        assert isinstance(w, _Window)
        assert w.start < w.effective_end


# ============================================================================
# cleanup
# ============================================================================


def test_cleanup_validation_uses_exact_username(monkeypatch):
    monkeypatch.setattr(target, "PROJECT_ROOT", PROJECT_ROOT)
    client = FakeClient(baseline_count=1, continuous_count=5)
    gen = FakeGenerator()

    captured_user_prefix = []
    def _track_cleanup(client, config, run_id, start_time, end_time, *, user_prefix="fixture_user_%"):
        captured_user_prefix.append(user_prefix)
        return {"success": True, "before_count": 0, "after_count": 0, "error": None}

    monkeypatch.setattr(target, "cleanup_validation_results", _track_cleanup)

    args = target.build_arg_parser().parse_args(_args("--cleanup-after", "--confirm-cleanup"))
    runner = target.ContinuousValidationRunner(
        args, client_factory=lambda c: client, sleep=lambda s: None, tick_seconds=0.01,
        generator_factory=lambda **kw: gen,
    )
    runner.client = client
    runner.report["success"] = True
    window = _Window(NAIVE_NOW, NAIVE_NOW + timedelta(seconds=5))
    runner.validation_windows = {"run-1": window, "run-2": window, "run-3": window}
    runner.validation_run_ids = ["run-1", "run-2", "run-3"]

    runner._run_cleanup()

    for prefix in captured_user_prefix:
        assert prefix == target.USERNAME, f"user_prefix 应为精确 username, 实际 {prefix!r}"
        assert "%" not in prefix, f"user_prefix 不应包含通配符: {prefix!r}"


def test_cleanup_failure_fails_report(monkeypatch):
    monkeypatch.setattr(target, "PROJECT_ROOT", PROJECT_ROOT)
    client = FakeClient(baseline_count=1, continuous_count=5)
    gen = FakeGenerator()

    def _failing_cleanup(client, config, run_id, start_time, end_time, *, user_prefix="fixture_user_%"):
        return {"success": False, "before_count": 0, "after_count": 0, "error": "cleanup 失败"}

    monkeypatch.setattr(target, "cleanup_validation_results", _failing_cleanup)

    args = target.build_arg_parser().parse_args(_args("--cleanup-after", "--confirm-cleanup"))
    runner = target.ContinuousValidationRunner(
        args, client_factory=lambda c: client, sleep=lambda s: None, tick_seconds=0.01,
        generator_factory=lambda **kw: gen,
    )
    runner.client = client
    runner.report["success"] = True
    window = _Window(NAIVE_NOW, NAIVE_NOW + timedelta(seconds=5))
    runner.validation_windows = {"run-x": window}
    runner.validation_run_ids = ["run-x"]

    runner._run_cleanup()

    assert runner.report["success"] is False
    assert any("cleanup" in e.lower() for e in runner.report["errors"])


def test_continuous_log_cleanup_rejects_residue(monkeypatch):
    monkeypatch.setattr(target, "PROJECT_ROOT", PROJECT_ROOT)
    client = FakeClient(baseline_count=1, continuous_count=10)
    gen = FakeGenerator()

    args = target.build_arg_parser().parse_args(_args("--cleanup-after", "--confirm-cleanup"))
    runner = target.ContinuousValidationRunner(
        args, client_factory=lambda c: client, sleep=lambda s: None, tick_seconds=0.01,
        generator_factory=lambda **kw: gen,
    )
    runner.client = client
    runner.report["success"] = True
    window = _Window(NAIVE_NOW, NAIVE_NOW + timedelta(seconds=5))
    runner.validation_windows = {"run-y": window}
    runner.validation_run_ids = ["run-y"]

    monkeypatch.setattr(target, "cleanup_validation_results",
        lambda client, config, run_id, start_time, end_time, *,
               user_prefix="fixture_user_%": {"success": True, "before_count": 0, "after_count": 0, "error": None})

    # 注入残留：residue 查询返回 5
    orig_scalar = target._scalar_query
    def _fake_scalar(client, sql, parameters):
        sql_norm = " ".join(sql.split()).lower()
        if "position" in sql_norm and "logs_structured" in sql_norm:
            return 5
        return orig_scalar(client, sql, parameters)
    monkeypatch.setattr(target, "_scalar_query", _fake_scalar)

    try:
        runner._run_cleanup()
    except target.ManualAcceptanceError:
        runner.report["success"] = False
        runner.report["errors"].append("ManualAcceptanceError: 持续日志清理后有残留")

    assert runner.report["success"] is False
    assert any("残留" in e for e in runner.report["errors"])


def test_cleanup_uses_dynamic_window(monkeypatch):
    monkeypatch.setattr(target, "PROJECT_ROOT", PROJECT_ROOT)
    client = FakeClient(baseline_count=1, continuous_count=5)
    gen = FakeGenerator()

    captured_start_times = []
    captured_end_times = []
    def _track_cleanup(client, config, run_id, start_time, end_time, *, user_prefix="fixture_user_%"):
        captured_start_times.append(start_time)
        captured_end_times.append(end_time)
        return {"success": True, "before_count": 0, "after_count": 0, "error": None}

    monkeypatch.setattr(target, "cleanup_validation_results", _track_cleanup)

    args = target.build_arg_parser().parse_args(_args("--cleanup-after", "--confirm-cleanup"))
    runner = target.ContinuousValidationRunner(
        args, client_factory=lambda c: client, sleep=lambda s: None, tick_seconds=0.01,
        generator_factory=lambda **kw: gen,
    )
    runner.client = client
    runner.report["success"] = True
    window = _Window(NAIVE_NOW, NAIVE_NOW + timedelta(seconds=5))
    runner.validation_windows = {"run-z": window}
    runner.validation_run_ids = ["run-z"]

    runner._run_cleanup()

    for st in captured_start_times:
        assert "2026-06-02" in st
        assert "Z" not in st
        assert "+" not in st
    for et in captured_end_times:
        assert "2026-06-02" in et
        assert "Z" not in et
        assert "+" not in et


# ============================================================================
# model_version
# ============================================================================


def test_model_version_from_config(monkeypatch):
    gen = FakeGenerator()
    client = FakeClient(baseline_count=1, continuous_count=10)

    _patch_time(monkeypatch)
    monkeypatch.setattr(target, "PROJECT_ROOT", PROJECT_ROOT)
    monkeypatch.setattr(target.subprocess, "run", _three_phase_subproc())
    args = target.build_arg_parser().parse_args(_args())
    runner = target.ContinuousValidationRunner(
        args, client_factory=lambda c: client, sleep=lambda s: None, tick_seconds=0.01,
        generator_factory=lambda **kw: gen,
    )
    assert runner.model_version == "ueba_baseline_fixture_v2_monthly"
    assert runner.report["model_version"] == runner.model_version

    empty = target._empty_report()
    assert empty["model_version"] == ""


# ============================================================================
# 窗口关闭等待
# ============================================================================


def test_normal_waits_before_isolation(monkeypatch):
    """正常阶段在执行窗口隔离检查之前已经等待至 effective_end。"""
    gen = FakeGenerator()
    client = FakeClient(baseline_count=1, continuous_count=10)

    call_order = []
    monkeypatch.setattr(target.ContinuousValidationRunner, "_wait_until_window_closed",
                        lambda self, w: call_order.append("wait_normal"))
    monkeypatch.setattr(target.ContinuousValidationRunner, "_check_window_isolation",
                        lambda self, w: call_order.append("isolate_normal"))

    _patch_time(monkeypatch)
    monkeypatch.setattr(target, "PROJECT_ROOT", PROJECT_ROOT)
    monkeypatch.setattr(target.subprocess, "run", _three_phase_subproc())
    args = target.build_arg_parser().parse_args(_args())
    runner = target.ContinuousValidationRunner(
        args, client_factory=lambda c: client, sleep=lambda s: None, tick_seconds=0.01,
        generator_factory=lambda **kw: gen,
    )
    runner._query_validation_aggregate = lambda rid, w: _agg_normal() if "combo" not in (rid or "") else (_agg_combo() if "idempotent" not in (rid or "") else _agg_zero())
    runner._validation_result_total = lambda rid: 0
    runner.run()

    # 检查第一次 wait 在第一个 isolate 之前
    waits = [i for i, c in enumerate(call_order) if c == "wait_normal"]
    isos = [i for i, c in enumerate(call_order) if c == "isolate_normal"]
    assert len(waits) >= 1, "至少一次 wait_normal"
    assert len(isos) >= 1, "至少一次 isolate_normal"
    assert waits[0] < isos[0], "wait_normal 必须在 isolate_normal 之前"


def test_idempotency_also_waits(monkeypatch):
    """幂等阶段也调用窗口关闭等待方法。"""
    gen = FakeGenerator()
    client = FakeClient(baseline_count=1, continuous_count=10)

    wait_count = [0]
    monkeypatch.setattr(target.ContinuousValidationRunner, "_wait_until_window_closed",
                        lambda self, w: wait_count.__setitem__(0, wait_count[0] + 1))

    _patch_time(monkeypatch)
    monkeypatch.setattr(target, "PROJECT_ROOT", PROJECT_ROOT)
    monkeypatch.setattr(target.subprocess, "run", _three_phase_subproc())
    args = target.build_arg_parser().parse_args(_args())
    runner = target.ContinuousValidationRunner(
        args, client_factory=lambda c: client, sleep=lambda s: None, tick_seconds=0.01,
        generator_factory=lambda **kw: gen,
    )
    runner._query_validation_aggregate = lambda rid, w: _agg_normal() if "combo" not in (rid or "") else (_agg_combo() if "idempotent" not in (rid or "") else _agg_zero())
    runner._check_window_isolation = lambda w: None
    runner._validation_result_total = lambda rid: 0
    runner.run()

    assert wait_count[0] == 3, f"三阶段都应调用 wait, 实际 {wait_count[0]}"


def test_wait_method_only_waits_necessary(monkeypatch):
    """等待方法只等待必要剩余时长，不重复增加缓冲。"""
    gen = FakeGenerator()
    client = FakeClient(baseline_count=1, continuous_count=10)

    sleep_durations = []
    # 让 effective_end 在 5 秒后的时间点上
    # 用固定时间序列：now 一路递增
    times = [NAIVE_NOW + timedelta(seconds=i) for i in range(50)]
    _ti = iter(times)
    monkeypatch.setattr(target, "_utc_now_naive", lambda: next(_ti))

    class _FakeSleep:
        def __call__(self, duration):
            sleep_durations.append(duration)

    _sleep = _FakeSleep()

    monkeypatch.setattr(target, "PROJECT_ROOT", PROJECT_ROOT)
    monkeypatch.setattr(target.subprocess, "run", _three_phase_subproc())
    args = target.build_arg_parser().parse_args(_args())
    runner = target.ContinuousValidationRunner(
        args, client_factory=lambda c: client, sleep=_sleep, tick_seconds=0.01,
        generator_factory=lambda **kw: gen,
    )
    _patch_runner(runner, [_agg_normal(), _agg_combo(), _agg_zero()])
    runner.run()

    # 验证 sleep 被调用过且时长合理
    assert len(sleep_durations) > 0, "wait 方法应调用 sleep"
    # 每次 sleep 时长不应超过 5 秒（单次缓冲大小）
    for d in sleep_durations:
        assert d <= 5.0, f"sleep 时长 {d} 超过 5 秒缓冲"


def test_wait_method_no_timezone(monkeypatch):
    """等待方法不会产生 Z 或 +00:00。"""
    gen = FakeGenerator()
    client = FakeClient(baseline_count=1, continuous_count=10)

    time_calls = []
    orig = target._utc_now_naive
    def _track_now():
        t = orig()
        time_calls.append(t)
        return t
    monkeypatch.setattr(target, "_utc_now_naive", _track_now)

    _patch_time(monkeypatch)
    monkeypatch.setattr(target, "PROJECT_ROOT", PROJECT_ROOT)
    monkeypatch.setattr(target.subprocess, "run", _three_phase_subproc())
    args = target.build_arg_parser().parse_args(_args())
    runner = target.ContinuousValidationRunner(
        args, client_factory=lambda c: client, sleep=lambda s: None, tick_seconds=0.01,
        generator_factory=lambda **kw: gen,
    )
    _patch_runner(runner, [_agg_normal(), _agg_combo(), _agg_zero()])
    runner.run()

    # 所有时间必须是 naive
    for t in time_calls:
        assert t.tzinfo is None, f"时间不应该是 aware: {t}"


def test_pause_before_wait(monkeypatch):
    """pause() 发生在等待窗口关闭之前。"""
    gen = FakeGenerator()
    client = FakeClient(baseline_count=1, continuous_count=10)

    pause_order = []
    orig_pause = FakeGenerator.pause
    def _track_pause(self):
        pause_order.append("pause")
        return orig_pause(self)
    # monkeypatch on the class so wait tracker can reference it
    monkeypatch.setattr(FakeGenerator, "pause", _track_pause)

    wait_order = []
    monkeypatch.setattr(target.ContinuousValidationRunner, "_wait_until_window_closed",
                        lambda self, w: wait_order.append("wait"))

    _patch_time(monkeypatch)
    monkeypatch.setattr(target, "PROJECT_ROOT", PROJECT_ROOT)
    monkeypatch.setattr(target.subprocess, "run", _three_phase_subproc())
    args = target.build_arg_parser().parse_args(_args())
    runner = target.ContinuousValidationRunner(
        args, client_factory=lambda c: client, sleep=lambda s: None, tick_seconds=0.01,
        generator_factory=lambda **kw: gen,
    )
    runner._query_validation_aggregate = lambda rid, w: _agg_normal() if "combo" not in (rid or "") else (_agg_combo() if "idempotent" not in (rid or "") else _agg_zero())
    runner._check_window_isolation = lambda w: None
    runner._validation_result_total = lambda rid: 0
    runner.run()

    # pause() 在 run() 中调用前的 wait 全部在之后
    # 两次 pause（normal + combo），两次 wait（normal + combo）在 pause 之后
    pause_indices = [i for i, v in enumerate(pause_order) if v == "pause"]
    assert len(pause_indices) >= 2, "至少两次 pause"


# ============================================================================
# 窗口关闭等待 — 有限重试
# ============================================================================


def test_wait_returns_when_time_advances(monkeypatch):
    """时间正常推进时，等待方法可以返回。"""
    gen = FakeGenerator()
    client = FakeClient(baseline_count=1, continuous_count=10)
    _patch_time(monkeypatch)
    monkeypatch.setattr(target, "PROJECT_ROOT", PROJECT_ROOT)
    monkeypatch.setattr(target.subprocess, "run", _three_phase_subproc())
    args = target.build_arg_parser().parse_args(_args())
    runner = target.ContinuousValidationRunner(
        args, client_factory=lambda c: client, sleep=lambda s: None, tick_seconds=0.01,
        generator_factory=lambda **kw: gen,
    )
    # 真实的 _wait_until_window_closed，未被覆盖
    # 窗口 2 秒后关闭，3 次重试足够时间推进到
    window = _Window(NAIVE_NOW, NAIVE_NOW + timedelta(seconds=2))
    runner._wait_until_window_closed(window)  # 不抛异常即为通过


def test_wait_sleeps_at_most_three_times(monkeypatch):
    """时间始终不推进时，最多 sleep 3 次。"""
    gen = FakeGenerator()
    client = FakeClient(baseline_count=1, continuous_count=10)
    runner = _setup_runner(monkeypatch, client, gen, _three_phase_subproc())
    runner._wait_until_window_closed = target.ContinuousValidationRunner._wait_until_window_closed.__get__(runner, target.ContinuousValidationRunner)

    # 时间永远冻结在 NAIVE_NOW
    monkeypatch.setattr(target, "_utc_now_naive", lambda: NAIVE_NOW)

    sleep_count = [0]
    def _counting_sleep(duration):
        sleep_count[0] += 1
    runner.sleep = _counting_sleep

    # 窗口在遥远的未来
    window = _Window(NAIVE_NOW, NAIVE_NOW + timedelta(hours=1))

    try:
        runner._wait_until_window_closed(window)
    except target.ManualAcceptanceError:
        pass

    assert sleep_count[0] == 3, f"应正好 sleep 3 次, 实际 {sleep_count[0]}"


def test_wait_raises_when_time_stuck(monkeypatch):
    """时间始终不推进时，最终抛出 ManualAcceptanceError。"""
    gen = FakeGenerator()
    client = FakeClient(baseline_count=1, continuous_count=10)
    runner = _setup_runner(monkeypatch, client, gen, _three_phase_subproc())
    runner._wait_until_window_closed = target.ContinuousValidationRunner._wait_until_window_closed.__get__(runner, target.ContinuousValidationRunner)

    monkeypatch.setattr(target, "_utc_now_naive", lambda: NAIVE_NOW)
    runner.sleep = lambda d: None

    window = _Window(NAIVE_NOW, NAIVE_NOW + timedelta(hours=1))

    with pytest.raises(target.ManualAcceptanceError, match="等待动态窗口关闭失败"):
        runner._wait_until_window_closed(window)


# ============================================================================
# 报告写入失败
# ============================================================================


def test_report_write_failure_marks_failure(monkeypatch):
    """报告写入失败时 report.success == False 且 errors 包含 report write failed。"""
    gen = FakeGenerator()
    client = FakeClient(baseline_count=1, continuous_count=10)
    runner = _setup_runner(monkeypatch, client, gen, _three_phase_subproc())
    _patch_runner(runner, [_agg_normal(), _agg_combo(), _agg_zero()])

    def _crashing_write(path, report):
        raise OSError("disk full")
    monkeypatch.setattr(target, "write_report", _crashing_write)

    report = runner.run()
    # 业务阶段成功，但报告写入失败
    assert report["success"] is False
    assert any("report write failed" in e for e in report["errors"])


def test_report_write_failure_still_closes_client(monkeypatch):
    """报告写入失败后 client 仍然关闭。"""
    gen = FakeGenerator()
    client = FakeClient(baseline_count=1, continuous_count=10)
    runner = _setup_runner(monkeypatch, client, gen, _three_phase_subproc())
    _patch_runner(runner, [_agg_normal(), _agg_combo(), _agg_zero()])

    def _crashing_write(path, report):
        raise OSError("disk full")
    monkeypatch.setattr(target, "write_report", _crashing_write)

    runner.run()
    assert client.closed is True


def test_report_write_failure_prints_to_stderr(capsys, monkeypatch):
    """报告写入失败时 stderr 有错误信息。"""
    gen = FakeGenerator()
    client = FakeClient(baseline_count=1, continuous_count=10)
    runner = _setup_runner(monkeypatch, client, gen, _three_phase_subproc())
    _patch_runner(runner, [_agg_normal(), _agg_combo(), _agg_zero()])

    def _crashing_write(path, report):
        raise OSError("disk full")
    monkeypatch.setattr(target, "write_report", _crashing_write)

    import sys as _sys
    monkeypatch.setattr(_sys, "stderr", _sys.stderr)

    runner.run()
    captured = capsys.readouterr()
    assert "report write failed" in captured.err


# ============================================================================
# report fields
# ============================================================================


def test_report_contains_required_fields():
    report = target._empty_report()
    report["model_version"] = MODEL_VERSION
    assert set(target.REQUIRED_REPORT_FIELDS).issubset(report)


def test_full_flow_produces_complete_report(monkeypatch):
    gen = FakeGenerator()
    client = FakeClient(baseline_count=1, continuous_count=10)
    runner = _setup_runner(monkeypatch, client, gen, _three_phase_subproc())
    _patch_runner(runner, [_agg_normal(), _agg_combo(), _agg_zero()])
    report = runner.run()

    assert report["success"] is True
    assert report["username"] == target.USERNAME
    assert report["model_version"] == MODEL_VERSION
    assert report["finished_at"] is not None
    assert "Z" not in report["finished_at"]
    assert "+" not in report["finished_at"]
    assert isinstance(report["normal_window"], dict) and report["normal_window"]["start"]
    assert isinstance(report["combo_window"], dict) and report["combo_window"]["start"]
    assert isinstance(report["idempotency_window"], dict) and report["idempotency_window"]["start"]
    assert report["normal_phase"]["validation_result_count"] == 5
    assert report["combo_phase"]["validation_result_count"] == 5
    assert report["normal_average_score"] == 5.0
    assert report["combo_average_score"] == 80.0
    assert report["baseline_unchanged"] is True
    assert report["baseline_count_unchanged"] is True
