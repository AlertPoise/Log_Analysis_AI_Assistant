"""UEBA dashboard API 提交级安全边界测试。

仅验证 api.py 不写库、不恢复旧接口、import 不连数据库。
完整 mock 单元测试已移入 local_only/tests/behavior/api/。
"""

from __future__ import annotations

import importlib
import sys
from pathlib import Path

API_PATH = Path(__file__).resolve().parents[3] / "src" / "behavior" / "api.py"


def test_import_does_not_import_clickhouse_connect() -> None:
    """import api.py 不应导入 clickhouse_connect（不连接数据库）。"""
    sys.modules.pop("src.behavior.api", None)
    sys.modules.pop("clickhouse_connect", None)

    importlib.import_module("src.behavior.api")

    assert "clickhouse_connect" not in sys.modules


def test_all_only_exports_dashboard_readonly_functions() -> None:
    """__all__ 只暴露 3 个 dashboard 只读函数。"""
    module = importlib.import_module("src.behavior.api")

    assert set(module.__all__) == {
        "get_validation_summary",
        "get_validation_ranking",
        "get_user_validation_detail",
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
