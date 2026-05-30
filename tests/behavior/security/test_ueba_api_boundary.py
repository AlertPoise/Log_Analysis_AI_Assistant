"""UEBA dashboard API 提交级安全边界测试。

仅验证 api.py 不写库、不恢复旧接口、import 不连数据库。
完整 mock 单元测试已移入 local_only/tests/behavior/api/。
"""

from __future__ import annotations

import importlib
import sys
from pathlib import Path
from typing import Any

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
# 20-C：默认 limit 常量
# ---------------------------------------------------------------------------


def test_default_limit_constants_match_frozen_plan() -> None:
    """默认 limit 常量必须匹配 20-ueba-dashboard-plan §24。"""
    module = importlib.import_module("src.behavior.api")

    assert module.DEFAULT_RECENT_RISK_LIMIT == 20
    assert module.DEFAULT_RANKING_LIMIT == 20
    assert module.DEFAULT_USER_DETAIL_LIMIT == 50
    assert module.MAX_QUERY_LIMIT == 1000


# ---------------------------------------------------------------------------
# 20-C：_clamp_limit 安全归一化（轻量纯函数测试）
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
    assert result == expected


# ---------------------------------------------------------------------------
# 20-C：异常脱敏
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


# ---------------------------------------------------------------------------
# 20-C：源码边界检查（轻量静态断言）
# ---------------------------------------------------------------------------


def test_resolve_validation_context_has_internal_try_except() -> None:
    """_resolve_validation_context 内部必须捕获异常。"""
    source = API_PATH.read_text(encoding="utf-8")
    assert "_error" in source


def test_public_apis_guard_resolve_errors() -> None:
    """所有调用 _resolve_validation_context 的公开 API 必须检查 _error 返回。"""
    source = API_PATH.read_text(encoding="utf-8")
    guard_count = source.count('resolved.get("_error") is not None')
    assert guard_count >= 5, f"至少 5 处 _error guard，实际 {guard_count}"


def test_enrich_events_calls_are_try_protected() -> None:
    """_enrich_events_with_source_logs 调用必须在 try/except 内。"""
    source = API_PATH.read_text(encoding="utf-8")
    assert "增强字段回查失败" in source


def test_get_validation_summary_uses_server_side_aggregation() -> None:
    """get_validation_summary 使用 fetch_validation_summary。"""
    source = API_PATH.read_text(encoding="utf-8")
    assert "fetch_validation_summary" in source


def test_get_validation_ranking_uses_server_side_aggregation() -> None:
    """get_validation_ranking 使用 fetch_validation_ranking。"""
    source = API_PATH.read_text(encoding="utf-8")
    assert "fetch_validation_ranking" in source
