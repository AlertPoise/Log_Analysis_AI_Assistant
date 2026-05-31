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


# ---------------------------------------------------------------------------
# 20-F：Dashboard 管理服务接入静态边界
# ---------------------------------------------------------------------------


def test_dashboard_imports_ueba_management_service() -> None:
    """dashboard 导入 UebaManagementService 并定义 UEBA_MANAGEMENT_AVAILABLE。"""
    dashboard = importlib.import_module("src.visualization.dashboard")
    assert hasattr(dashboard, "UEBA_MANAGEMENT_AVAILABLE")
    from src.behavior.ueba_management_service import UebaManagementService
    assert dashboard.UebaManagementService is UebaManagementService


def test_ueba_forms_have_no_deadlock_disabled_by_confirm() -> None:
    """三个 form_submit_button 不得使用 disabled=not xxx_confirm (表单内 checkbox 死锁)。"""
    source = DASHBOARD_PATH.read_text(encoding="utf-8")
    assert "disabled=not build_confirm" not in source
    assert "disabled=not up_confirm" not in source
    assert "disabled=not val_confirm" not in source


def _get_ueba_management_source() -> str:
    """提取 UEBA 管理区域源码：_ueba_get_management_service → 区域 2.1 之前。"""
    source = DASHBOARD_PATH.read_text(encoding="utf-8")
    start = source.find("def _ueba_get_management_service")
    end = source.find("# --- 区域 2.1：当前准线信息 ---")
    assert start >= 0, "缺少 _ueba_get_management_service"
    assert end > start, "无法定位 UEBA 管理区域结束位置"
    return source[start:end]


def test_ueba_params_button_not_disabled_by_write() -> None:
    """默认参数按钮不得随 write_disabled 一起禁用。"""
    import re
    source = DASHBOARD_PATH.read_text(encoding="utf-8")
    pattern = r'st\.button\(.*查看默认参数.*disabled=write_disabled'
    assert not re.search(pattern, source), "查看默认参数按钮不得使用 disabled=write_disabled"


def test_ueba_management_region_has_no_forbidden_dependencies() -> None:
    """UEBA 管理区域不导入 subprocess/scripts/tests/local_only 等。"""
    ueba_src = _get_ueba_management_source()

    forbidden_terms = [
        "subprocess",
        "scripts/",
        "tests/",
        "local_only/",
        ".tox/",
        "acceptance",
        "fixture",
        "clickhouse_connect.get_client",
        "from scripts",
        "import scripts",
        "from tests",
        "import tests",
        "from local_only",
        "import local_only",
    ]
    violations = [t for t in forbidden_terms if t in ueba_src]
    assert not violations, f"UEBA 管理区域包含禁止依赖: {violations}"


def test_ueba_management_region_has_no_direct_sql() -> None:
    """UEBA 管理区域不得直接拼 SQL。"""
    import re
    ueba_src = _get_ueba_management_source()
    sql_pattern = re.compile(
        r"\b(SELECT|INSERT\s+INTO|UPDATE\s+\w+\s+SET|ALTER\s+TABLE|DELETE\s+FROM|DROP\s+TABLE|TRUNCATE)\b",
        re.IGNORECASE,
    )
    assert not sql_pattern.search(ueba_src), "UEBA 管理区域包含直接拼 SQL"


def test_ueba_management_region_has_no_cli_calls() -> None:
    """UEBA 管理区域不得调用 subprocess / os.system / CLI。"""
    ueba_src = _get_ueba_management_source()
    cli_terms = [
        "subprocess.",
        "os.system(",
        "os.popen(",
        "Popen(",
        "scripts/",
        "build_ueba_baseline.py",
        "run_ueba_validation.py",
    ]
    violations = [t for t in cli_terms if t in ueba_src]
    assert not violations, f"UEBA 管理区域包含 CLI 调用: {violations}"


def test_ueba_management_no_raw_result_exposure() -> None:
    """UEBA 管理区域不使用 st.json(result) / st.write(result) 直接暴露结果。"""
    ueba_src = _get_ueba_management_source()
    assert 'st.json(result)' not in ueba_src
    assert 'st.write(result["details"]' not in ueba_src
    assert 'st.write(result)' not in ueba_src


def test_dashboard_still_has_five_pages() -> None:
    """dashboard 仍然保留 5 个导航页面。"""
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
