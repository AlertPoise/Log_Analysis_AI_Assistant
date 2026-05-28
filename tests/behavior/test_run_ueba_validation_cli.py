"""Tests for the UEBA validation CLI."""

import argparse
import json
import sys
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from scripts import run_ueba_validation as validation_cli
from src.behavior.validation_service import UebaValidationService


START_TIME = "2024-03-01 00:00:00"
END_TIME = "2024-03-02 00:00:00"
MODEL_VERSION = "ueba_baseline_v1"


def _argv(extra=None):
    args = [
        "--start-time",
        START_TIME,
        "--end-time",
        END_TIME,
        "--model-version",
        MODEL_VERSION,
    ]
    if extra:
        args.extend(extra)
    return args


def _summary(success=True, dry_run=True, written_count=0):
    return {
        "success": success,
        "processed_count": 1,
        "selected_count": 1,
        "scored_count": 1,
        "written_count": written_count,
        "skipped_count": 0,
        "no_baseline_count": 0,
        "unreliable_baseline_count": 0,
        "failed_count": 0,
        "dry_run": dry_run,
        "risk_level_counts": {"LOW": 1},
        "validation_status_counts": {"VALIDATED": 1},
        "sample_results": [],
        "start_time": START_TIME,
        "end_time": END_TIME,
        "log_type": "vpn",
        "model_version": MODEL_VERSION,
        "message": "ok",
        "error": None,
    }


def test_help_does_not_create_clickhouse_client(monkeypatch, capsys):
    """--help should print argparse help without connecting ClickHouse."""
    create_client = Mock(side_effect=AssertionError("should not connect"))
    monkeypatch.setattr(validation_cli, "create_clickhouse_client", create_client)

    with pytest.raises(SystemExit) as exc_info:
        validation_cli.main(["--help"])

    assert exc_info.value.code == 0
    create_client.assert_not_called()
    assert "UEBA baseline validation" in capsys.readouterr().out


def test_parse_args_defaults_to_dry_run():
    """Validation CLI should be dry-run unless --write is passed."""
    args = validation_cli.parse_args(_argv())

    assert args.write is False
    assert args.dry_run is False
    assert args.log_type == "vpn"
    assert args.limit == 1000
    assert args.sample_size == 5


def test_parse_args_supports_write():
    """--write should opt into non dry-run service execution."""
    args = validation_cli.parse_args(_argv(["--write"]))

    assert args.write is True


def test_parse_args_rejects_write_and_dry_run_together():
    """Conflicting write mode flags should fail before connecting."""
    with pytest.raises(SystemExit) as exc_info:
        validation_cli.parse_args(_argv(["--write", "--dry-run"]))

    assert exc_info.value.code == 2


def test_parse_args_requires_start_time():
    """start-time is required."""
    with pytest.raises(SystemExit) as exc_info:
        validation_cli.parse_args(["--end-time", END_TIME, "--model-version", MODEL_VERSION])

    assert exc_info.value.code == 2


def test_parse_args_requires_end_time():
    """end-time is required."""
    with pytest.raises(SystemExit) as exc_info:
        validation_cli.parse_args(["--start-time", START_TIME, "--model-version", MODEL_VERSION])

    assert exc_info.value.code == 2


def test_parse_args_requires_model_version():
    """model-version is required for auditable validation output."""
    with pytest.raises(SystemExit) as exc_info:
        validation_cli.parse_args(["--start-time", START_TIME, "--end-time", END_TIME])

    assert exc_info.value.code == 2


def test_parse_args_rejects_non_positive_limit():
    """limit must be positive."""
    with pytest.raises(SystemExit) as exc_info:
        validation_cli.parse_args(_argv(["--limit", "0"]))

    assert exc_info.value.code == 2


def test_parse_args_rejects_non_positive_sample_size():
    """sample-size must be positive."""
    with pytest.raises(SystemExit) as exc_info:
        validation_cli.parse_args(_argv(["--sample-size", "0"]))

    assert exc_info.value.code == 2


def test_create_clickhouse_client_checks_connection(monkeypatch):
    """create_clickhouse_client should build a client and run a lightweight probe."""
    fake_client = Mock()
    fake_clickhouse_connect = Mock()
    fake_clickhouse_connect.get_client.return_value = fake_client
    monkeypatch.setitem(sys.modules, "clickhouse_connect", fake_clickhouse_connect)
    args = validation_cli.parse_args(_argv(["--password", "secret-for-test"]))

    client = validation_cli.create_clickhouse_client(args)

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


def test_build_service_creates_validation_service():
    """build_service should wire repository and baseline store into service."""
    fake_client = Mock()
    args = validation_cli.parse_args(_argv(["--database", "log_analysis_test"]))

    service = validation_cli.build_service(fake_client, args)

    assert isinstance(service, UebaValidationService)
    assert service.validation_repository.client is fake_client
    assert service.validation_repository.database == "log_analysis_test"
    assert service.baseline_store.client is fake_client
    assert service.baseline_store.database == "log_analysis_test"


def test_main_success_outputs_json_and_closes_client(monkeypatch, capsys):
    """main success path should print service JSON, return 0, and close client."""
    fake_client = SimpleNamespace(close=Mock())
    fake_service = Mock()
    fake_service.run.return_value = _summary(success=True, dry_run=True)
    monkeypatch.setattr(validation_cli, "create_clickhouse_client", Mock(return_value=fake_client))
    monkeypatch.setattr(validation_cli, "build_service", Mock(return_value=fake_service))

    exit_code = validation_cli.main(_argv())

    assert exit_code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["success"] is True
    assert payload["dry_run"] is True
    fake_service.run.assert_called_once_with(
        start_time=START_TIME,
        end_time=END_TIME,
        log_type="vpn",
        model_version=MODEL_VERSION,
        limit=1000,
        dry_run=True,
        sample_size=5,
    )
    fake_client.close.assert_called_once_with()


def test_main_failure_outputs_success_false_and_nonzero(monkeypatch, capsys):
    """Business failure summaries should become a non-zero exit code."""
    fake_client = SimpleNamespace(close=Mock())
    fake_service = Mock()
    fake_service.run.return_value = _summary(success=False, dry_run=True)
    monkeypatch.setattr(validation_cli, "create_clickhouse_client", Mock(return_value=fake_client))
    monkeypatch.setattr(validation_cli, "build_service", Mock(return_value=fake_service))

    exit_code = validation_cli.main(_argv())

    assert exit_code == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload["success"] is False
    fake_client.close.assert_called_once_with()


def test_service_exception_closes_client_and_hides_password(monkeypatch, capsys):
    """Service exceptions should close the client and not leak password values."""
    fake_client = SimpleNamespace(close=Mock())
    fake_service = Mock()
    fake_service.run.side_effect = RuntimeError("boom secret-for-test")
    monkeypatch.setattr(validation_cli, "create_clickhouse_client", Mock(return_value=fake_client))
    monkeypatch.setattr(validation_cli, "build_service", Mock(return_value=fake_service))

    exit_code = validation_cli.main(_argv(["--password", "secret-for-test"]))

    assert exit_code == 1
    output = capsys.readouterr().out
    payload = json.loads(output)
    assert payload["success"] is False
    assert "RuntimeError" in payload["error"]
    assert "secret-for-test" not in output
    fake_client.close.assert_called_once_with()


def test_main_write_passes_dry_run_false(monkeypatch, capsys):
    """--write should call service with dry_run=False."""
    fake_client = SimpleNamespace(close=Mock())
    fake_service = Mock()
    fake_service.run.return_value = _summary(success=True, dry_run=False, written_count=1)
    monkeypatch.setattr(validation_cli, "create_clickhouse_client", Mock(return_value=fake_client))
    monkeypatch.setattr(validation_cli, "build_service", Mock(return_value=fake_service))

    exit_code = validation_cli.main(_argv(["--write"]))

    assert exit_code == 0
    assert json.loads(capsys.readouterr().out)["written_count"] == 1
    assert fake_service.run.call_args.kwargs["dry_run"] is False


def test_main_default_passes_dry_run_true(monkeypatch, capsys):
    """Default execution should call service with dry_run=True."""
    fake_client = SimpleNamespace(close=Mock())
    fake_service = Mock()
    fake_service.run.return_value = _summary(success=True, dry_run=True)
    monkeypatch.setattr(validation_cli, "create_clickhouse_client", Mock(return_value=fake_client))
    monkeypatch.setattr(validation_cli, "build_service", Mock(return_value=fake_service))

    exit_code = validation_cli.main(_argv())

    assert exit_code == 0
    assert json.loads(capsys.readouterr().out)["dry_run"] is True
    assert fake_service.run.call_args.kwargs["dry_run"] is True


def test_cli_source_has_no_forbidden_stage_markers():
    """CLI should not carry stage-only markers."""
    source = validation_cli.Path("scripts/run_ueba_validation.py").read_text(encoding="utf-8")
    forbidden = [
        "." + "tox",
        "fixture" + "_user",
        "2026" + "-05",
        "2026" + "-06",
        "accept" + "ance",
        "manual" + "_training" + "_update",
        "monthly" + "_training" + "_update",
    ]

    for marker in forbidden:
        assert marker not in source


def test_cli_source_has_no_mutation_keywords_or_direct_source_write():
    """CLI should not expose source-log write controls or mutation statements."""
    source = validation_cli.Path("scripts/run_ueba_validation.py").read_text(encoding="utf-8")

    for marker in ("UPDATE", "ALTER", "DELETE", "TRUNCATE"):
        assert marker not in source
    assert "client.insert" not in source
    assert "save_validation_results" not in source
    assert "logs" + "_structured" not in source
