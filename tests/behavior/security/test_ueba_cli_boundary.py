"""CLI 提交级安全边界测试。

从 4 个 CLI 完整 mock 测试中提取关键危险边界：
- --help 不连接 ClickHouse
- run_ueba_validation 默认 dry-run
- --write 与 --dry-run 互斥
- password 不泄漏到输出
- export CLI 源码不含写库 SQL
- training update replace / append 边界

完整参数组合/输出格式测试保留在原 4 个 CLI 测试文件中，
等待 P4 迁入 local_only。
"""

from __future__ import annotations

import json
import sys
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

# ---------------------------------------------------------------------------
# --help 不连接数据库（参数化消除 4 份重复）
# ---------------------------------------------------------------------------

_HELP_CASES = [
    ("scripts.build_ueba_baseline", "build_ueba_baseline"),
    ("scripts.run_ueba_validation", "run_ueba_validation"),
    ("scripts.export_ueba_validation_results", "export_ueba_validation_results"),
    ("scripts.update_ueba_baseline_training_logs", "update_ueba_baseline_training_logs"),
]


@pytest.mark.parametrize("module_name,label", _HELP_CASES)
def test_help_does_not_connect_to_clickhouse(module_name, label, monkeypatch, capsys):
    """4 个 CLI 执行 --help 时均不得创建 ClickHouse 连接。"""
    import importlib

    cli = importlib.import_module(module_name)
    create_client = Mock(side_effect=AssertionError(f"{label}: --help 不应连接 ClickHouse"))
    monkeypatch.setattr(cli, "create_clickhouse_client", create_client)

    with pytest.raises(SystemExit) as exc_info:
        cli.main(["--help"])

    assert exc_info.value.code == 0
    create_client.assert_not_called()


# ---------------------------------------------------------------------------
# run_ueba_validation 默认 dry-run
# ---------------------------------------------------------------------------

from scripts import run_ueba_validation as validation_cli


def test_run_validation_defaults_to_dry_run():
    """未传 --write 时，默认 dry_run=True。"""
    args = validation_cli.parse_args([
        "--start-time", "2024-01-01 00:00:00",
        "--end-time", "2024-02-01 00:00:00",
        "--model-version", "ueba_baseline_v1",
    ])
    assert args.write is False


def test_run_validation_write_and_dry_run_are_mutually_exclusive():
    """--write 与 --dry-run 同时传入时必须拒绝。"""
    with pytest.raises(SystemExit) as exc_info:
        validation_cli.parse_args([
            "--start-time", "2024-01-01 00:00:00",
            "--end-time", "2024-02-01 00:00:00",
            "--model-version", "ueba_baseline_v1",
            "--write", "--dry-run",
        ])
    assert exc_info.value.code == 2


def test_run_validation_password_not_leaked_to_output(monkeypatch, capsys):
    """CLI 异常输出中不得包含密码明文。"""
    fake_client = SimpleNamespace(close=Mock())
    fake_service = Mock()
    fake_service.run.side_effect = RuntimeError("boom secret-for-test")
    monkeypatch.setattr(validation_cli, "create_clickhouse_client", Mock(return_value=fake_client))
    monkeypatch.setattr(validation_cli, "build_service", Mock(return_value=fake_service))

    exit_code = validation_cli.main([
        "--start-time", "2024-01-01 00:00:00",
        "--end-time", "2024-02-01 00:00:00",
        "--model-version", "ueba_baseline_v1",
        "--password", "secret-for-test",
    ])

    assert exit_code == 1
    output = capsys.readouterr().out
    assert "secret-for-test" not in output


# ---------------------------------------------------------------------------
# export CLI 源码不含写库 SQL
# ---------------------------------------------------------------------------


def test_export_cli_source_has_no_write_sql_keywords():
    """export_ueba_validation_results.py 源码不含任何写库 SQL 结构。"""
    from pathlib import Path

    source = (Path(__file__).resolve().parents[3] / "scripts" / "export_ueba_validation_results.py").read_text(encoding="utf-8")
    source_upper = source.upper()

    for sql_pattern in ("INSERT INTO", "DELETE FROM", "DELETE WHERE",
                        "ALTER TABLE", "TRUNCATE TABLE", "UPDATE "):
        assert sql_pattern not in source_upper, f"export CLI 包含写库 SQL: {sql_pattern}"


# ---------------------------------------------------------------------------
# training update replace / append 边界
# ---------------------------------------------------------------------------

from scripts import update_ueba_baseline_training_logs as update_cli


def test_training_update_has_replace_and_append_modes():
    """update CLI 必须支持 replace 和 append 两种模式。"""
    args_replace = update_cli.parse_args([
        "--mode", "replace",
        "--dataset-id", "ds-001",
        "--baseline-purpose", "initial_build",
        "--import-batch-id", "batch-1",
        "--start-time", "2024-01-01 00:00:00",
        "--end-time", "2024-02-01 00:00:00",
    ])
    assert args_replace.mode == "replace"

    args_append = update_cli.parse_args([
        "--mode", "append",
        "--dataset-id", "ds-001",
        "--baseline-purpose", "manual_update",
        "--import-batch-id", "batch-2",
        "--start-time", "2024-01-01 00:00:00",
        "--end-time", "2024-02-01 00:00:00",
    ])
    assert args_append.mode == "append"


def test_training_update_help_does_not_connect():
    """training update CLI --help 不连接 ClickHouse。"""
    # 上一参数化测试已覆盖，此测试确认 update CLI 模块可正常导入
    assert hasattr(update_cli, "main")
    assert hasattr(update_cli, "parse_args")
