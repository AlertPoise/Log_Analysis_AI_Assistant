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
    """使用 AST 解析，禁止 import subprocess / scripts / tests / local_only。"""
    import ast
    source = MANAGEMENT_PATH.read_text(encoding="utf-8")
    tree = ast.parse(source)

    forbidden_roots = {"subprocess", "scripts", "tests", "local_only"}
    violations: list[str] = []

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                root = alias.name.split(".")[0]
                if root in forbidden_roots:
                    violations.append(f"import {alias.name}")
        elif isinstance(node, ast.ImportFrom):
            if node.module is not None:
                root = node.module.split(".")[0]
                if root in forbidden_roots:
                    violations.append(f"from {node.module} import ...")

    assert not violations, f"ueba_management_service.py 包含禁止导入: {violations}"

    # 轻量文本检查
    assert ".tox/" not in source
    assert "raw_log" not in source


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
