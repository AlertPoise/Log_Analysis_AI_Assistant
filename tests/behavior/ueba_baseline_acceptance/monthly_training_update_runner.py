"""One-shot UEBA monthly training-table update acceptance runner."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from json import JSONDecodeError
from pathlib import Path
from typing import Any
import argparse
import hashlib
import json
import subprocess
import sys
import time

from .clickhouse_writer import create_clickhouse_client, load_fixture_to_clickhouse, validate_identifier
from .config import AcceptanceConfig
from .report_writer import ensure_output_dir, update_run_state, write_json
from src.behavior.baseline_store import BaselineStore
from src.behavior.training_log_store import TrainingLogStore


PROJECT_ROOT = Path(__file__).resolve().parents[3]
UPDATE_SCRIPT_PATH = Path("scripts/update_ueba_baseline_training_logs.py")
BUILD_SCRIPT_PATH = Path("scripts/build_ueba_baseline.py")

DATASET_ID = "ueba_training_monthly_acceptance"
MAY_START = "2026-05-01 00:00:00"
MAY_END = "2026-06-01 00:00:00"
JUNE_START = "2026-06-01 00:00:00"
JUNE_END = "2026-07-01 00:00:00"
MAY_MODEL_VERSION = "ueba_monthly_acceptance_may_init"
JUNE_MODEL_VERSION = "ueba_monthly_acceptance_june_updated"
TEST_WINDOWS_NOTE = (
    "2026-05 and 2026-06 are acceptance test windows only; "
    "production can use any approved training window."
)

STATE_FILE = "monthly_training_update_state.json"
REPORT_FILE = "monthly_training_update_report.json"
MAY_UPDATE_FILE = "may_training_update_result.json"
MAY_BUILD_FILE = "may_baseline_build_result.json"
JUNE_UPDATE_FILE = "june_training_update_result.json"
JUNE_BUILD_FILE = "june_baseline_build_result.json"
BASELINE_BEFORE_FILE = "baseline_before_june_update.json"
BASELINE_AFTER_UPDATE_FILE = "baseline_after_june_training_update.json"
BASELINE_AFTER_REBUILD_FILE = "baseline_after_june_rebuild.json"
BASELINE_DIFF_FILE = "baseline_change_diff.json"
DEBUG_ARTIFACT_FILES = (
    MAY_UPDATE_FILE,
    MAY_BUILD_FILE,
    JUNE_UPDATE_FILE,
    JUNE_BUILD_FILE,
    BASELINE_BEFORE_FILE,
    BASELINE_AFTER_UPDATE_FILE,
    BASELINE_AFTER_REBUILD_FILE,
)
MONTHLY_ARTIFACT_FILES = (
    REPORT_FILE,
    STATE_FILE,
    BASELINE_DIFF_FILE,
    *DEBUG_ARTIFACT_FILES,
)

COMPARE_FIELDS = (
    "full_baseline_json",
    "common_source_cities",
    "common_vpn_gateways",
    "action_distribution",
    "result_distribution",
    "protocol_distribution",
    "failed_rate",
    "off_hours_rate",
    "unusual_ip_rate",
    "avg_daily_events",
    "max_daily_events",
)
BASELINE_SELECT_COLUMNS = (
    "username",
    "sample_count",
    "common_source_cities",
    "common_vpn_gateways",
    "action_distribution",
    "result_distribution",
    "protocol_distribution",
    "failed_rate",
    "off_hours_rate",
    "unusual_ip_rate",
    "avg_daily_events",
    "max_daily_events",
    "baseline_json",
    "created_at",
)


@dataclass(slots=True)
class CommandResult:
    """Structured result for a subprocess-backed acceptance step."""

    success: bool
    stage: str
    command: list[str]
    returncode: int
    cli_success: bool
    duration_seconds: float
    payload: dict[str, Any]
    error: str | None = None
    stdout: str | None = None
    stderr: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "success": self.success,
            "stage": self.stage,
            "command": self.command,
            "returncode": self.returncode,
            "cli_success": self.cli_success,
            "duration_seconds": self.duration_seconds,
            "payload": self.payload,
            "error": self.error,
            "stdout": self.stdout,
            "stderr": self.stderr,
        }


def build_update_training_command(
    config: AcceptanceConfig,
    *,
    start_time: str,
    end_time: str,
    baseline_purpose: str,
    import_batch_id: str,
) -> list[str]:
    """Build the official training-table update CLI command."""
    return [
        sys.executable,
        str(UPDATE_SCRIPT_PATH),
        "--mode",
        "replace",
        "--dataset-id",
        DATASET_ID,
        "--baseline-purpose",
        baseline_purpose,
        "--import-batch-id",
        import_batch_id,
        "--start-time",
        start_time,
        "--end-time",
        end_time,
        "--log-type",
        config.log_type,
        "--created-by",
        "acceptance",
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
    ]


def build_training_baseline_command(
    config: AcceptanceConfig,
    *,
    start_time: str,
    end_time: str,
    model_version: str,
) -> list[str]:
    """Build the official baseline CLI command against the training table."""
    return [
        sys.executable,
        str(BUILD_SCRIPT_PATH),
        "--source-table",
        "ueba_baseline_training_logs",
        "--dataset-id",
        DATASET_ID,
        "--active-only",
        "--start-time",
        start_time,
        "--end-time",
        end_time,
        "--log-type",
        config.log_type,
        "--model-version",
        model_version,
        "--min-sample-count",
        str(config.min_sample_count),
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
    ]


def run_monthly_training_update(
    config: AcceptanceConfig | None = None,
    *,
    client_factory: Callable[[AcceptanceConfig], Any] = create_clickhouse_client,
    command_runner: Callable[[list[str], str], dict[str, Any]] | None = None,
    debug_artifacts: bool = False,
) -> dict[str, Any]:
    """Run the full monthly training-table update acceptance flow."""
    config = config or AcceptanceConfig()
    output_dir = ensure_output_dir(config)
    cleanup_monthly_artifacts(output_dir)
    command_runner = command_runner or run_json_command
    failed_checks: list[str] = []
    context: dict[str, Any] = _empty_context(config)
    client = None

    try:
        load_result = load_fixture_to_clickhouse(config, client_factory=client_factory)
        context["fixture_load_result"] = load_result
        _check(load_result.get("success") is True, "fixture_load_success", failed_checks)
        if failed_checks:
            return _finish(config, context, failed_checks, debug_artifacts=debug_artifacts)

        client = client_factory(config)
        cleanup_acceptance_baselines(client, config)
        context["fixture_stats"] = fetch_fixture_stats(client, config)
        _validate_fixture_stats(context, failed_checks)

        may_update = replace_training_table_from_fixture_logs(
            client,
            config,
            start_time=MAY_START,
            end_time=MAY_END,
            baseline_purpose="initial_build",
            import_batch_id="may_initial_2026_05",
            stage="may_training_update",
        )
        _write_debug_artifact(output_dir, MAY_UPDATE_FILE, may_update, debug_artifacts)
        context["may_training_update_result"] = may_update
        _check(may_update.get("success") is True, "may_training_update_success", failed_checks)
        context["may_training_stats"] = fetch_training_table_stats(client, config, MAY_START, MAY_END)
        _validate_training_stats(context["may_training_stats"], MAY_START, MAY_END, "may_training", failed_checks)

        may_build = command_runner(
            build_training_baseline_command(
                config,
                start_time=MAY_START,
                end_time=MAY_END,
                model_version=MAY_MODEL_VERSION,
            ),
            "may_baseline_build",
        )
        _write_debug_artifact(output_dir, MAY_BUILD_FILE, may_build, debug_artifacts)
        context["may_baseline_build_result"] = may_build
        _check(may_build.get("success") is True, "may_baseline_build_success", failed_checks)
        before = fetch_baseline_snapshot(client, config, MAY_MODEL_VERSION)
        _write_debug_artifact(output_dir, BASELINE_BEFORE_FILE, before, debug_artifacts)
        context["baseline_before_june_update"] = before
        _validate_baseline_snapshot(before, MAY_MODEL_VERSION, "may_baseline", failed_checks)

        june_update = replace_training_table_from_fixture_logs(
            client,
            config,
            start_time=JUNE_START,
            end_time=JUNE_END,
            baseline_purpose="manual_update",
            import_batch_id="june_manual_update_2026_06",
            stage="june_training_update",
        )
        _write_debug_artifact(output_dir, JUNE_UPDATE_FILE, june_update, debug_artifacts)
        context["june_training_update_result"] = june_update
        _check(june_update.get("success") is True, "june_training_update_success", failed_checks)

        after_update = fetch_baseline_snapshot(client, config, MAY_MODEL_VERSION)
        _write_debug_artifact(output_dir, BASELINE_AFTER_UPDATE_FILE, after_update, debug_artifacts)
        context["baseline_after_june_training_update"] = after_update
        unchanged = baseline_fingerprint(before) == baseline_fingerprint(after_update)
        context["baseline_unchanged_after_training_update"] = unchanged
        _check(unchanged, "baseline_unchanged_after_training_update", failed_checks)
        june_prebuild = fetch_baseline_snapshot(client, config, JUNE_MODEL_VERSION)
        _check(june_prebuild["row_count"] == 0, "no_june_baseline_before_rebuild", failed_checks)

        context["june_training_stats"] = fetch_training_table_stats(client, config, JUNE_START, JUNE_END)
        _validate_training_stats(context["june_training_stats"], JUNE_START, JUNE_END, "june_training", failed_checks)
        replaced = bool(context["june_training_stats"].get("replaced_by_window"))
        context["training_table_replaced_by_june"] = replaced
        _check(replaced, "training_table_replaced_by_june", failed_checks)

        june_build = command_runner(
            build_training_baseline_command(
                config,
                start_time=JUNE_START,
                end_time=JUNE_END,
                model_version=JUNE_MODEL_VERSION,
            ),
            "june_baseline_build",
        )
        _write_debug_artifact(output_dir, JUNE_BUILD_FILE, june_build, debug_artifacts)
        context["june_baseline_build_result"] = june_build
        _check(june_build.get("success") is True, "june_baseline_build_success", failed_checks)
        after_rebuild = fetch_baseline_snapshot(client, config, JUNE_MODEL_VERSION)
        _write_debug_artifact(output_dir, BASELINE_AFTER_REBUILD_FILE, after_rebuild, debug_artifacts)
        context["baseline_after_june_rebuild"] = after_rebuild
        _validate_baseline_snapshot(after_rebuild, JUNE_MODEL_VERSION, "june_baseline", failed_checks)

        diff = diff_baseline_snapshots(before, after_rebuild)
        write_json(output_dir / BASELINE_DIFF_FILE, diff)
        context["baseline_change_diff"] = diff
        changed = int(diff.get("changed_user_count") or 0) > 0
        context["baseline_changed_after_rebuild"] = changed
        _check(changed, "baseline_changed_after_rebuild", failed_checks)
        return _finish(config, context, failed_checks, debug_artifacts=debug_artifacts)
    except Exception as exc:
        failed_checks.append(f"unexpected_error:{type(exc).__name__}:{exc}")
        return _finish(config, context, failed_checks, debug_artifacts=debug_artifacts)
    finally:
        if client is not None and hasattr(client, "close"):
            try:
                client.close()
            except Exception:
                pass






def cleanup_acceptance_baselines(client: Any, config: AcceptanceConfig) -> None:
    """Remove prior fixture baselines for this acceptance's model versions."""
    BaselineStore(client=client, database=config.clickhouse_database).ensure_table()
    database = validate_identifier(config.clickhouse_database)
    sql = f"""
    ALTER TABLE {database}.user_behavior_baselines
    DELETE
    WHERE username LIKE 'fixture_user_%%'
      AND model_version IN (%(may_model_version)s, %(june_model_version)s)
    SETTINGS mutations_sync = 1
    """
    _execute_command(
        client,
        sql,
        {"may_model_version": MAY_MODEL_VERSION, "june_model_version": JUNE_MODEL_VERSION},
    )


def replace_training_table_from_fixture_logs(
    client: Any,
    config: AcceptanceConfig,
    *,
    start_time: str,
    end_time: str,
    baseline_purpose: str,
    import_batch_id: str,
    stage: str,
) -> dict[str, Any]:
    """Replace the acceptance dataset with fixture_user_% logs only."""
    begin = time.time()
    store = TrainingLogStore(client=client, database=config.clickhouse_database)
    store.ensure_table()
    store.delete_dataset(DATASET_ID)
    selected_rows = count_fixture_source_rows(client, config, start_time, end_time)
    _insert_fixture_training_rows(
        client,
        config,
        start_time=start_time,
        end_time=end_time,
        baseline_purpose=baseline_purpose,
        import_batch_id=import_batch_id,
    )
    target_rows = count_training_dataset_rows(client, config)
    success = selected_rows == target_rows and selected_rows >= 30000
    return {
        "success": success,
        "stage": stage,
        "mode": "replace",
        "dataset_id": DATASET_ID,
        "baseline_purpose": baseline_purpose,
        "import_batch_id": import_batch_id,
        "source_table": TrainingLogStore.SOURCE_TABLE,
        "target_table": TrainingLogStore.TARGET_TABLE,
        "source_filter": "username LIKE 'fixture_user_%'",
        "selected_rows": selected_rows,
        "inserted_rows": selected_rows,
        "target_rows": target_rows,
        "start_time": start_time,
        "end_time": end_time,
        "log_type": config.log_type,
        "duration_seconds": round(time.time() - begin, 3),
        "message": f"replace fixture dataset {DATASET_ID}: inserted {selected_rows} rows",
        "error": None if success else "selected_rows and target_rows mismatch or below threshold",
    }


def count_fixture_source_rows(client: Any, config: AcceptanceConfig, start_time: str, end_time: str) -> int:
    database = validate_identifier(config.clickhouse_database)
    sql = f"""
    SELECT count() AS cnt
    FROM {database}.logs_structured
    WHERE username LIKE 'fixture_user_%%'
      AND log_type = %(log_type)s
      AND timestamp >= %(start_time)s
      AND timestamp < %(end_time)s
    """
    rows = _query_rows(client, sql, {"log_type": config.log_type, "start_time": start_time, "end_time": end_time})
    return int((rows[0] if rows else {}).get("cnt") or 0)


def count_training_dataset_rows(client: Any, config: AcceptanceConfig) -> int:
    database = validate_identifier(config.clickhouse_database)
    sql = f"""
    SELECT count() AS cnt
    FROM {database}.ueba_baseline_training_logs
    WHERE dataset_id = %(dataset_id)s
    """
    rows = _query_rows(client, sql, {"dataset_id": DATASET_ID})
    return int((rows[0] if rows else {}).get("cnt") or 0)


def _insert_fixture_training_rows(
    client: Any,
    config: AcceptanceConfig,
    *,
    start_time: str,
    end_time: str,
    baseline_purpose: str,
    import_batch_id: str,
) -> None:
    database = validate_identifier(config.clickhouse_database)
    columns = ",\n            ".join(TrainingLogStore.INSERT_COLUMNS)
    sql = f"""
    INSERT INTO {database}.ueba_baseline_training_logs
    (
        {columns}
    )
    SELECT
        %(dataset_id)s AS dataset_id,
        %(baseline_purpose)s AS baseline_purpose,
        1 AS is_active,
        %(import_batch_id)s AS import_batch_id,
        id,
        timestamp,
        log_type,
        source,
        username,
        user_id,
        dept,
        role,
        action,
        event_type,
        result,
        fail_reason,
        source_ip,
        destination_ip,
        vpn_gateway,
        src_country,
        src_city,
        protocol,
        auth_method,
        client_software,
        user_agent,
        session_id,
        is_off_hours,
        is_unusual_ip,
        session_duration_sec,
        bytes_sent,
        bytes_recv,
        uri,
        method,
        status_code,
        response_time,
        detail,
        severity_level,
        device_info,
        location,
        request_id,
        raw_log,
        parser,
        parse_status,
        %(source_table)s AS source_table,
        toString(id) AS source_record_id,
        %(remark)s AS remark,
        %(created_by)s AS created_by
    FROM {database}.logs_structured
    WHERE username LIKE 'fixture_user_%%'
      AND log_type = %(log_type)s
      AND timestamp >= %(start_time)s
      AND timestamp < %(end_time)s
    """
    _execute_command(
        client,
        sql,
        {
            "dataset_id": DATASET_ID,
            "baseline_purpose": baseline_purpose,
            "import_batch_id": import_batch_id,
            "source_table": TrainingLogStore.SOURCE_TABLE,
            "remark": "monthly_training_update_acceptance",
            "created_by": "acceptance",
            "log_type": config.log_type,
            "start_time": start_time,
            "end_time": end_time,
        },
    )


def run_json_command(command: list[str], stage: str) -> dict[str, Any]:
    """Run a CLI command that prints one JSON object to stdout."""
    begin = time.time()
    redacted_command = _redact_command(command)
    try:
        completed = subprocess.run(command, cwd=PROJECT_ROOT, capture_output=True, text=True, check=False)
    except Exception as exc:
        return CommandResult(
            success=False,
            stage=stage,
            command=redacted_command,
            returncode=-1,
            cli_success=False,
            duration_seconds=round(time.time() - begin, 3),
            payload={},
            error=f"{type(exc).__name__}: {exc}",
        ).to_dict()

    stdout = completed.stdout or ""
    stderr = completed.stderr or ""
    payload, parse_error = _parse_json(stdout)
    cli_success = bool(payload.get("success")) if payload else False
    success = completed.returncode == 0 and cli_success and parse_error is None
    error = None
    if parse_error:
        error = f"JSON parse error: {parse_error}"
    elif not success:
        error = str(payload.get("message") or stderr or f"returncode={completed.returncode}")
    return CommandResult(
        success=success,
        stage=stage,
        command=redacted_command,
        returncode=completed.returncode,
        cli_success=cli_success,
        duration_seconds=round(time.time() - begin, 3),
        payload=payload,
        error=error,
        stdout=stdout if not success else None,
        stderr=stderr if stderr else None,
    ).to_dict()


def fetch_fixture_stats(client: Any, config: AcceptanceConfig) -> dict[str, Any]:
    """Fetch May, June, and total fixture rows from logs_structured."""
    return {
        "may": fetch_log_window_stats(client, config, MAY_START, MAY_END),
        "june": fetch_log_window_stats(client, config, JUNE_START, JUNE_END),
        "total": fetch_log_window_stats(client, config, MAY_START, JUNE_END),
    }


def fetch_log_window_stats(client: Any, config: AcceptanceConfig, start_time: str, end_time: str) -> dict[str, Any]:
    database = validate_identifier(config.clickhouse_database)
    sql = f"""
    SELECT
        count() AS rows,
        uniqExact(username) AS users,
        min(timestamp) AS min_timestamp,
        max(timestamp) AS max_timestamp
    FROM {database}.logs_structured
    WHERE username LIKE 'fixture_user_%%'
      AND log_type = %(log_type)s
      AND timestamp >= %(start_time)s
      AND timestamp < %(end_time)s
    """
    rows = _query_rows(client, sql, {"log_type": config.log_type, "start_time": start_time, "end_time": end_time})
    return _stats_row(rows[0] if rows else {})


def fetch_training_table_stats(client: Any, config: AcceptanceConfig, start_time: str, end_time: str) -> dict[str, Any]:
    database = validate_identifier(config.clickhouse_database)
    sql = f"""
    SELECT
        count() AS rows,
        uniqExact(username) AS users,
        min(timestamp) AS min_timestamp,
        max(timestamp) AS max_timestamp,
        countIf(timestamp < %(start_time)s) AS rows_before_window,
        countIf(timestamp >= %(end_time)s) AS rows_after_window,
        countIf(is_active = 1) AS active_rows
    FROM {database}.ueba_baseline_training_logs
    WHERE dataset_id = %(dataset_id)s
      AND log_type = %(log_type)s
    """
    rows = _query_rows(
        client,
        sql,
        {"dataset_id": DATASET_ID, "log_type": config.log_type, "start_time": start_time, "end_time": end_time},
    )
    stats = _stats_row(rows[0] if rows else {})
    stats["rows_before_window"] = int((rows[0] if rows else {}).get("rows_before_window") or 0)
    stats["rows_after_window"] = int((rows[0] if rows else {}).get("rows_after_window") or 0)
    stats["active_rows"] = int((rows[0] if rows else {}).get("active_rows") or 0)
    stats["replaced_by_window"] = (
        stats["rows"] >= 30000
        and stats["users"] == config.expected_user_count
        and stats["rows_before_window"] == 0
        and stats["rows_after_window"] == 0
    )
    return stats


def fetch_baseline_snapshot(client: Any, config: AcceptanceConfig, model_version: str) -> dict[str, Any]:
    """Fetch a compact baseline snapshot for one model_version."""
    database = validate_identifier(config.clickhouse_database)
    sql = f"""
    SELECT {", ".join(BASELINE_SELECT_COLUMNS)}
    FROM {database}.user_behavior_baselines FINAL
    WHERE model_version = %(model_version)s
      AND username LIKE 'fixture_user_%%'
    ORDER BY username
    """
    rows = _query_rows(client, sql, {"model_version": model_version})
    users: dict[str, dict[str, Any]] = {}
    for row in rows:
        username = str(row.get("username") or "")
        users[username] = {
            "sample_count": int(row.get("sample_count") or 0),
            "common_source_cities": _json_ready(row.get("common_source_cities")),
            "common_vpn_gateways": _json_ready(row.get("common_vpn_gateways")),
            "action_distribution": _json_ready(row.get("action_distribution")),
            "result_distribution": _json_ready(row.get("result_distribution")),
            "protocol_distribution": _json_ready(row.get("protocol_distribution")),
            "failed_rate": float(row.get("failed_rate") or 0.0),
            "off_hours_rate": float(row.get("off_hours_rate") or 0.0),
            "unusual_ip_rate": float(row.get("unusual_ip_rate") or 0.0),
            "avg_daily_events": float(row.get("avg_daily_events") or 0.0),
            "max_daily_events": int(row.get("max_daily_events") or 0),
            "full_baseline_json": _json_ready(row.get("baseline_json")),
            "created_at": _json_ready(row.get("created_at")),
        }
    total_sample_count = sum(user["sample_count"] for user in users.values())
    snapshot = {
        "model_version": model_version,
        "row_count": len(users),
        "user_count": len(users),
        "total_sample_count": total_sample_count,
        "fingerprint": "",
        "users": dict(sorted(users.items())),
    }
    snapshot["fingerprint"] = baseline_fingerprint(snapshot)
    return snapshot


def baseline_fingerprint(snapshot: dict[str, Any]) -> str:
    """Return a stable fingerprint for behavioral baseline content."""
    users = snapshot.get("users", {})
    comparable = {
        username: {field: user.get(field) for field in ("sample_count",) + COMPARE_FIELDS}
        for username, user in sorted(users.items())
    }
    payload = {
        "model_version": snapshot.get("model_version"),
        "row_count": snapshot.get("row_count", 0),
        "total_sample_count": snapshot.get("total_sample_count", 0),
        "users": comparable,
    }
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def diff_baseline_snapshots(before: dict[str, Any], after: dict[str, Any]) -> dict[str, Any]:
    """Compare two baseline snapshots and summarize changed fields."""
    before_users = before.get("users", {})
    after_users = after.get("users", {})
    shared_users = sorted(set(before_users) & set(after_users))
    changed_fields: dict[str, int] = {}
    examples: list[dict[str, Any]] = []
    changed_user_count = 0
    for username in shared_users:
        user_changed_fields: list[str] = []
        for field in COMPARE_FIELDS:
            if before_users[username].get(field) != after_users[username].get(field):
                changed_fields[field] = changed_fields.get(field, 0) + 1
                user_changed_fields.append(field)
        if user_changed_fields:
            changed_user_count += 1
            if len(examples) < 5:
                examples.append({"username": username, "changed_fields": user_changed_fields})
    return {
        "changed_user_count": changed_user_count,
        "checked_user_count": len(shared_users),
        "changed_fields": dict(sorted(changed_fields.items())),
        "examples": examples,
        "before_fingerprint": baseline_fingerprint(before),
        "after_fingerprint": baseline_fingerprint(after),
    }


def build_report(config: AcceptanceConfig, context: dict[str, Any], failed_checks: list[str]) -> dict[str, Any]:
    """Build the final monthly acceptance report payload."""
    fixture_stats = context.get("fixture_stats", {})
    may_stats = fixture_stats.get("may", {})
    june_stats = fixture_stats.get("june", {})
    may_training = context.get("may_training_stats", {})
    june_training = context.get("june_training_stats", {})
    may_baseline = context.get("baseline_before_june_update", {})
    june_baseline = context.get("baseline_after_june_rebuild", {})
    diff = context.get("baseline_change_diff", {})
    success = len(failed_checks) == 0
    return {
        "success": success,
        "fixture_id": config.fixture_id,
        "model_versions": {"may": MAY_MODEL_VERSION, "june": JUNE_MODEL_VERSION},
        "dataset_id": DATASET_ID,
        "test_windows_note": TEST_WINDOWS_NOTE,
        "may_rows": int(may_stats.get("rows") or 0),
        "may_users": int(may_stats.get("users") or 0),
        "june_rows": int(june_stats.get("rows") or 0),
        "june_users": int(june_stats.get("users") or 0),
        "may_training_rows": int(may_training.get("rows") or 0),
        "june_training_rows": int(june_training.get("rows") or 0),
        "may_baseline_rows": int(may_baseline.get("row_count") or 0),
        "june_baseline_rows": int(june_baseline.get("row_count") or 0),
        "may_baseline_total_sample_count": int(may_baseline.get("total_sample_count") or 0),
        "june_baseline_total_sample_count": int(june_baseline.get("total_sample_count") or 0),
        "baseline_unchanged_after_training_update": bool(context.get("baseline_unchanged_after_training_update")),
        "training_table_replaced_by_june": bool(context.get("training_table_replaced_by_june")),
        "baseline_changed_after_rebuild": bool(context.get("baseline_changed_after_rebuild")),
        "changed_user_count": int(diff.get("changed_user_count") or 0),
        "failed_checks": failed_checks,
        "message": "monthly training update acceptance passed" if success else "monthly training update acceptance failed",
    }


def main(argv: list[str] | None = None) -> int:
    """CLI entrypoint for the one-shot monthly acceptance runner."""
    args = _parse_args(argv)
    config = AcceptanceConfig()
    if args.output_dir:
        config.output_dir = Path(args.output_dir)
    report = run_monthly_training_update(config, debug_artifacts=args.debug_artifacts)
    print(json.dumps(report, ensure_ascii=False, sort_keys=True, default=str))
    return 0 if report.get("success") is True else 1


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run UEBA monthly training-table update acceptance flow")
    parser.add_argument("--output-dir", help="override default .tox/ueba_baseline_acceptance output directory")
    parser.add_argument(
        "--debug-artifacts",
        action="store_true",
        help="write intermediate monthly acceptance JSON files for debugging",
    )
    return parser.parse_args(argv)


def _finish(
    config: AcceptanceConfig,
    context: dict[str, Any],
    failed_checks: list[str],
    *,
    debug_artifacts: bool = False,
) -> dict[str, Any]:
    output_dir = ensure_output_dir(config)
    report = build_report(config, context, failed_checks)
    state = build_state(config, context, report, failed_checks, debug_artifacts=debug_artifacts)
    write_json(output_dir / STATE_FILE, state)
    write_json(output_dir / REPORT_FILE, report)
    update_run_state(
        config,
        monthly_training_update_done=True,
        monthly_training_update_success=bool(report["success"]),
        monthly_training_update_report_path=str(output_dir / REPORT_FILE),
        monthly_training_update_state_path=str(output_dir / STATE_FILE),
        last_error=None if report["success"] else "; ".join(failed_checks),
    )
    return report


def build_state(
    config: AcceptanceConfig,
    context: dict[str, Any],
    report: dict[str, Any],
    failed_checks: list[str],
    *,
    debug_artifacts: bool = False,
) -> dict[str, Any]:
    """Build the consolidated state file that replaces default debug artifacts."""
    before = context.get("baseline_before_june_update", {})
    after_update = context.get("baseline_after_june_training_update", {})
    after_rebuild = context.get("baseline_after_june_rebuild", {})
    return {
        "fixture_id": config.fixture_id,
        "dataset_id": DATASET_ID,
        "test_windows_note": TEST_WINDOWS_NOTE,
        "model_versions": {"may": MAY_MODEL_VERSION, "june": JUNE_MODEL_VERSION},
        "success": report["success"],
        "failed_checks": failed_checks,
        "debug_artifacts": bool(debug_artifacts),
        "report_path": str(Path(config.output_dir) / REPORT_FILE),
        "baseline_change_diff_path": str(Path(config.output_dir) / BASELINE_DIFF_FILE),
        "fixture_load_result": context.get("fixture_load_result"),
        "fixture_stats": context.get("fixture_stats"),
        "may_training_update_result": context.get("may_training_update_result"),
        "may_training_stats": context.get("may_training_stats"),
        "may_baseline_build_result": context.get("may_baseline_build_result"),
        "june_training_update_result": context.get("june_training_update_result"),
        "june_training_stats": context.get("june_training_stats"),
        "june_baseline_build_result": context.get("june_baseline_build_result"),
        "baseline_before_june_update": _state_snapshot_summary(before),
        "baseline_after_june_training_update": {
            **_state_snapshot_summary(after_update),
            "unchanged": bool(context.get("baseline_unchanged_after_training_update")),
        },
        "baseline_after_june_rebuild": {
            **_state_snapshot_summary(after_rebuild),
            "changed": bool(context.get("baseline_changed_after_rebuild")),
        },
        "training_table_replaced_by_june": bool(context.get("training_table_replaced_by_june")),
        "baseline_change_diff": context.get("baseline_change_diff"),
    }


def cleanup_monthly_artifacts(output_dir: Path) -> None:
    """Remove only files owned by the monthly training update runner."""
    for filename in MONTHLY_ARTIFACT_FILES:
        path = output_dir / filename
        if path.is_file():
            path.unlink()


def _write_debug_artifact(output_dir: Path, filename: str, payload: Any, debug_artifacts: bool) -> None:
    if debug_artifacts:
        write_json(output_dir / filename, payload)


def _state_snapshot_summary(snapshot: dict[str, Any]) -> dict[str, Any]:
    return {
        "model_version": snapshot.get("model_version"),
        "fingerprint": snapshot.get("fingerprint"),
        "rows": int(snapshot.get("row_count") or 0),
        "user_count": int(snapshot.get("user_count") or 0),
        "total_sample_count": int(snapshot.get("total_sample_count") or 0),
    }


def _validate_fixture_stats(context: dict[str, Any], failed_checks: list[str]) -> None:
    stats = context.get("fixture_stats", {})
    _check(int(stats.get("may", {}).get("rows") or 0) >= 30000, "may_fixture_rows_ge_30000", failed_checks)
    _check(int(stats.get("june", {}).get("rows") or 0) >= 30000, "june_fixture_rows_ge_30000", failed_checks)
    _check(int(stats.get("total", {}).get("rows") or 0) >= 60000, "total_fixture_rows_ge_60000", failed_checks)
    _check(int(stats.get("may", {}).get("users") or 0) == 26, "may_fixture_users_26", failed_checks)
    _check(int(stats.get("june", {}).get("users") or 0) == 26, "june_fixture_users_26", failed_checks)
    _check(int(stats.get("total", {}).get("users") or 0) == 26, "total_fixture_users_26", failed_checks)


def _validate_training_stats(stats: dict[str, Any], start_time: str, end_time: str, label: str, failed_checks: list[str]) -> None:
    _check(int(stats.get("rows") or 0) >= 30000, f"{label}_rows_ge_30000", failed_checks)
    _check(int(stats.get("users") or 0) == 26, f"{label}_users_26", failed_checks)
    _check(int(stats.get("rows_before_window") or 0) == 0, f"{label}_no_rows_before_window", failed_checks)
    _check(int(stats.get("rows_after_window") or 0) == 0, f"{label}_no_rows_after_window", failed_checks)
    min_timestamp = stats.get("min_timestamp")
    max_timestamp = stats.get("max_timestamp")
    _check(bool(min_timestamp) and str(min_timestamp) >= start_time, f"{label}_min_timestamp_in_window", failed_checks)
    _check(bool(max_timestamp) and str(max_timestamp) < end_time, f"{label}_max_timestamp_in_window", failed_checks)


def _validate_baseline_snapshot(snapshot: dict[str, Any], model_version: str, label: str, failed_checks: list[str]) -> None:
    _check(snapshot.get("model_version") == model_version, f"{label}_model_version", failed_checks)
    _check(int(snapshot.get("row_count") or 0) == 26, f"{label}_rows_26", failed_checks)
    _check(int(snapshot.get("user_count") or 0) == 26, f"{label}_users_26", failed_checks)
    _check(int(snapshot.get("total_sample_count") or 0) >= 30000, f"{label}_samples_ge_30000", failed_checks)


def _check(condition: bool, name: str, failed_checks: list[str]) -> None:
    if not condition:
        failed_checks.append(name)




def _execute_command(client: Any, sql: str, parameters: dict[str, Any]) -> None:
    if hasattr(client, "command"):
        client.command(sql, parameters=parameters)
        return
    if hasattr(client, "execute"):
        client.execute(sql, parameters)
        return
    raise TypeError("client must provide command(...) or execute(...)")


def _query_rows(client: Any, sql: str, parameters: dict[str, Any]) -> list[dict[str, Any]]:
    if hasattr(client, "query"):
        result = client.query(sql, parameters=parameters)
    elif hasattr(client, "execute"):
        result = client.execute(sql, parameters)
    else:
        raise TypeError("client must provide query(...) or execute(...)")
    return _rows_to_dicts(result)


def _rows_to_dicts(result: Any) -> list[dict[str, Any]]:
    if hasattr(result, "named_results"):
        rows = result.named_results() if callable(result.named_results) else result.named_results
        return [dict(row) for row in rows]
    if hasattr(result, "result_rows") and hasattr(result, "column_names"):
        return [dict(zip(result.column_names, row)) for row in result.result_rows]
    if isinstance(result, list):
        return [dict(row) for row in result]
    raise TypeError("unsupported query result format")


def _stats_row(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "rows": int(row.get("rows") or 0),
        "users": int(row.get("users") or 0),
        "min_timestamp": _time_string(row.get("min_timestamp")),
        "max_timestamp": _time_string(row.get("max_timestamp")),
    }


def _time_string(value: Any) -> str | None:
    if value in (None, ""):
        return None
    if isinstance(value, datetime):
        return value.strftime("%Y-%m-%d %H:%M:%S")
    text = str(value)
    if "." in text:
        text = text.split(".", 1)[0]
    return text.replace("T", " ")[:19]


def _json_ready(value: Any) -> Any:
    if isinstance(value, datetime):
        return value.strftime("%Y-%m-%d %H:%M:%S")
    return value


def _parse_json(stdout: str) -> tuple[dict[str, Any], str | None]:
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


def _redact_command(command: list[str]) -> list[str]:
    redacted = list(command)
    for index, value in enumerate(redacted[:-1]):
        if value == "--clickhouse-password" and redacted[index + 1] != "":
            redacted[index + 1] = "***"
    return redacted


def _empty_context(config: AcceptanceConfig) -> dict[str, Any]:
    return {
        "fixture_id": config.fixture_id,
        "dataset_id": DATASET_ID,
        "model_versions": {"may": MAY_MODEL_VERSION, "june": JUNE_MODEL_VERSION},
        "baseline_unchanged_after_training_update": False,
        "training_table_replaced_by_june": False,
        "baseline_changed_after_rebuild": False,
    }


__all__ = [
    "BASELINE_DIFF_FILE",
    "DEBUG_ARTIFACT_FILES",
    "MONTHLY_ARTIFACT_FILES",
    "DATASET_ID",
    "JUNE_MODEL_VERSION",
    "MAY_MODEL_VERSION",
    "TEST_WINDOWS_NOTE",
    "build_report",
    "build_state",
    "build_training_baseline_command",
    "build_update_training_command",
    "cleanup_acceptance_baselines",
    "cleanup_monthly_artifacts",
    "baseline_fingerprint",
    "diff_baseline_snapshots",
    "fetch_baseline_snapshot",
    "replace_training_table_from_fixture_logs",
    "run_json_command",
    "run_monthly_training_update",
]


if __name__ == "__main__":
    raise SystemExit(main())
