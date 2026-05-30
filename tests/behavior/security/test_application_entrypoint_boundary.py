"""正式应用入口隔离门禁。

确保 src/ 和 scripts/ 中的正式运行代码不依赖 tests/ 或 local_only/。

使用 Python AST 而非文本扫描，避免误报注释、文档字符串和普通字符串。
"""

from __future__ import annotations

import ast
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[3]
FORBIDDEN_ROOTS = {"tests", "local_only"}


def _py_files(root: Path):
    """收集目录下所有 .py 文件。"""
    for path in root.rglob("*.py"):
        if path.name == "__init__.py" and not list(path.parent.iterdir()):
            continue  # skip empty init files
        yield path


def _check_file(file_path: Path) -> list[str]:
    """检查单个 Python 文件的 import 语句，返回违规描述列表。"""
    violations = []
    try:
        tree = ast.parse(file_path.read_text(encoding="utf-8"))
    except SyntaxError:
        return violations  # 不是合法 Python（不太可能，但容错）

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                root = alias.name.split(".")[0]
                if root in FORBIDDEN_ROOTS:
                    violations.append(
                        f"{file_path.relative_to(PROJECT_ROOT)}: "
                        f"import {alias.name}"
                    )
        elif isinstance(node, ast.ImportFrom):
            if node.module is None:
                continue
            root = node.module.split(".")[0]
            if root in FORBIDDEN_ROOTS:
                violations.append(
                    f"{file_path.relative_to(PROJECT_ROOT)}: "
                    f"from {node.module} import ..."
                )
    return violations


# ---------------------------------------------------------------------------
# src/ 不依赖 tests/ 或 local_only/
# ---------------------------------------------------------------------------


def test_src_does_not_import_tests_or_local_only():
    """src/ 下所有 .py 文件不得 import tests 或 local_only。"""
    src_dir = PROJECT_ROOT / "src"
    violations = []
    for py_file in _py_files(src_dir):
        violations.extend(_check_file(py_file))

    assert not violations, (
        "src/ 中的正式应用入口不得依赖 tests/ 或 local_only/：\n"
        + "\n".join(violations)
    )


# ---------------------------------------------------------------------------
# scripts/ 不依赖 tests/ 或 local_only/
# ---------------------------------------------------------------------------


def test_scripts_does_not_import_tests_or_local_only():
    """scripts/ 下所有 .py 文件不得 import tests 或 local_only。"""
    scripts_dir = PROJECT_ROOT / "scripts"
    violations = []
    for py_file in _py_files(scripts_dir):
        violations.extend(_check_file(py_file))

    assert not violations, (
        "scripts/ 中的正式应用入口不得依赖 tests/ 或 local_only/：\n"
        + "\n".join(violations)
    )
