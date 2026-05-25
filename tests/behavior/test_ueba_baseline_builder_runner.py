"""Tests for UEBA acceptance baseline builder runner."""

from __future__ import annotations

from types import SimpleNamespace
import json
import sys

from tests.behavior.ueba_baseline_acceptance import baseline_builder_runner
from tests.behavior.ueba_baseline_acceptance.baseline_builder_runner import (
    build_baseline_command,
    run_baseline_build,
)
from tests.behavior.ueba_baseline_acceptance.config import AcceptanceConfig


def test_build_baseline_command_contains_official_cli_arguments(tmp_path):
    """Command construction should target the existing CLI with required args."""
    config = AcceptanceConfig(output_dir=tmp_path)

    command = build_baseline_command(config)

    assert command[0] == sys.executable
    assert "scripts/build_ueba_baseline.py" in command
    for option in (
        "--clickhouse-host",
        "--clickhouse-port",
        "--clickhouse-user",
        "--clickhouse-password",
        "--clickhouse-database",
        "--start-time",
        "--end-time",
        "--log-type",
        "--model-version",
        "--min-sample-count",
    ):
        assert option in command


def test_build_result_redacts_non_empty_password(tmp_path, monkeypatch):
    """Execution command can contain a password, but build_result must not."""
    config = AcceptanceConfig(output_dir=tmp_path, clickhouse_password="secret")
    _write_precheck_files(tmp_path)

    monkeypatch.setattr(
        baseline_builder_runner.subprocess,
        "run",
        lambda *args, **kwargs: _completed(stdout=_success_stdout()),
    )

    result = run_baseline_build(config)

    build_result = _read_json(tmp_path / "build_result.json")
    assert result["success"] is True
    assert "secret" not in json.dumps(build_result, ensure_ascii=False)
    assert "***" in build_result["command"]


def test_precheck_failures_do_not_call_subprocess(tmp_path, monkeypatch):
    """Missing or invalid prerequisite reports should stop before CLI invocation."""
    calls = []

    def fake_run(*args, **kwargs):
        calls.append((args, kwargs))
        return _completed(stdout=_success_stdout())

    monkeypatch.setattr(baseline_builder_runner.subprocess, "run", fake_run)

    cases = [
        {},
        {"expected": True},
        {"expected": True, "load_result": {"success": False, "expected_rows": 1, "database_rows": 1}},
        {"expected": True, "load_result": {"success": True, "expected_rows": 2, "database_rows": 1}},
    ]
    for index, case in enumerate(cases):
        case_dir = tmp_path / f"case_{index}"
        case_dir.mkdir()
        if case.get("expected"):
            _write_json(case_dir / "expected_baselines.json", {"fixture_id": "x"})
        if "load_result" in case:
            _write_json(case_dir / "load_result.json", case["load_result"])

        result = run_baseline_build(AcceptanceConfig(output_dir=case_dir))
        run_state = _read_json(case_dir / "run_state.json")

        assert result["success"] is False
        assert result["stage"] == "precheck"
        assert result["error"]
        assert run_state["baseline_built"] is False

    assert calls == []


def test_cli_success_parses_json_and_updates_run_state(tmp_path, monkeypatch):
    """Successful CLI JSON should be summarized and mark baseline_built true."""
    config = AcceptanceConfig(output_dir=tmp_path)
    _write_precheck_files(tmp_path)
    monkeypatch.setattr(
        baseline_builder_runner.subprocess,
        "run",
        lambda *args, **kwargs: _completed(stdout=_success_stdout()),
    )

    result = run_baseline_build(config)

    run_state = _read_json(tmp_path / "run_state.json")
    assert result["success"] is True
    assert result["total_log_count"] == 30065
    assert result["total_user_count"] == 24
    assert run_state["baseline_built"] is True
    assert run_state["user_count"] == 24


def test_cli_nonzero_returncode_is_failure(tmp_path, monkeypatch):
    """Non-zero return codes should fail even when stdout is parseable."""
    config = AcceptanceConfig(output_dir=tmp_path)
    _write_precheck_files(tmp_path)
    monkeypatch.setattr(
        baseline_builder_runner.subprocess,
        "run",
        lambda *args, **kwargs: _completed(
            returncode=1,
            stdout='{"success": false, "message": "boom"}',
            stderr="cli stderr",
        ),
    )

    result = run_baseline_build(config)

    run_state = _read_json(tmp_path / "run_state.json")
    assert result["success"] is False
    assert "returncode=1" in result["error"]
    assert "cli stderr" in result["error"]
    assert run_state["baseline_built"] is False


def test_cli_invalid_json_stdout_is_failure(tmp_path, monkeypatch):
    """Invalid stdout JSON should fail and preserve stdout for inspection."""
    config = AcceptanceConfig(output_dir=tmp_path)
    _write_precheck_files(tmp_path)
    monkeypatch.setattr(
        baseline_builder_runner.subprocess,
        "run",
        lambda *args, **kwargs: _completed(stdout="not json"),
    )

    result = run_baseline_build(config)

    run_state = _read_json(tmp_path / "run_state.json")
    assert result["success"] is False
    assert "无法解析 CLI JSON" in result["error"]
    assert result["stdout"] == "not json"
    assert run_state["baseline_built"] is False


def test_cli_json_success_false_is_failure(tmp_path, monkeypatch):
    """A zero returncode with success=false JSON should still fail."""
    config = AcceptanceConfig(output_dir=tmp_path)
    _write_precheck_files(tmp_path)
    monkeypatch.setattr(
        baseline_builder_runner.subprocess,
        "run",
        lambda *args, **kwargs: _completed(stdout='{"success": false, "message": "xxx"}'),
    )

    result = run_baseline_build(config)

    run_state = _read_json(tmp_path / "run_state.json")
    assert result["success"] is False
    assert result["cli_success"] is False
    assert "xxx" in result["error"]
    assert run_state["baseline_built"] is False


def _write_precheck_files(output_dir):
    _write_json(output_dir / "expected_baselines.json", {"fixture_id": "ueba_fixture_v1_seed_42"})
    _write_json(
        output_dir / "load_result.json",
        {"success": True, "expected_rows": 30065, "database_rows": 30065},
    )


def _success_stdout():
    return json.dumps(
        {
            "success": True,
            "total_user_count": 24,
            "reliable_user_count": 22,
            "unreliable_user_count": 2,
            "total_log_count": 30065,
            "message": "saved 24 user baselines",
        }
    )


def _completed(returncode=0, stdout="", stderr=""):
    return SimpleNamespace(returncode=returncode, stdout=stdout, stderr=stderr)


def _write_json(path, payload):
    path.write_text(json.dumps(payload), encoding="utf-8")


def _read_json(path):
    return json.loads(path.read_text(encoding="utf-8"))
