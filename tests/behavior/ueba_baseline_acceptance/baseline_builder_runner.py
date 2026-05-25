"""Runner that invokes the official UEBA baseline build CLI for acceptance."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from json import JSONDecodeError
from pathlib import Path
from typing import Any
import json
import subprocess
import sys
import time

from .config import AcceptanceConfig
from .report_writer import ensure_output_dir, read_json, update_run_state, write_json


BUILD_RESULT_FILE = "build_result.json"
EXPECTED_FILE = "expected_baselines.json"
LOAD_RESULT_FILE = "load_result.json"
PROJECT_ROOT = Path(__file__).resolve().parents[3]
SCRIPT_PATH = Path("scripts/build_ueba_baseline.py")


@dataclass(slots=True)
class BaselineBuildRunResult:
    """Structured result for one acceptance baseline build invocation."""

    success: bool
    fixture_id: str
    model_version: str
    stage: str
    command: list[str]
    returncode: int
    cli_success: bool
    duration_seconds: float
    total_user_count: int = 0
    reliable_user_count: int = 0
    unreliable_user_count: int = 0
    total_log_count: int = 0
    message: str | None = None
    error: str | None = None
    stdout: str | None = None
    stderr: str | None = None

    def to_dict(self) -> dict[str, Any]:
        """Convert the result to a compact JSON-ready dict."""
        payload = asdict(self)
        return {
            key: value
            for key, value in payload.items()
            if value is not None or key == "error"
        }


def build_baseline_command(config: AcceptanceConfig) -> list[str]:
    """Build the official scripts/build_ueba_baseline.py command."""
    return [
        sys.executable,
        str(SCRIPT_PATH),
        "--clickhouse-host",
        config.clickhouse_host,
        "--clickhouse-port",
        str(config.clickhouse_port),
        "--clickhouse-user",
        config.clickhouse_user,
        "--clickhouse-password",
        config.clickhouse_password,
        "--clickhouse-database",
        config.clickhouse_database,
        "--start-time",
        config.start_time,
        "--end-time",
        config.end_time,
        "--log-type",
        config.log_type,
        "--model-version",
        config.model_version,
        "--min-sample-count",
        str(config.min_sample_count),
    ]


def run_baseline_build(config: AcceptanceConfig) -> dict[str, Any]:
    """Run the official baseline CLI and write build_result.json."""
    begin = time.time()
    output_dir = ensure_output_dir(config)
    build_result_path = output_dir / BUILD_RESULT_FILE

    precheck_error = _precheck(config, output_dir)
    if precheck_error:
        result = _failure_result(
            config=config,
            stage="precheck",
            command=[],
            returncode=-1,
            duration_seconds=0.0,
            error=precheck_error,
        )
        write_json(build_result_path, result)
        _mark_failed(config, build_result_path, precheck_error)
        return result

    command = build_baseline_command(config)
    redacted_command = _redact_command(command)
    try:
        completed = subprocess.run(
            command,
            cwd=PROJECT_ROOT,
            capture_output=True,
            text=True,
            check=False,
        )
    except Exception as exc:
        error = f"{type(exc).__name__}: {exc}"
        result = _failure_result(
            config=config,
            stage="baseline_build",
            command=redacted_command,
            returncode=-1,
            duration_seconds=round(time.time() - begin, 3),
            error=error,
        )
        write_json(build_result_path, result)
        _mark_failed(config, build_result_path, error)
        return result

    duration_seconds = round(time.time() - begin, 3)
    stdout = completed.stdout or ""
    stderr = completed.stderr or ""
    parsed_output, parse_error = _parse_cli_output(stdout)
    if parse_error:
        error = f"无法解析 CLI JSON: {parse_error}"
        result = _failure_result(
            config=config,
            stage="baseline_build",
            command=redacted_command,
            returncode=completed.returncode,
            duration_seconds=duration_seconds,
            error=error,
            stdout=stdout,
            stderr=stderr,
        )
        write_json(build_result_path, result)
        _mark_failed(config, build_result_path, error)
        return result

    cli_success = bool(parsed_output.get("success"))
    if completed.returncode != 0 or not cli_success:
        error = _build_cli_error(completed.returncode, stderr, parsed_output)
        result = _result_from_cli_output(
            config=config,
            command=redacted_command,
            returncode=completed.returncode,
            cli_success=cli_success,
            duration_seconds=duration_seconds,
            parsed_output=parsed_output,
            success=False,
            error=error,
            stdout=stdout,
            stderr=stderr,
        )
        write_json(build_result_path, result)
        _mark_failed(config, build_result_path, error)
        return result

    result = _result_from_cli_output(
        config=config,
        command=redacted_command,
        returncode=completed.returncode,
        cli_success=cli_success,
        duration_seconds=duration_seconds,
        parsed_output=parsed_output,
        success=True,
        error=None,
    )
    write_json(build_result_path, result)
    update_run_state(
        config,
        baseline_built=True,
        comparison_done=False,
        build_result_path=str(build_result_path),
        model_version=config.model_version,
        total_logs=result.get("total_log_count", 0),
        user_count=result.get("total_user_count", 0),
        last_error=None,
    )
    return result


def _precheck(config: AcceptanceConfig, output_dir: Path) -> str | None:
    expected_path = output_dir / EXPECTED_FILE
    load_result_path = output_dir / LOAD_RESULT_FILE
    if not expected_path.exists():
        return "expected_baselines.json missing; please run menu item 1 or 2 first"
    if not load_result_path.exists():
        return "load_result.json missing or unsuccessful; please run menu item 2 first"

    try:
        load_result = read_json(load_result_path)
    except Exception as exc:
        return f"load_result.json unreadable: {type(exc).__name__}: {exc}"

    if load_result.get("success") is not True:
        return "load_result.json missing or unsuccessful; please run menu item 2 first"

    expected_rows = int(load_result.get("expected_rows") or 0)
    database_rows = int(load_result.get("database_rows") or 0)
    if expected_rows <= 0:
        return "load_result.json expected_rows must be greater than 0"
    if database_rows != expected_rows:
        return (
            "load_result.json row count mismatch: "
            f"database_rows={database_rows}, expected_rows={expected_rows}"
        )
    return None


def _parse_cli_output(stdout: str) -> tuple[dict[str, Any], str | None]:
    payload = stdout.strip()
    if not payload:
        return {}, "stdout is empty"
    try:
        parsed = json.loads(payload)
    except JSONDecodeError as exc:
        return {}, str(exc)
    if not isinstance(parsed, dict):
        return {}, "stdout JSON is not an object"
    return parsed, None


def _result_from_cli_output(
    *,
    config: AcceptanceConfig,
    command: list[str],
    returncode: int,
    cli_success: bool,
    duration_seconds: float,
    parsed_output: dict[str, Any],
    success: bool,
    error: str | None,
    stdout: str | None = None,
    stderr: str | None = None,
) -> dict[str, Any]:
    result = BaselineBuildRunResult(
        success=success,
        fixture_id=config.fixture_id,
        model_version=str(parsed_output.get("model_version") or config.model_version),
        stage="baseline_build",
        command=command,
        returncode=returncode,
        cli_success=cli_success,
        total_user_count=int(parsed_output.get("total_user_count") or 0),
        reliable_user_count=int(parsed_output.get("reliable_user_count") or 0),
        unreliable_user_count=int(parsed_output.get("unreliable_user_count") or 0),
        total_log_count=int(parsed_output.get("total_log_count") or 0),
        duration_seconds=duration_seconds,
        message=parsed_output.get("message"),
        error=error,
        stdout=stdout,
        stderr=stderr,
    ).to_dict()
    return result


def _failure_result(
    *,
    config: AcceptanceConfig,
    stage: str,
    command: list[str],
    returncode: int,
    duration_seconds: float,
    error: str,
    stdout: str | None = None,
    stderr: str | None = None,
) -> dict[str, Any]:
    return BaselineBuildRunResult(
        success=False,
        fixture_id=config.fixture_id,
        model_version=config.model_version,
        stage=stage,
        command=command,
        returncode=returncode,
        cli_success=False,
        duration_seconds=duration_seconds,
        error=error,
        stdout=stdout,
        stderr=stderr,
    ).to_dict()


def _build_cli_error(returncode: int, stderr: str, parsed_output: dict[str, Any]) -> str:
    message = str(parsed_output.get("message") or "").strip()
    stderr_text = stderr.strip()
    if stderr_text:
        return f"CLI failed with returncode={returncode}: {stderr_text}"
    if message:
        return f"CLI failed with returncode={returncode}: {message}"
    return f"CLI failed with returncode={returncode}"


def _mark_failed(config: AcceptanceConfig, build_result_path: Path, error: str) -> None:
    update_run_state(
        config,
        baseline_built=False,
        comparison_done=False,
        build_result_path=str(build_result_path),
        last_error=error,
    )


def _redact_command(command: list[str]) -> list[str]:
    redacted = list(command)
    for index, value in enumerate(redacted[:-1]):
        if value == "--clickhouse-password" and redacted[index + 1] != "":
            redacted[index + 1] = "***"
    return redacted


__all__ = [
    "BUILD_RESULT_FILE",
    "BaselineBuildRunResult",
    "build_baseline_command",
    "run_baseline_build",
]
