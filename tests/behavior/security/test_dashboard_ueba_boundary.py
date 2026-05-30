"""Dashboard UEBA 接入提交级安全边界测试。

仅验证 dashboard 不依赖旧接口、页面入口完整、BEHAVIOR_API_AVAILABLE=True。
完整 mock / AST 集成测试已移入 local_only/tests/visualization/dashboard_mock/。
"""

from __future__ import annotations

import importlib
from pathlib import Path

DASHBOARD_PATH = Path(__file__).resolve().parents[3] / "src" / "visualization" / "dashboard.py"


def test_behavior_api_available() -> None:
    """api.py 已创建后 BEHAVIOR_API_AVAILABLE 应为 True。"""
    dashboard = importlib.import_module("src.visualization.dashboard")

    assert getattr(dashboard, "BEHAVIOR_API_AVAILABLE") is True


def test_dashboard_five_pages_still_exist() -> None:
    """5 个 show_* 页面函数均存在且可调用。"""
    dashboard = importlib.import_module("src.visualization.dashboard")

    expected = (
        "show_realtime_logs",
        "show_security_score",
        "show_ai_suggestions",
        "show_history_search",
        "show_ueba_ranking",
    )
    for name in expected:
        assert hasattr(dashboard, name), f"缺少页面函数: {name}"
        assert callable(getattr(dashboard, name))


def test_source_has_no_old_behavior_demo_interfaces() -> None:
    """dashboard.py 不引用旧 behavior demo 接口。"""
    source = DASHBOARD_PATH.read_text(encoding="utf-8")

    forbidden = (
        "analyze_behavior_for_frontend",
        "analyze_behavior_from_clickhouse",
        "build_demo_behavior_payload",
        "get_behavior_demo_result",
        "convert_behavior_result_for_dashboard",
        "get_behavior_analysis_for_dashboard",
    )
    for name in forbidden:
        assert name not in source, f"dashboard.py 包含旧接口: {name}"


def test_source_has_no_old_risk_score_ueba_source() -> None:
    """dashboard.py 不将 logs_structured.risk_score 用作 UEBA 数据源。"""
    source = DASHBOARD_PATH.read_text(encoding="utf-8")

    assert "logs_structured.risk_score" not in source


# ---------------------------------------------------------------------------
# 20-E：UebaManagementService 静态边界
# ---------------------------------------------------------------------------

MANAGEMENT_PATH = Path(__file__).resolve().parents[3] / "src" / "behavior" / "ueba_management_service.py"


def test_management_service_file_exists() -> None:
    """ueba_management_service.py 必须存在。"""
    assert MANAGEMENT_PATH.exists(), "缺少 ueba_management_service.py"


def test_management_service_has_class() -> None:
    """文件必须包含 UebaManagementService 类。"""
    management = importlib.import_module("src.behavior.ueba_management_service")
    assert hasattr(management, "UebaManagementService")


def test_management_service_has_three_operations() -> None:
    """必须包含 build_baseline / update_training_and_rebuild / run_validation。"""
    management = importlib.import_module("src.behavior.ueba_management_service")
    svc = management.UebaManagementService
    for method in ("build_baseline", "update_training_and_rebuild", "run_validation"):
        assert hasattr(svc, method), f"缺少方法: {method}"


def test_management_service_has_write_lock() -> None:
    """必须包含模块级 _WRITE_LOCK。"""
    management = importlib.import_module("src.behavior.ueba_management_service")
    assert hasattr(management, "_WRITE_LOCK")


def test_management_service_source_has_no_forbidden_imports() -> None:
    """源码不应导入 subprocess / scripts / tests / local_only。"""
    source = MANAGEMENT_PATH.read_text(encoding="utf-8")
    for term in ("subprocess", "from scripts", "import scripts", "tests/", "local_only/", ".tox/"):
        assert term not in source, f"ueba_management_service.py 包含禁止导入: {term}"


def test_management_service_source_has_dry_run_false() -> None:
    """源码必须显式包含 dry_run=False。"""
    source = MANAGEMENT_PATH.read_text(encoding="utf-8")
    assert "dry_run=False" in source, "validation 必须显式传 dry_run=False"


def test_management_service_source_has_acquire_blocking_false() -> None:
    """源码必须包含 acquire(blocking=False)。"""
    source = MANAGEMENT_PATH.read_text(encoding="utf-8")
    assert "acquire(blocking=False)" in source


def test_management_service_source_has_finally() -> None:
    """源码必须包含 finally 用于释放锁或关闭 client。"""
    source = MANAGEMENT_PATH.read_text(encoding="utf-8")
    assert "finally" in source


def test_management_service_source_has_close() -> None:
    """源码必须包含 client.close() 调用。"""
    source = MANAGEMENT_PATH.read_text(encoding="utf-8")
    assert ".close()" in source or "close(" in source
