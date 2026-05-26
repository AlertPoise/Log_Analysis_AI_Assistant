"""Validator for UEBA baseline acceptance expected-vs-actual checks."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from typing import Any
import json
import re
import time

from .clickhouse_writer import create_clickhouse_client, validate_identifier
from .config import AcceptanceConfig
from .report_writer import ensure_output_dir, read_json, update_run_state, write_json


ACTUAL_BASELINES_FILE = "actual_baselines.json"
BASELINES_TABLE = "user_behavior_baselines"
BUILD_RESULT_FILE = "build_result.json"
EXPECTED_FILE = "expected_baselines.json"
FAILED_DIFF_FILE = "failed_diff.json"
VALIDATION_REPORT_FILE = "validation_report.json"
DISTRIBUTION_FIELDS = (
    "result_distribution",
    "event_type_distribution",
    "action_distribution",
    "fail_reason_distribution",
    "auth_method_distribution",
    "client_software_distribution",
    "protocol_distribution",
)
FLOAT_FIELDS = (
    "failed_rate",
    "off_hours_rate",
    "unusual_ip_rate",
    "active_day_avg_events",
)
COMMON_FIELD_MAPPING = {
    "common_active_hours": "expected_common_active_hours",
    "common_source_ips": "source_ip_frequency",
    "common_destination_ips": "destination_ip_frequency",
    "common_source_countries": "src_country_frequency",
    "common_source_cities": "src_city_frequency",
    "common_vpn_gateways": "vpn_gateway_frequency",
}
SELECT_COLUMNS = [
    "username",
    "sample_count",
    "is_reliable",
    "common_active_hours",
    "common_source_ips",
    "common_destination_ips",
    "common_source_countries",
    "common_source_cities",
    "common_vpn_gateways",
    "action_distribution",
    "event_type_distribution",
    "result_distribution",
    "fail_reason_distribution",
    "auth_method_distribution",
    "client_software_distribution",
    "protocol_distribution",
    "failed_rate",
    "off_hours_rate",
    "unusual_ip_rate",
    "active_day_avg_events",
    "max_daily_events",
    "baseline_start_time",
    "baseline_end_time",
    "model_version",
    "created_at",
]


def validate_fixture_baselines(
    config: AcceptanceConfig,
    client_factory: Callable[[AcceptanceConfig], Any] = create_clickhouse_client,
) -> dict[str, Any]:
    """Execute expected-vs-actual baseline validation for fixture users."""
    begin = time.time()
    output_dir = ensure_output_dir(config)
    paths = _report_paths(output_dir)

    precheck_error = _precheck(output_dir)
    if precheck_error:
        report = _base_report(config, duration_seconds=0.0, error=precheck_error)
        failed_diff = [
            _diff("__global__", "precheck", True, False, precheck_error),
        ]
        report.update(
            {
                "success": False,
                "failed_items": 1,
                "checked_items": 1,
                "summary": {"precheck": "FAIL"},
            }
        )
        write_json(paths["validation_report"], report)
        write_json(paths["failed_diff"], failed_diff)
        write_json(paths["actual_baselines"], _empty_actual_baselines(config, precheck_error))
        _mark_validation_precheck_failed(config, paths, precheck_error)
        return report

    expected = read_json(output_dir / EXPECTED_FILE)
    try:
        actual = fetch_actual_baselines(config, client_factory=client_factory)
    except Exception as exc:
        error = f"{type(exc).__name__}: {exc}"
        report = _base_report(config, duration_seconds=round(time.time() - begin, 3), error=error)
        failed_diff = [_diff("__global__", "fetch_actual_baselines", "query success", error, error)]
        report.update(
            {
                "success": False,
                "failed_items": 1,
                "checked_items": 1,
                "summary": {"fetch_actual_baselines": "FAIL"},
            }
        )
        write_json(paths["validation_report"], report)
        write_json(paths["failed_diff"], failed_diff)
        write_json(paths["actual_baselines"], _empty_actual_baselines(config, error))
        _mark_validation_precheck_failed(config, paths, error)
        return report

    write_json(paths["actual_baselines"], actual)
    report = compare_expected_and_actual(expected, actual, config)
    report["duration_seconds"] = round(time.time() - begin, 3)
    failed_diff = report.pop("failed_diff")
    write_json(paths["validation_report"], report)
    write_json(paths["failed_diff"], failed_diff)

    update_run_state(
        config,
        comparison_done=True,
        validation_success=bool(report["success"]),
        actual_baselines_path=str(paths["actual_baselines"]),
        validation_report_path=str(paths["validation_report"]),
        failed_diff_path=str(paths["failed_diff"]),
        last_error=report.get("error"),
    )
    return report


def fetch_actual_baselines(
    config: AcceptanceConfig,
    client_factory: Callable[[AcceptanceConfig], Any] = create_clickhouse_client,
) -> dict[str, Any]:
    """Fetch fixture_user_% baselines from ClickHouse using FINAL semantics."""
    database = validate_identifier(config.clickhouse_database)
    client = None
    try:
        client = client_factory(config)
        sql = f"""
        SELECT
            {", ".join(SELECT_COLUMNS)}
        FROM {database}.{BASELINES_TABLE} FINAL
        WHERE model_version = %(model_version)s
          AND username LIKE 'fixture_user_%%'
        ORDER BY username
        """
        rows = _query_rows(
            client,
            sql,
            {
                "model_version": config.model_version,
            },
        )
    finally:
        if client is not None and hasattr(client, "close"):
            try:
                client.close()
            except Exception:
                pass

    users = {_normalize_username(row): _actual_user_from_row(row) for row in rows}
    total_sample_count = sum(int(user["sample_count"]) for user in users.values())
    return {
        "model_version": config.model_version,
        "database": config.clickhouse_database,
        "table": BASELINES_TABLE,
        "read_consistency": "FINAL",
        "baseline_start_time": config.start_time,
        "baseline_end_time": config.end_time,
        "user_filter": "username LIKE 'fixture_user_%'",
        "user_count": len(users),
        "total_sample_count": total_sample_count,
        "users": dict(sorted(users.items())),
    }


def compare_expected_and_actual(
    expected: dict[str, Any],
    actual: dict[str, Any],
    config: AcceptanceConfig,
) -> dict[str, Any]:
    """Compare expected fixture baselines with actual database baselines."""
    failures: list[dict[str, Any]] = []
    summary: dict[str, str] = {}
    counts = {"checked": 0, "passed": 0}
    expected_users = expected.get("users", {})
    actual_users = actual.get("users", {})

    def check(username: str, field: str, passed: bool, expected_value: Any, actual_value: Any, message: str) -> None:
        counts["checked"] += 1
        summary[field] = "FAIL" if not passed else summary.get(field, "PASS")
        if passed:
            counts["passed"] += 1
            return
        failures.append(_diff(username, field, expected_value, actual_value, message))

    check(
        "__global__",
        "user_count",
        int(expected.get("user_count", 0)) == int(actual.get("user_count", 0)),
        expected.get("user_count"),
        actual.get("user_count"),
        "fixture 用户数不匹配，检查是否只读取 fixture_user_% 且使用 FINAL/latest 口径",
    )
    check(
        "__global__",
        "total_sample_count",
        int(expected.get("total_logs", 0)) == int(actual.get("total_sample_count", 0)),
        expected.get("total_logs"),
        actual.get("total_sample_count"),
        "fixture 样本总数不匹配，检查入库数量、时间窗口和 log_type",
    )

    expected_names = set(expected_users)
    actual_names = set(actual_users)
    for username in sorted(expected_names - actual_names):
        check(username, "missing_user", False, "present", "missing", "expected 用户未出现在实际 baseline 中")
    for username in sorted(actual_names - expected_names):
        check(username, "unexpected_user", False, "absent", "present", "实际结果出现非 expected 的 fixture 用户")

    for username in sorted(expected_names & actual_names):
        expected_user = expected_users[username]
        actual_user = actual_users[username]
        _compare_user(username, expected_user, actual_user, config, check)

    failed_items = len(failures)
    success = failed_items == 0
    return {
        "success": success,
        "fixture_id": expected.get("fixture_id", config.fixture_id),
        "model_version": config.model_version,
        "checked_users": len(expected_names & actual_names),
        "expected_user_count": int(expected.get("user_count", 0)),
        "actual_user_count": int(actual.get("user_count", 0)),
        "expected_total_logs": int(expected.get("total_logs", 0)),
        "actual_total_sample_count": int(actual.get("total_sample_count", 0)),
        "checked_items": counts["checked"],
        "passed_items": counts["passed"],
        "failed_items": failed_items,
        "duration_seconds": 0.0,
        "summary": dict(sorted(summary.items())),
        "error": None if success else f"{failed_items} validation checks failed",
        "failed_diff": failures,
    }


def extract_common_values(value: Any) -> set[str]:
    """Extract comparable common values from JSON/list/dict CountRatio shapes."""
    parsed = _parse_json_if_needed(value)
    values: set[str] = set()
    if isinstance(parsed, dict):
        for key, item in parsed.items():
            if isinstance(item, dict) and "value" in item:
                values.add(_value_key(item.get("value")))
            else:
                values.add(_value_key(key))
        return values
    if isinstance(parsed, list):
        for item in parsed:
            if isinstance(item, dict):
                values.add(_value_key(item.get("value")))
            else:
                values.add(_value_key(item))
        return values
    if parsed not in (None, ""):
        values.add(_value_key(parsed))
    return values


def _compare_user(
    username: str,
    expected_user: dict[str, Any],
    actual_user: dict[str, Any],
    config: AcceptanceConfig,
    check: Callable[[str, str, bool, Any, Any, str], None],
) -> None:
    check(
        username,
        "sample_count",
        int(expected_user.get("sample_count", 0)) == int(actual_user.get("sample_count", 0)),
        expected_user.get("sample_count"),
        actual_user.get("sample_count"),
        "sample_count 不一致，检查入库数量、时间窗口、log_type 与 fixture_user_% 过滤",
    )
    check(
        username,
        "is_reliable",
        bool(expected_user.get("is_reliable")) == bool(actual_user.get("is_reliable")),
        expected_user.get("is_reliable"),
        actual_user.get("is_reliable"),
        "基线可靠性不匹配，重点检查 min_sample_count",
    )

    for field in FLOAT_FIELDS:
        expected_value = float(expected_user.get(field, 0.0) or 0.0)
        actual_value = float(actual_user.get(field, 0.0) or 0.0)
        check(
            username,
            field,
            abs(actual_value - expected_value) <= config.validation_float_tolerance,
            expected_value,
            actual_value,
            f"{field} 超出容差，检查聚合口径或 fixture 生成逻辑",
        )

    check(
        username,
        "max_daily_events",
        int(expected_user.get("max_daily_events", 0)) == int(actual_user.get("max_daily_events", 0)),
        expected_user.get("max_daily_events"),
        actual_user.get("max_daily_events"),
        "max_daily_events 不一致，检查按日聚合口径",
    )

    sample_count = int(actual_user.get("sample_count", expected_user.get("sample_count", 0)) or 0)
    for field in DISTRIBUTION_FIELDS:
        expected_counts = _number_dict(expected_user.get(field, {}), force_int=True)
        actual_counts = _distribution_counts(actual_user.get(field, {}), sample_count)
        check(
            username,
            field,
            expected_counts == actual_counts,
            expected_counts,
            actual_counts,
            f"{field} 不一致，检查 key 集合、count 或 FAILED/FAIL/LOGIN_FAIL 统计口径",
        )

    for actual_field, expected_field in COMMON_FIELD_MAPPING.items():
        expected_values = _required_common_values(expected_user, expected_field, config)
        actual_values = extract_common_values(actual_user.get(actual_field, []))
        check(
            username,
            actual_field,
            expected_values.issubset(actual_values),
            sorted(expected_values),
            sorted(actual_values),
            f"{actual_field} 缺少 expected 高频值，检查 top_n / min_ratio / expected 频率",
        )


def _actual_user_from_row(row: dict[str, Any]) -> dict[str, Any]:
    sample_count = int(row.get("sample_count") or 0)
    user = {
        "sample_count": sample_count,
        "is_reliable": _to_bool(row.get("is_reliable")),
        "failed_rate": float(row.get("failed_rate") or 0.0),
        "off_hours_rate": float(row.get("off_hours_rate") or 0.0),
        "unusual_ip_rate": float(row.get("unusual_ip_rate") or 0.0),
        "active_day_avg_events": float(row.get("active_day_avg_events") or 0.0),
        "max_daily_events": int(row.get("max_daily_events") or 0),
        "created_at": _json_ready(row.get("created_at")),
        "baseline_start_time": _json_ready(row.get("baseline_start_time")),
        "baseline_end_time": _json_ready(row.get("baseline_end_time")),
    }
    for field in (
        "common_active_hours",
        "common_source_ips",
        "common_destination_ips",
        "common_source_countries",
        "common_source_cities",
        "common_vpn_gateways",
    ):
        user[field] = sorted(extract_common_values(row.get(field, [])), key=str)
    for field in DISTRIBUTION_FIELDS:
        user[field] = _distribution_counts(row.get(field, {}), sample_count)
    return user


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


def _precheck(output_dir: Path) -> str | None:
    expected_path = output_dir / EXPECTED_FILE
    build_result_path = output_dir / BUILD_RESULT_FILE
    if not expected_path.exists():
        return "expected_baselines.json missing; please run menu item 1 or 2 first"
    if not build_result_path.exists():
        return "build_result.json missing or unsuccessful; please run menu item 3 first"
    try:
        build_result = read_json(build_result_path)
    except Exception as exc:
        return f"build_result.json unreadable: {type(exc).__name__}: {exc}"
    if build_result.get("success") is not True:
        return "build_result.json missing or unsuccessful; please run menu item 3 first"
    return None


def _empty_actual_baselines(config: AcceptanceConfig, error: str) -> dict[str, Any]:
    return {
        "model_version": config.model_version,
        "database": config.clickhouse_database,
        "table": BASELINES_TABLE,
        "read_consistency": "FINAL",
        "baseline_start_time": config.start_time,
        "baseline_end_time": config.end_time,
        "user_filter": "username LIKE 'fixture_user_%'",
        "user_count": 0,
        "total_sample_count": 0,
        "users": {},
        "error": error,
    }


def _base_report(config: AcceptanceConfig, duration_seconds: float, error: str | None) -> dict[str, Any]:
    return {
        "success": False,
        "fixture_id": config.fixture_id,
        "model_version": config.model_version,
        "checked_users": 0,
        "expected_user_count": 0,
        "actual_user_count": 0,
        "expected_total_logs": 0,
        "actual_total_sample_count": 0,
        "checked_items": 0,
        "passed_items": 0,
        "failed_items": 0,
        "duration_seconds": duration_seconds,
        "summary": {},
        "error": error,
    }


def _report_paths(output_dir: Path) -> dict[str, Path]:
    return {
        "actual_baselines": output_dir / ACTUAL_BASELINES_FILE,
        "validation_report": output_dir / VALIDATION_REPORT_FILE,
        "failed_diff": output_dir / FAILED_DIFF_FILE,
    }


def _mark_validation_precheck_failed(config: AcceptanceConfig, paths: dict[str, Path], error: str) -> None:
    update_run_state(
        config,
        comparison_done=False,
        validation_success=False,
        validation_report_path=str(paths["validation_report"]),
        actual_baselines_path=str(paths["actual_baselines"]),
        failed_diff_path=str(paths["failed_diff"]),
        last_error=error,
    )


def _diff(username: str, field: str, expected: Any, actual: Any, message: str) -> dict[str, Any]:
    return {
        "username": username,
        "field": field,
        "expected": expected,
        "actual": actual,
        "message": message,
    }


def _required_common_values(user: dict[str, Any], field: str, config: AcceptanceConfig) -> set[str]:
    if field == "expected_common_active_hours":
        return {_value_key(value) for value in user.get(field, [])}
    frequency = _number_dict(user.get(field, {}), force_int=False)
    sample_count = int(user.get("sample_count", 0) or 0)
    if sample_count <= 0:
        return set()
    required = {
        _value_key(key)
        for key, count in frequency.items()
        if float(count) / sample_count >= config.validation_common_min_ratio
    }
    return required


def _distribution_counts(value: Any, sample_count: int) -> dict[str, int]:
    raw = _number_dict(value, force_int=False)
    if not raw:
        return {}
    total = sum(float(number) for number in raw.values())
    values_are_ratios = sample_count > 0 and total <= 1.0001
    counts: dict[str, int] = {}
    for key, number in raw.items():
        numeric = float(number)
        counts[str(key)] = int(round(numeric * sample_count)) if values_are_ratios else int(round(numeric))
    return dict(sorted(counts.items()))


def _number_dict(value: Any, force_int: bool) -> dict[str, int | float]:
    parsed = _parse_json_if_needed(value)
    if not isinstance(parsed, dict):
        return {}
    result: dict[str, int | float] = {}
    for key, raw_value in parsed.items():
        if raw_value in (None, ""):
            continue
        try:
            number = float(raw_value)
        except (TypeError, ValueError):
            continue
        result[str(key)] = int(round(number)) if force_int else number
    return dict(sorted(result.items()))


def _parse_json_if_needed(value: Any) -> Any:
    if isinstance(value, str):
        stripped = value.strip()
        if stripped == "":
            return value
        try:
            return json.loads(stripped)
        except json.JSONDecodeError:
            return value
    return value


def _value_key(value: Any) -> str:
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value)


def _to_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes"}
    return bool(value)


def _normalize_username(row: dict[str, Any]) -> str:
    username = str(row.get("username", ""))
    if not re.fullmatch(r"fixture_user_[A-Za-z0-9_]+", username):
        return username
    return username


def _json_ready(value: Any) -> Any:
    if isinstance(value, datetime):
        return value.isoformat(sep=" ")
    return value


__all__ = [
    "ACTUAL_BASELINES_FILE",
    "FAILED_DIFF_FILE",
    "VALIDATION_REPORT_FILE",
    "compare_expected_and_actual",
    "extract_common_values",
    "fetch_actual_baselines",
    "validate_fixture_baselines",
]
