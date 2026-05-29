"""Tests for the UEBA validation result export CLI."""

import csv
import io
import json
import sys
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from scripts import export_ueba_validation_results as export_cli


START_TIME = "2024-03-01 00:00:00"
END_TIME = "2024-03-02 00:00:00"
MODEL_VERSION = "ueba_baseline_v1"
VALIDATION_RUN_ID = "run-export-1"


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


def _row(**overrides):
    row = {
        "validation_id": "validation-1",
        "validation_run_id": VALIDATION_RUN_ID,
        "source_identity": "request_id:req-1",
        "source_log_id": 1001,
        "timestamp": "2024-03-01 10:00:00",
        "username": "alice",
        "log_type": "vpn",
        "request_id": "req-1",
        "baseline_model_version": MODEL_VERSION,
        "baseline_is_reliable": 1,
        "ueba_score": 25,
        "ueba_risk_level": "MEDIUM",
        "ueba_anomaly_reasons": "[]",
        "validation_status": "VALIDATED",
        "validated_at": "2024-03-01 10:00:10",
        "error": None,
        "created_at": "2024-03-01 10:00:11",
    }
    row.update(overrides)
    return row


def test_help_does_not_create_clickhouse_client(monkeypatch, capsys):
    """--help should not connect to ClickHouse."""
    create_client = Mock(side_effect=AssertionError("should not connect"))
    monkeypatch.setattr(export_cli, "create_clickhouse_client", create_client)

    with pytest.raises(SystemExit) as exc_info:
        export_cli.main(["--help"])

    assert exc_info.value.code == 0
    create_client.assert_not_called()
    assert "Export UEBA validation results" in capsys.readouterr().out


def test_parse_args_requires_start_time():
    """start-time is required."""
    with pytest.raises(SystemExit) as exc_info:
        export_cli.parse_args(["--end-time", END_TIME, "--model-version", MODEL_VERSION])

    assert exc_info.value.code == 2


def test_parse_args_requires_end_time():
    """end-time is required."""
    with pytest.raises(SystemExit) as exc_info:
        export_cli.parse_args(["--start-time", START_TIME, "--model-version", MODEL_VERSION])

    assert exc_info.value.code == 2


def test_parse_args_requires_model_version():
    """model-version is required."""
    with pytest.raises(SystemExit) as exc_info:
        export_cli.parse_args(["--start-time", START_TIME, "--end-time", END_TIME])

    assert exc_info.value.code == 2


def test_parse_args_rejects_non_positive_limit():
    """limit must be positive."""
    with pytest.raises(SystemExit) as exc_info:
        export_cli.parse_args(_argv(["--limit", "0"]))

    assert exc_info.value.code == 2


def test_parse_args_restricts_format():
    """format should only accept csv or json."""
    with pytest.raises(SystemExit) as exc_info:
        export_cli.parse_args(_argv(["--format", "xml"]))

    assert exc_info.value.code == 2


def test_parse_args_defaults_to_csv():
    """CSV should be the default export format."""
    args = export_cli.parse_args(_argv())

    assert args.format == "csv"
    assert args.log_type == "vpn"
    assert args.limit == 1000


def test_query_results_passes_required_filters(monkeypatch):
    """Repository query should receive the required filters."""
    repository = Mock()
    repository.query_validation_results.return_value = []
    repository_cls = Mock(return_value=repository)
    monkeypatch.setattr(export_cli, "UebaValidationRepository", repository_cls)
    args = export_cli.parse_args(_argv(["--limit", "25", "--database", "log_analysis_test"]))

    rows = export_cli.query_results(args, client=object())

    assert rows == []
    repository_cls.assert_called_once()
    repository.query_validation_results.assert_called_once_with(
        start_time=START_TIME,
        end_time=END_TIME,
        model_version=MODEL_VERSION,
        log_type="vpn",
        risk_level=None,
        validation_status=None,
        username=None,
        validation_run_id=None,
        source_identity=None,
        limit=25,
    )


def test_query_results_passes_optional_filters(monkeypatch):
    """Optional filters should be forwarded to the repository."""
    repository = Mock()
    repository.query_validation_results.return_value = []
    monkeypatch.setattr(export_cli, "UebaValidationRepository", Mock(return_value=repository))
    args = export_cli.parse_args(
        _argv(
            [
                "--validation-run-id",
                VALIDATION_RUN_ID,
                "--risk-level",
                "HIGH",
                "--validation-status",
                "VALIDATED",
                "--username-filter",
                "alice",
                "--source-identity",
                "request_id:req-1",
            ]
        )
    )

    export_cli.query_results(args, client=object())

    kwargs = repository.query_validation_results.call_args.kwargs
    assert kwargs["validation_run_id"] == VALIDATION_RUN_ID
    assert kwargs["risk_level"] == "HIGH"
    assert kwargs["validation_status"] == "VALIDATED"
    assert kwargs["username"] == "alice"
    assert kwargs["source_identity"] == "request_id:req-1"


def test_csv_stdout_outputs_header_and_data(monkeypatch, capsys):
    """CSV export to stdout should include header and rows."""
    fake_client = SimpleNamespace(close=Mock())
    monkeypatch.setattr(export_cli, "create_clickhouse_client", Mock(return_value=fake_client))
    monkeypatch.setattr(export_cli, "query_results", Mock(return_value=[_row()]))

    exit_code = export_cli.main(_argv())

    assert exit_code == 0
    output = capsys.readouterr().out
    parsed = list(csv.DictReader(io.StringIO(output)))
    assert parsed[0]["validation_id"] == "validation-1"
    assert parsed[0]["validation_run_id"] == VALIDATION_RUN_ID
    assert "raw_log" not in output


def test_json_stdout_outputs_valid_json(monkeypatch, capsys):
    """JSON export to stdout should be valid JSON."""
    fake_client = SimpleNamespace(close=Mock())
    monkeypatch.setattr(export_cli, "create_clickhouse_client", Mock(return_value=fake_client))
    monkeypatch.setattr(export_cli, "query_results", Mock(return_value=[_row()]))

    exit_code = export_cli.main(_argv(["--format", "json"]))

    assert exit_code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload[0]["source_identity"] == "request_id:req-1"


def test_output_writes_file(monkeypatch, tmp_path):
    """--output should write the export to a file."""
    fake_client = SimpleNamespace(close=Mock())
    monkeypatch.setattr(export_cli, "create_clickhouse_client", Mock(return_value=fake_client))
    monkeypatch.setattr(export_cli, "query_results", Mock(return_value=[_row()]))
    output_path = tmp_path / "exports" / "results.json"

    exit_code = export_cli.main(_argv(["--format", "json", "--output", str(output_path)]))

    assert exit_code == 0
    assert json.loads(output_path.read_text(encoding="utf-8"))[0]["username"] == "alice"


def test_none_values_are_empty_in_csv():
    """None should serialize as an empty CSV cell."""
    stream = io.StringIO()

    export_cli.write_csv([_row(error=None, request_id=None)], stream)

    parsed = list(csv.DictReader(io.StringIO(stream.getvalue())))
    assert parsed[0]["error"] == ""
    assert parsed[0]["request_id"] == ""


def test_query_exception_returns_nonzero_and_closes_client(monkeypatch, capsys):
    """Query failures should return non-zero and close the client."""
    fake_client = SimpleNamespace(close=Mock())
    monkeypatch.setattr(export_cli, "create_clickhouse_client", Mock(return_value=fake_client))
    monkeypatch.setattr(export_cli, "query_results", Mock(side_effect=RuntimeError("boom")))

    exit_code = export_cli.main(_argv())

    assert exit_code == 1
    assert "RuntimeError" in capsys.readouterr().err
    fake_client.close.assert_called_once_with()


def test_success_closes_client(monkeypatch):
    """The client should close after a successful export."""
    fake_client = SimpleNamespace(close=Mock())
    monkeypatch.setattr(export_cli, "create_clickhouse_client", Mock(return_value=fake_client))
    monkeypatch.setattr(export_cli, "query_results", Mock(return_value=[]))

    assert export_cli.main(_argv()) == 0
    fake_client.close.assert_called_once_with()


def test_password_is_redacted_from_errors(monkeypatch, capsys):
    """Configured password should not leak into error output."""
    fake_client = SimpleNamespace(close=Mock())
    monkeypatch.setattr(export_cli, "create_clickhouse_client", Mock(return_value=fake_client))
    monkeypatch.setattr(export_cli, "query_results", Mock(side_effect=RuntimeError("bad secret-for-test")))

    exit_code = export_cli.main(_argv(["--password", "secret-for-test"]))

    assert exit_code == 1
    output = capsys.readouterr().err
    assert "secret-for-test" not in output
    assert "***" in output


def test_script_source_has_no_statement_keywords_or_forbidden_markers():
    """Export CLI source should stay read-only and free of stage-only markers."""
    source = export_cli.Path("scripts/export_ueba_validation_results.py").read_text(encoding="utf-8")

    for marker in ("INSERT", "UPDATE", "ALTER", "DELETE", "TRUNCATE"):
        assert marker not in source
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
    assert "run" + "_ueba" + "_validation" not in source
    assert "validation" + "_service" not in source
