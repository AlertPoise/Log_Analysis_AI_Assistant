"""UEBA dashboard API 提交级安全边界测试。

仅验证 api.py 不写库、不恢复旧接口、import 不连数据库。
完整 mock 单元测试已移入 local_only/tests/behavior/api/。
"""

from __future__ import annotations

import importlib
import sys
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest

API_PATH = Path(__file__).resolve().parents[3] / "src" / "behavior" / "api.py"


def test_import_does_not_import_clickhouse_connect() -> None:
    """import api.py 不应导入 clickhouse_connect（不连接数据库）。"""
    sys.modules.pop("src.behavior.api", None)
    sys.modules.pop("clickhouse_connect", None)

    importlib.import_module("src.behavior.api")

    assert "clickhouse_connect" not in sys.modules


def test_all_only_exports_dashboard_readonly_functions() -> None:
    """__all__ 只暴露 dashboard 只读函数。"""
    module = importlib.import_module("src.behavior.api")

    assert set(module.__all__) == {
        "get_validation_summary",
        "get_validation_ranking",
        "get_user_validation_detail",
        "get_baseline_summary",
        "get_baseline_default_parameters",
        "get_baseline_detail",
        "get_recent_risk_events",
        "query_validation_events",
    }


def test_source_has_no_write_sql_keywords() -> None:
    """api.py 源码不含写库 SQL 关键字。"""
    source = API_PATH.read_text(encoding="utf-8").upper()

    for keyword in ("INSERT", "UPDATE", "ALTER", "DELETE", "TRUNCATE"):
        assert keyword not in source, f"api.py 包含写库关键字: {keyword}"


def test_source_has_no_old_behavior_demo_interfaces() -> None:
    """api.py 不恢复旧 behavior demo 接口。"""
    source = API_PATH.read_text(encoding="utf-8")

    forbidden = (
        "analyze_behavior_for_frontend",
        "analyze_behavior_from_clickhouse",
        "build_demo_behavior_payload",
        "get_behavior_demo_result",
        "convert_behavior_result_for_dashboard",
        "get_behavior_analysis_for_dashboard",
    )
    for name in forbidden:
        assert name not in source, f"api.py 包含旧接口: {name}"


# ---------------------------------------------------------------------------
# 20-C 补丁：默认 limit 常量匹配冻结方案
# ---------------------------------------------------------------------------


def test_default_limit_constants_match_frozen_plan() -> None:
    """默认 limit 常量必须匹配 20-ueba-dashboard-plan §24。"""
    module = importlib.import_module("src.behavior.api")

    assert module.DEFAULT_RECENT_RISK_LIMIT == 20
    assert module.DEFAULT_RANKING_LIMIT == 20
    assert module.DEFAULT_USER_DETAIL_LIMIT == 50
    assert module.MAX_QUERY_LIMIT == 1000


# ---------------------------------------------------------------------------
# 20-C 补丁：_clamp_limit 安全归一化（含 bool 拒绝）
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "limit_input, default, expected",
    [
        (-1, 20, 20),
        (0, 20, 20),
        (1, 20, 1),
        (20, 20, 20),
        (1000, 20, 1000),
        (1001, 20, 1000),
        (9999, 50, 1000),
        # 非法类型 → 回退到 default
        ("20", 20, 20),
        (None, 20, 20),
        (True, 20, 20),
        (False, 20, 20),
    ],
)
def test_clamp_limit_edge_cases(limit_input: Any, default: int, expected: int) -> None:
    """_clamp_limit 正确处理 bool/字符串/None/负数/零/正常/超上限。"""
    module = importlib.import_module("src.behavior.api")

    result = module._clamp_limit(limit_input, default=default)
    assert result == expected, f"_clamp_limit({limit_input!r}, default={default}) = {result!r}, expected {expected}"


# ---------------------------------------------------------------------------
# 20-C 补丁：异常脱敏
# ---------------------------------------------------------------------------


def test_fail_returns_sanitized_message() -> None:
    """_fail 返回固定脱敏错误文案，不泄露异常详情。"""
    module = importlib.import_module("src.behavior.api")

    result = module._fail("TEST_CODE", {}, ValueError("password=secret db://localhost:8123 /home/admin1/config.json"))

    assert result == {
        "success": False,
        "error": {"code": "TEST_CODE", "message": "UEBA dashboard query failed"},
        "filters": {},
    }

    result_str = str(result)
    assert "password" not in result_str
    assert "secret" not in result_str
    assert "localhost" not in result_str
    assert "admin1" not in result_str
    assert "ValueError" not in result_str
    assert "config.json" not in result_str


def test_get_baseline_default_parameters_error_sanitized() -> None:
    """get_baseline_default_parameters 源码不泄露异常类型。"""
    source = API_PATH.read_text(encoding="utf-8")

    assert 'f"{type(exc).__name__}: {exc}"' not in source
    assert "{type(exc).__name__}" not in source

    module = importlib.import_module("src.behavior.api")
    fake_result = module._fail("CONFIG_ERROR", {}, RuntimeError("should not appear"))
    assert fake_result["error"]["message"] == "UEBA dashboard query failed"
    assert "RuntimeError" not in str(fake_result)
    assert "should not appear" not in str(fake_result)


# ---------------------------------------------------------------------------
# 20-C 补丁：_resolve_validation_context 异常保护
# ---------------------------------------------------------------------------


def test_resolve_validation_context_has_internal_try_except() -> None:
    """_resolve_validation_context 内部必须捕获异常。"""
    source = API_PATH.read_text(encoding="utf-8")

    assert "_error" in source


def test_public_apis_guard_resolve_errors() -> None:
    """所有调用 _resolve_validation_context 的公开 API 必须检查 _error。"""
    source = API_PATH.read_text(encoding="utf-8")

    guard_count = source.count('resolved.get("_error") is not None')
    assert guard_count >= 5, f"至少 5 处 _error guard，实际 {guard_count}"


# ---------------------------------------------------------------------------
# 20-C 补丁：_enrich_events_with_source_logs 异常保护
# ---------------------------------------------------------------------------


def test_enrich_events_calls_are_try_protected() -> None:
    """_enrich_events_with_source_logs 调用必须在 try/except 内。"""
    source = API_PATH.read_text(encoding="utf-8")

    assert "增强字段回查失败" in source


# ---------------------------------------------------------------------------
# 20-C 补丁：摘要和排行使库端聚合
# ---------------------------------------------------------------------------


def test_get_validation_summary_uses_server_side_aggregation() -> None:
    """get_validation_summary 使用 fetch_validation_summary。"""
    source = API_PATH.read_text(encoding="utf-8")
    assert "fetch_validation_summary" in source


def test_get_validation_ranking_uses_server_side_aggregation() -> None:
    """get_validation_ranking 使用 fetch_validation_ranking。"""
    source = API_PATH.read_text(encoding="utf-8")
    assert "fetch_validation_ranking" in source


# ===========================================================================
# 运行时行为测试（fake repository / monkeypatch）
# ===========================================================================


# ---------------------------------------------------------------------------
# 上下文解析失败 → 公开 API 返回脱敏错误
# ---------------------------------------------------------------------------


def _resolve_context_error(*args: Any, **kwargs: Any) -> dict[str, Any]:
    return {
        "model_version": None,
        "validation_run_id": None,
        "resolved_from": "error",
        "_error": {
            "success": False,
            "error": {"code": "UEBA_DASHBOARD_QUERY_ERROR", "message": "UEBA dashboard query failed"},
            "filters": {},
        },
    }


@pytest.mark.parametrize("api_name", [
    "get_validation_summary",
    "get_validation_ranking",
    "get_user_validation_detail",
    "get_recent_risk_events",
    "query_validation_events",
])
def test_apis_return_sanitized_error_on_context_failure(api_name: str) -> None:
    """_resolve_validation_context 失败时，所有 API 返回脱敏错误。"""
    module = importlib.import_module("src.behavior.api")
    api_func = getattr(module, api_name)

    with patch.object(module, "_resolve_validation_context", side_effect=_resolve_context_error):
        kwargs: dict[str, Any] = {
            "start_time": "2020-01-01 00:00:00",
            "end_time": "2030-01-01 00:00:00",
        }
        if api_name == "get_user_validation_detail":
            kwargs["username"] = "testuser"

        result = api_func(**kwargs)

    assert result["success"] is False
    assert result["error"]["code"] == "UEBA_DASHBOARD_QUERY_ERROR"
    assert result["error"]["message"] == "UEBA dashboard query failed"

    result_str = str(result)
    assert "RuntimeError" not in result_str
    assert "password" not in result_str


# ---------------------------------------------------------------------------
# 增强字段回查失败 → 公开 API 返回脱敏错误
# ---------------------------------------------------------------------------


class _FakeRepoEnrichFail:
    """query 成功但 fetch_source_log_details 抛异常。"""

    def query_validation_results(self, **kwargs: Any) -> list[dict[str, Any]]:
        return [{
            "validation_id": "v1",
            "validation_run_id": "run1",
            "source_log_id": 42,
            "timestamp": "2020-01-01",
            "username": "u",
            "log_type": "vpn",
            "source_identity": None,
            "baseline_model_version": "m1",
            "baseline_is_reliable": True,
            "ueba_score": 0,
            "ueba_risk_level": "LOW",
            "validation_status": "VALIDATED",
            "validated_at": "2020-01-01",
            "ueba_anomaly_reasons": [],
            "error": None,
            "request_id": None,
        }]

    def query_validation_events_ordered(self, **kwargs: Any) -> list[dict[str, Any]]:
        return self.query_validation_results(**kwargs)

    def query_recent_risk_events(self, **kwargs: Any) -> list[dict[str, Any]]:
        return self.query_validation_results(**kwargs)

    def fetch_source_log_details(self, source_log_ids: list[int]) -> dict:
        msg = "password=secret db://localhost:8123 /home/admin1/private.json"
        raise RuntimeError(msg)


@pytest.mark.parametrize("api_name", [
    "get_user_validation_detail",
    "get_recent_risk_events",
    "query_validation_events",
])
def test_apis_return_sanitized_error_on_enrich_failure(api_name: str) -> None:
    """增强字段回查失败时 API 返回脱敏错误，不泄露异常文本。"""
    module = importlib.import_module("src.behavior.api")
    api_func = getattr(module, api_name)

    fake_repo = _FakeRepoEnrichFail()

    def fake_resolve(*args: Any, **kwargs: Any) -> dict[str, Any]:
        return {"model_version": "m1", "validation_run_id": "r1", "resolved_from": "explicit"}

    def fake_build_repo(*args: Any, **kwargs: Any) -> _FakeRepoEnrichFail:
        return fake_repo

    with patch.object(module, "_resolve_validation_context", side_effect=fake_resolve):
        with patch.object(module, "_build_repository", side_effect=fake_build_repo):
            kwargs: dict[str, Any] = {
                "start_time": "2020-01-01 00:00:00",
                "end_time": "2030-01-01 00:00:00",
            }
            if api_name == "get_user_validation_detail":
                kwargs["username"] = "testuser"

            result = api_func(**kwargs)

    assert result["success"] is False
    assert result["error"]["code"] == "UEBA_DASHBOARD_QUERY_ERROR"
    assert result["error"]["message"] == "UEBA dashboard query failed"

    result_str = str(result)
    assert "password" not in result_str
    assert "secret" not in result_str
    assert "localhost" not in result_str
    assert "private.json" not in result_str
    assert "RuntimeError" not in result_str


# ---------------------------------------------------------------------------
# 摘要聚合行为测试（fake repo）
# ---------------------------------------------------------------------------


def _make_fake_summary_agg(
    total: int = 10,
    risk_low: int = 5,
    risk_medium: int = 2,
    risk_high: int = 1,
    risk_critical: int = 1,
    status_validated: int = 7,
    status_no_baseline: int = 1,
    status_unreliable: int = 1,
    status_error: int = 0,
    max_score: int = 85,
    avg_score: float = 42.5,
) -> dict[str, Any]:
    return {
        "total": total,
        "risk_low": risk_low,
        "risk_medium": risk_medium,
        "risk_high": risk_high,
        "risk_critical": risk_critical,
        "status_validated": status_validated,
        "status_no_baseline": status_no_baseline,
        "status_unreliable": status_unreliable,
        "status_error": status_error,
        "max_score": max_score,
        "avg_score": avg_score,
        "latest_validated_at": "2020-06-15 12:00:00",
        "latest_validation_run_id": "run-1",
    }


class _FakeRepoSummary:
    def __init__(self, agg: dict[str, Any]) -> None:
        self._agg = agg

    def fetch_validation_summary(self, **kwargs: Any) -> dict[str, Any] | None:
        return self._agg


def test_summary_normal_aggregation() -> None:
    """正常聚合：UNKNOWN 作为残差正确计算。"""
    module = importlib.import_module("src.behavior.api")
    agg = _make_fake_summary_agg(total=5, risk_low=2, risk_medium=1, risk_high=1, risk_critical=0,
                                  status_validated=2, status_no_baseline=1, status_unreliable=0, status_error=0)
    fake_repo = _FakeRepoSummary(agg)

    def fake_resolve(*args: Any, **kwargs: Any) -> dict[str, Any]:
        return {"model_version": "m1", "resolved_from": "explicit"}

    def fake_build(*args: Any, **kwargs: Any) -> _FakeRepoSummary:
        return fake_repo

    with patch.object(module, "_resolve_validation_context", side_effect=fake_resolve):
        with patch.object(module, "_build_repository", side_effect=fake_build):
            result = module.get_validation_summary(
                start_time="2020-01-01 00:00:00",
                end_time="2030-01-01 00:00:00",
            )

    assert result["success"] is True
    s = result["summary"]
    assert s["total"] == 5
    # risk: 2+1+1+0=4 known, unknown=1
    assert s["risk_counts"]["UNKNOWN"] == 1
    assert s["risk_counts"]["LOW"] == 2
    # status: 2+1+0+0=3 known, unknown=2
    assert s["status_counts"]["UNKNOWN"] == 2
    assert s["status_counts"]["VALIDATED"] == 2
    assert s["status_counts"]["NO_BASELINE"] == 1
    # sums match total
    assert sum(s["risk_counts"].values()) == 5
    assert sum(s["status_counts"].values()) == 5


def test_summary_empty_window_no_nan() -> None:
    """空窗口 (total=0, avg_score=NaN) → 返回空摘要，不含 NaN。"""
    module = importlib.import_module("src.behavior.api")
    agg = _make_fake_summary_agg(total=0, risk_low=0, risk_medium=0, risk_high=0, risk_critical=0,
                                  status_validated=0, status_no_baseline=0, status_unreliable=0, status_error=0,
                                  avg_score=float("nan"))
    fake_repo = _FakeRepoSummary(agg)

    def fake_resolve(*args: Any, **kwargs: Any) -> dict[str, Any]:
        return {"model_version": "m1", "resolved_from": "explicit"}

    def fake_build(*args: Any, **kwargs: Any) -> _FakeRepoSummary:
        return fake_repo

    with patch.object(module, "_resolve_validation_context", side_effect=fake_resolve):
        with patch.object(module, "_build_repository", side_effect=fake_build):
            result = module.get_validation_summary(
                start_time="2020-01-01 00:00:00",
                end_time="2030-01-01 00:00:00",
            )

    assert result["success"] is True
    s = result["summary"]
    assert s["total"] == 0
    assert s["max_score"] == 0
    assert s["avg_score"] == 0.0
    assert s["latest_validated_at"] is None
    assert s["latest_validation_run_id"] is None

    result_str = str(result)
    assert "NaN" not in result_str
    assert "nan" not in result_str


def test_summary_missing_total_degrades_to_empty_summary() -> None:
    """缺少 total 字段时按空窗口安全降级，不抛异常。"""
    module = importlib.import_module("src.behavior.api")
    fake_repo = _FakeRepoSummary({})

    def fake_resolve(*args: Any, **kwargs: Any) -> dict[str, Any]:
        return {"model_version": "m1", "resolved_from": "explicit"}

    def fake_build(*args: Any, **kwargs: Any) -> _FakeRepoSummary:
        return fake_repo

    with patch.object(module, "_resolve_validation_context", side_effect=fake_resolve):
        with patch.object(module, "_build_repository", side_effect=fake_build):
            result = module.get_validation_summary(
                start_time="2020-01-01 00:00:00",
                end_time="2030-01-01 00:00:00",
            )

    assert result["success"] is True
    assert result["summary"]["total"] == 0


# ---------------------------------------------------------------------------
# 排行聚合行为测试（fake repo）
# ---------------------------------------------------------------------------


class _FakeRepoRanking:
    def __init__(self, rows: list[dict[str, Any]] | None = None) -> None:
        self._rows = rows or []
        self.last_call_kwargs: dict[str, Any] = {}

    def fetch_validation_ranking(self, **kwargs: Any) -> list[dict[str, Any]]:
        self.last_call_kwargs = kwargs
        return self._rows


def test_ranking_uses_fetch_validation_ranking() -> None:
    """排行调用 fetch_validation_ranking 而非 query_validation_results。"""
    module = importlib.import_module("src.behavior.api")

    fake_repo = _FakeRepoRanking([
        {"username": "alice", "max_score": 80, "avg_score": 50.0, "event_count": 10,
         "high_risk_count": 2, "critical_count": 0, "latest_validated_at": "2020-01-01",
         "latest_validation_run_id": "r1", "overall_risk": "HIGH"},
    ])

    def fake_resolve(*args: Any, **kwargs: Any) -> dict[str, Any]:
        return {"model_version": "m1", "resolved_from": "explicit"}

    def fake_build(*args: Any, **kwargs: Any) -> _FakeRepoRanking:
        return fake_repo

    with patch.object(module, "_resolve_validation_context", side_effect=fake_resolve):
        with patch.object(module, "_build_repository", side_effect=fake_build):
            result = module.get_validation_ranking(
                start_time="2020-01-01 00:00:00",
                end_time="2030-01-01 00:00:00",
            )

    assert result["success"] is True
    assert len(result["ranking"]) == 1
    assert result["ranking"][0]["username"] == "alice"
    assert result["ranking"][0]["max_score"] == 80


def test_ranking_default_limit() -> None:
    """排行默认 limit 为 20（冻结方案）。"""
    module = importlib.import_module("src.behavior.api")

    fake_repo = _FakeRepoRanking([])

    def fake_resolve(*args: Any, **kwargs: Any) -> dict[str, Any]:
        return {"model_version": "m1", "resolved_from": "explicit"}

    def fake_build(*args: Any, **kwargs: Any) -> _FakeRepoRanking:
        return fake_repo

    with patch.object(module, "_resolve_validation_context", side_effect=fake_resolve):
        with patch.object(module, "_build_repository", side_effect=fake_build):
            module.get_validation_ranking(
                start_time="2020-01-01 00:00:00",
                end_time="2030-01-01 00:00:00",
            )

    assert fake_repo.last_call_kwargs.get("limit") == 20


def test_ranking_invalid_limit_falls_back_to_default() -> None:
    """非法 limit → 回退到 20。"""
    module = importlib.import_module("src.behavior.api")

    fake_repo = _FakeRepoRanking([])

    def fake_resolve(*args: Any, **kwargs: Any) -> dict[str, Any]:
        return {"model_version": "m1", "resolved_from": "explicit"}

    def fake_build(*args: Any, **kwargs: Any) -> _FakeRepoRanking:
        return fake_repo

    with patch.object(module, "_resolve_validation_context", side_effect=fake_resolve):
        with patch.object(module, "_build_repository", side_effect=fake_build):
            module.get_validation_ranking(
                start_time="2020-01-01 00:00:00",
                end_time="2030-01-01 00:00:00",
                limit=-1,
            )

    assert fake_repo.last_call_kwargs.get("limit") == 20


def test_ranking_malformed_row_returns_error() -> None:
    """排行映射中畸形行 → 返回脱敏错误。"""
    module = importlib.import_module("src.behavior.api")

    # 缺少 max_score 字段 → int(row.get("max_score", 0)) 仍能工作
    # 但 str(row.get("overall_risk", "LOW")) 也能工作
    # 真正畸形的情况：返回非 dict
    fake_repo = _FakeRepoRanking([None])  # type: ignore[list-item]

    def fake_resolve(*args: Any, **kwargs: Any) -> dict[str, Any]:
        return {"model_version": "m1", "resolved_from": "explicit"}

    def fake_build(*args: Any, **kwargs: Any) -> _FakeRepoRanking:
        return fake_repo

    with patch.object(module, "_resolve_validation_context", side_effect=fake_resolve):
        with patch.object(module, "_build_repository", side_effect=fake_build):
            result = module.get_validation_ranking(
                start_time="2020-01-01 00:00:00",
                end_time="2030-01-01 00:00:00",
            )

    assert result["success"] is False
    assert result["error"]["code"] == "UEBA_DASHBOARD_QUERY_ERROR"


# ---------------------------------------------------------------------------
# 20-C 收口：avg_score 两位小数 + SQL 排序语义
# ---------------------------------------------------------------------------


def test_ranking_avg_score_rounded_two_decimals() -> None:
    """排行 avg_score 保留两位小数。"""
    module = importlib.import_module("src.behavior.api")

    fake_repo = _FakeRepoRanking([
        {"username": "alice", "max_score": 80, "avg_score": 12.34567, "event_count": 3,
         "high_risk_count": 0, "critical_count": 0, "latest_validated_at": "2020-01-01",
         "latest_validation_run_id": "r1", "overall_risk": "LOW"},
    ])

    def fake_resolve(*args: Any, **kwargs: Any) -> dict[str, Any]:
        return {"model_version": "m1", "resolved_from": "explicit"}

    def fake_build(*args: Any, **kwargs: Any) -> _FakeRepoRanking:
        return fake_repo

    with patch.object(module, "_resolve_validation_context", side_effect=fake_resolve):
        with patch.object(module, "_build_repository", side_effect=fake_build):
            result = module.get_validation_ranking(
                start_time="2020-01-01 00:00:00",
                end_time="2030-01-01 00:00:00",
            )

    assert result["ranking"][0]["avg_score"] == 12.35


def test_repository_ranking_sql_has_event_count_in_order() -> None:
    """fetch_validation_ranking SQL 包含 event_count DESC 排序。"""
    from src.behavior.validation_repository import UebaValidationRepository

    class _FakeClientOrderCheck:
        def execute(self, sql: str, parameters: dict | None = None) -> list:
            return []

    captured_sql: list[str] = []

    def _capture_exec(sql: str, parameters: dict[str, Any]) -> list[dict[str, Any]]:
        captured_sql.append(sql)
        return []

    client = _FakeClientOrderCheck()
    repo = UebaValidationRepository(client=client, database="log_analysis")
    repo._execute_query = _capture_exec  # type: ignore[method-assign]

    repo.fetch_validation_ranking(
        start_time="2020-01-01 00:00:00",
        end_time="2030-01-01 00:00:00",
        model_version="m1",
        limit=20,
    )

    order_sql = captured_sql[-1]
    expected = "ORDER BY max_score DESC, critical_count DESC, high_risk_count DESC, event_count DESC, username ASC"
    assert expected in order_sql, f"SQL should contain event_count DESC.\nGot: {order_sql}"
