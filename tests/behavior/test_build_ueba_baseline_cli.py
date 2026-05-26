"""UEBA baseline CLI 入口测试。"""

import argparse
from datetime import datetime
import json
import sys
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from scripts import build_ueba_baseline
from src.behavior.config import UebaBaselineConfig
from src.behavior.schemas import BaselineBuildResult


def _clickhouse_args() -> argparse.Namespace:
    """构造 ClickHouse CLI 参数。"""
    return argparse.Namespace(
        clickhouse_host="localhost",
        clickhouse_port=8123,
        clickhouse_user="default",
        clickhouse_password="secret-for-test",
        clickhouse_database="log_analysis",
        clickhouse_secure=False,
    )


def test_create_clickhouse_client_checks_connection(monkeypatch):
    """create_clickhouse_client 应创建 client 并执行轻量连接探测。"""
    fake_client = Mock()
    fake_clickhouse_connect = Mock()
    fake_clickhouse_connect.get_client.return_value = fake_client
    monkeypatch.setitem(sys.modules, "clickhouse_connect", fake_clickhouse_connect)

    client = build_ueba_baseline.create_clickhouse_client(_clickhouse_args())

    assert client is fake_client
    fake_clickhouse_connect.get_client.assert_called_once_with(
        host="localhost",
        port=8123,
        username="default",
        password="secret-for-test",
        database="log_analysis",
        secure=False,
    )
    fake_client.command.assert_called_once_with("SELECT 1")


def test_main_success_closes_client(monkeypatch, capsys):
    """main 成功路径应输出 JSON、返回 0，并关闭 client。"""
    args = argparse.Namespace(clickhouse_database="log_analysis", log_type="vpn")
    config = UebaBaselineConfig(model_version="test_model")
    start_time = datetime(2026, 5, 1)
    end_time = datetime(2026, 5, 2)
    fake_client = Mock()
    fake_service = Mock()
    fake_service.build_baseline_once.return_value = BaselineBuildResult(
        success=True,
        baseline_start_time=start_time,
        baseline_end_time=end_time,
        total_user_count=1,
        reliable_user_count=1,
        unreliable_user_count=0,
        total_log_count=20,
        model_version="test_model",
        duration_seconds=0.1,
        message="saved 1 user baselines",
    )

    monkeypatch.setattr(build_ueba_baseline, "parse_args", Mock(return_value=args))
    monkeypatch.setattr(build_ueba_baseline, "build_config", Mock(return_value=config))
    monkeypatch.setattr(build_ueba_baseline, "resolve_time_window", Mock(return_value=(start_time, end_time)))
    monkeypatch.setattr(build_ueba_baseline, "create_clickhouse_client", Mock(return_value=fake_client))
    monkeypatch.setattr(build_ueba_baseline, "build_service", Mock(return_value=fake_service))

    exit_code = build_ueba_baseline.main(["--unused-because-parse-args-is-mocked"])

    assert exit_code == 0
    fake_service.build_baseline_once.assert_called_once_with(
        start_time=start_time,
        end_time=end_time,
        log_type="vpn",
    )
    fake_client.close.assert_called_once_with()
    payload = json.loads(capsys.readouterr().out)
    assert payload["success"] is True
    assert payload["message"] == "saved 1 user baselines"


def test_main_failure_outputs_json_and_closes_client(monkeypatch, capsys):
    """业务失败路径仍应输出 success=false、返回 1，并关闭已创建 client。"""
    args = argparse.Namespace(clickhouse_database="log_analysis", log_type="vpn")
    fake_client = Mock()

    monkeypatch.setattr(build_ueba_baseline, "parse_args", Mock(return_value=args))
    monkeypatch.setattr(build_ueba_baseline, "build_config", Mock(return_value=UebaBaselineConfig(model_version="test_model")))
    monkeypatch.setattr(
        build_ueba_baseline,
        "resolve_time_window",
        Mock(return_value=(datetime(2026, 5, 1), datetime(2026, 5, 2))),
    )
    monkeypatch.setattr(build_ueba_baseline, "create_clickhouse_client", Mock(return_value=fake_client))
    monkeypatch.setattr(build_ueba_baseline, "build_service", Mock(side_effect=RuntimeError("boom")))

    exit_code = build_ueba_baseline.main([])

    assert exit_code == 1
    fake_client.close.assert_called_once_with()
    payload = json.loads(capsys.readouterr().out)
    assert payload["success"] is False
    assert payload["model_version"] == "test_model"
    assert "RuntimeError: boom" in payload["message"]


def test_main_failure_does_not_mask_original_error_when_close_fails(monkeypatch, capsys):
    """close 异常不应覆盖原始业务异常。"""
    args = argparse.Namespace(clickhouse_database="log_analysis", log_type="vpn")
    fake_client = SimpleNamespace(close=Mock(side_effect=RuntimeError("close failed")))

    monkeypatch.setattr(build_ueba_baseline, "parse_args", Mock(return_value=args))
    monkeypatch.setattr(build_ueba_baseline, "build_config", Mock(return_value=UebaBaselineConfig()))
    monkeypatch.setattr(
        build_ueba_baseline,
        "resolve_time_window",
        Mock(return_value=(datetime(2026, 5, 1), datetime(2026, 5, 2))),
    )
    monkeypatch.setattr(build_ueba_baseline, "create_clickhouse_client", Mock(return_value=fake_client))
    monkeypatch.setattr(build_ueba_baseline, "build_service", Mock(side_effect=RuntimeError("original error")))

    exit_code = build_ueba_baseline.main([])

    assert exit_code == 1
    fake_client.close.assert_called_once_with()
    payload = json.loads(capsys.readouterr().out)
    assert "RuntimeError: original error" in payload["message"]
    assert "close failed" not in payload["message"]


def test_help_does_not_create_clickhouse_client(monkeypatch, capsys):
    """--help 应只打印帮助信息，不触发 ClickHouse 连接。"""
    create_client = Mock(side_effect=AssertionError("should not connect"))
    monkeypatch.setattr(build_ueba_baseline, "create_clickhouse_client", create_client)

    with pytest.raises(SystemExit) as exc_info:
        build_ueba_baseline.main(["--help"])

    assert exc_info.value.code == 0
    create_client.assert_not_called()
    assert "构建 UEBA 离线用户行为 Baseline" in capsys.readouterr().out



def test_parse_args_accepts_training_source_table_options():
    """build CLI should parse source-table, dataset-id, and active-only options."""
    args = build_ueba_baseline.parse_args(
        [
            "--source-table",
            "ueba_baseline_training_logs",
            "--dataset-id",
            "baseline_init_2026_05",
            "--active-only",
        ]
    )

    assert args.source_table == "ueba_baseline_training_logs"
    assert args.dataset_id == "baseline_init_2026_05"
    assert args.active_only is True


def test_build_service_passes_training_source_options_to_repository():
    """build_service should wire training table options into UebaRepository."""
    fake_client = Mock()
    config = UebaBaselineConfig()

    service = build_ueba_baseline.build_service(
        fake_client,
        "log_analysis",
        config,
        source_table="ueba_baseline_training_logs",
        dataset_id="baseline_init_2026_05",
        active_only=True,
    )

    assert service.repository.source_table == "ueba_baseline_training_logs"
    assert service.repository.dataset_id == "baseline_init_2026_05"
    assert service.repository.active_only is True
    assert service.baseline_store.TABLE_NAME == "user_behavior_baselines"


def test_help_lists_source_table_options_without_connecting(monkeypatch, capsys):
    """--help should expose new source controls and still avoid ClickHouse."""
    create_client = Mock(side_effect=AssertionError("should not connect"))
    monkeypatch.setattr(build_ueba_baseline, "create_clickhouse_client", create_client)

    with pytest.raises(SystemExit) as exc_info:
        build_ueba_baseline.main(["--help"])

    assert exc_info.value.code == 0
    output = capsys.readouterr().out
    assert "--source-table" in output
    assert "--dataset-id" in output
    assert "--active-only" in output
    create_client.assert_not_called()
