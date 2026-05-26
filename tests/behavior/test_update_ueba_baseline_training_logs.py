"""Tests for update_ueba_baseline_training_logs CLI."""

import json
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from scripts import update_ueba_baseline_training_logs as update_cli


class FakeStore:
    """Fake TrainingLogStore for CLI tests."""

    SOURCE_TABLE = "logs_structured"
    TARGET_TABLE = "ueba_baseline_training_logs"
    instances = []

    def __init__(self, client, database="log_analysis"):
        self.client = client
        self.database = database
        self.ensure_called = False
        self.calls = []
        FakeStore.instances.append(self)

    def ensure_table(self):
        self.ensure_called = True

    def replace_from_logs_structured(self, **kwargs):
        self.calls.append(("replace", kwargs))
        return {"selected_rows": 10, "inserted_rows": 10, "target_rows": 10}

    def append_from_logs_structured(self, **kwargs):
        self.calls.append(("append", kwargs))
        return {"selected_rows": 3, "inserted_rows": 3, "target_rows": 13}


def _argv(mode="replace"):
    return [
        "--mode",
        mode,
        "--dataset-id",
        "baseline_init_test",
        "--baseline-purpose",
        "initial_build",
        "--import-batch-id",
        "import_test_001",
        "--start-time",
        "2026-05-01 00:00:00",
        "--end-time",
        "2026-06-01 00:00:00",
        "--log-type",
        "vpn",
        "--created-by",
        "manual",
    ]


def test_help_does_not_create_clickhouse_client(monkeypatch, capsys):
    """--help should print argparse help without connecting ClickHouse."""
    create_client = Mock(side_effect=AssertionError("should not connect"))
    monkeypatch.setattr(update_cli, "create_clickhouse_client", create_client)

    with pytest.raises(SystemExit) as exc_info:
        update_cli.main(["--help"])

    assert exc_info.value.code == 0
    create_client.assert_not_called()
    assert "UEBA baseline 训练日志表" in capsys.readouterr().out


def test_update_cli_replace_outputs_required_json(monkeypatch, capsys):
    """Successful replace should print the required JSON fields and close client."""
    FakeStore.instances = []
    fake_client = SimpleNamespace(close=Mock())
    monkeypatch.setattr(update_cli, "create_clickhouse_client", Mock(return_value=fake_client))
    monkeypatch.setattr(update_cli, "TrainingLogStore", FakeStore)

    exit_code = update_cli.main(_argv("replace"))

    assert exit_code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["success"] is True
    assert payload["mode"] == "replace"
    assert payload["dataset_id"] == "baseline_init_test"
    assert payload["source_table"] == "logs_structured"
    assert payload["target_table"] == "ueba_baseline_training_logs"
    assert payload["selected_rows"] == 10
    assert payload["inserted_rows"] == 10
    assert payload["target_rows"] == 10
    assert payload["log_type"] == "vpn"
    assert payload["error"] is None
    fake_client.close.assert_called_once_with()
    store = FakeStore.instances[0]
    assert store.ensure_called is True
    assert store.calls[0][0] == "replace"
    assert store.calls[0][1]["created_by"] == "manual"


def test_update_cli_append_calls_append(monkeypatch, capsys):
    """append mode should not call replace path."""
    FakeStore.instances = []
    fake_client = SimpleNamespace(close=Mock())
    monkeypatch.setattr(update_cli, "create_clickhouse_client", Mock(return_value=fake_client))
    monkeypatch.setattr(update_cli, "TrainingLogStore", FakeStore)

    exit_code = update_cli.main(_argv("append"))

    assert exit_code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["mode"] == "append"
    assert payload["selected_rows"] == 3
    assert FakeStore.instances[0].calls[0][0] == "append"


def test_update_cli_failure_outputs_success_false_and_nonzero(monkeypatch, capsys):
    """Script failures should become success=false JSON and non-zero exit code."""
    fake_client = SimpleNamespace(close=Mock())
    monkeypatch.setattr(update_cli, "create_clickhouse_client", Mock(return_value=fake_client))
    monkeypatch.setattr(update_cli, "run_update", Mock(side_effect=RuntimeError("boom")))

    exit_code = update_cli.main(_argv("replace"))

    assert exit_code == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload["success"] is False
    assert payload["mode"] == "replace"
    assert payload["dataset_id"] == "baseline_init_test"
    assert payload["selected_rows"] == 0
    assert payload["inserted_rows"] == 0
    assert payload["target_rows"] == 0
    assert "RuntimeError: boom" in payload["error"]
    fake_client.close.assert_called_once_with()


def test_parse_args_requires_known_mode_and_dataset_id():
    """argparse should expose the manual update controls."""
    args = update_cli.parse_args(_argv("replace"))

    assert args.mode == "replace"
    assert args.dataset_id == "baseline_init_test"
    assert args.baseline_purpose == "initial_build"
    assert args.import_batch_id == "import_test_001"
