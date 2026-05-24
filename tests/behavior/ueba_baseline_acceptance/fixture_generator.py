"""Deterministic UEBA baseline acceptance fixture generator.

The generator creates logs_structured-compatible rows and expected baseline
statistics without connecting to ClickHouse. Raw logs are streamed and are not
written to disk unless the config explicitly enables dump_logs_jsonl.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any
import json

from .config import AcceptanceConfig
from .report_writer import ensure_output_dir, update_run_state, write_json


PARSER_NAME = "ueba_fixture_v1"
EDGE_SAMPLE_COUNTS = (5, 19, 20, 21)


@dataclass(frozen=True, slots=True)
class UserSpec:
    """Deterministic profile used to generate one fixture user."""

    username: str
    user_type: str
    sample_count: int
    hours: tuple[int, ...]
    source_ips: tuple[str, ...]
    source_ip_weights: tuple[int, ...]
    cities: tuple[str, ...]
    city_weights: tuple[int, ...]
    vpn_gateways: tuple[str, ...]
    vpn_gateway_weights: tuple[int, ...]
    results: tuple[str, ...]
    result_weights: tuple[int, ...]
    offhour_ratio: float = 0.0


def iter_fixture_logs(config: AcceptanceConfig) -> Iterator[dict[str, Any]]:
    """Yield logs_structured-compatible fixture rows according to config."""
    start_time = _parse_time(config.start_time)
    for user_index, spec in enumerate(_build_user_specs(config), start=1):
        result_values = _expand_weighted_values(spec.results, spec.result_weights, spec.sample_count)
        source_ip_values = _expand_weighted_values(spec.source_ips, spec.source_ip_weights, spec.sample_count)
        city_values = _expand_weighted_values(spec.cities, spec.city_weights, spec.sample_count)
        gateway_values = _expand_weighted_values(spec.vpn_gateways, spec.vpn_gateway_weights, spec.sample_count)
        hour_values = _hour_values(spec)

        for row_index in range(spec.sample_count):
            result = result_values[row_index]
            is_failure = result in {"FAILED", "FAIL"}
            active_hour = hour_values[row_index]
            timestamp = _timestamp_for(start_time, row_index, active_hour)
            source_ip = source_ip_values[row_index]
            city = city_values[row_index]
            gateway = gateway_values[row_index]
            is_off_hours = active_hour in {0, 1, 2, 3}
            session_duration = 240 + (row_index % 180) + user_index

            yield {
                "timestamp": timestamp.strftime("%Y-%m-%d %H:%M:%S"),
                "log_type": config.log_type,
                "username": spec.username,
                "source_ip": source_ip,
                "destination_ip": f"172.20.{user_index % 20}.{(row_index % 200) + 1}",
                "src_country": "中国",
                "src_city": city,
                "vpn_gateway": gateway,
                "action": "LOGIN",
                "event_type": "LOGIN_FAIL" if is_failure else "LOGIN_SUCCESS",
                "result": result,
                "fail_reason": "PASSWORD_ERROR" if is_failure else "",
                "auth_method": "password+mfa",
                "client_software": "OpenVPN Connect",
                "protocol": "SSLVPN",
                "session_duration_sec": session_duration,
                "bytes_sent": 2048 + row_index * 3 + user_index,
                "bytes_recv": 8192 + row_index * 5 + user_index,
                "is_off_hours": is_off_hours,
                "is_unusual_ip": False,
                "parser": PARSER_NAME,
                "raw_log": f"ueba fixture generated log fixture_id={config.fixture_id}",
            }


def generate_expected_baselines(config: AcceptanceConfig) -> tuple[dict[str, Any], dict[str, Any]]:
    """Generate expected_baselines and fixture_summary without database access."""
    users: dict[str, dict[str, Any]] = {}
    for log_row in iter_fixture_logs(config):
        username = str(log_row["username"])
        user = users.setdefault(username, _empty_expected_user(username))
        _accumulate_log(user, log_row)

    for user in users.values():
        _finalize_expected_user(user, config)

    total_logs = sum(int(user["sample_count"]) for user in users.values())
    expected = {
        "fixture_id": config.fixture_id,
        "seed": config.seed,
        "log_type": config.log_type,
        "start_time": config.start_time,
        "end_time": config.end_time,
        "total_logs": total_logs,
        "user_count": len(users),
        "min_sample_count": config.min_sample_count,
        "users": dict(sorted(users.items())),
    }
    summary = {
        "fixture_id": config.fixture_id,
        "seed": config.seed,
        "total_logs": total_logs,
        "user_count": len(users),
        "start_time": config.start_time,
        "end_time": config.end_time,
        "log_type": config.log_type,
        "model_version": config.model_version,
        "user_types": {
            "stable": config.stable_user_count,
            "multi_location": config.multi_location_user_count,
            "high_failure": config.high_failure_user_count,
            "offhour": config.offhour_user_count,
            "edge": len(config.edge_user_sample_counts),
        },
    }
    return expected, summary


def generate_fixture_outputs(config: AcceptanceConfig) -> dict[str, Any]:
    """Write expected_baselines.json, fixture_summary.json, and run_state.json."""
    output_dir = ensure_output_dir(config)
    expected, summary = generate_expected_baselines(config)
    expected_path = output_dir / "expected_baselines.json"
    summary_path = output_dir / "fixture_summary.json"
    write_json(expected_path, expected)
    write_json(summary_path, summary)

    logs_path: Path | None = None
    if config.dump_logs_jsonl:
        logs_path = output_dir / "fixture_logs.jsonl"
        with logs_path.open("w", encoding="utf-8") as file_obj:
            for log_row in iter_fixture_logs(config):
                file_obj.write(json.dumps(log_row, ensure_ascii=False, sort_keys=True) + "\n")

    state = {
        "fixture_id": config.fixture_id,
        "expected_generated": True,
        "expected_baselines_path": str(expected_path),
        "fixture_summary_path": str(summary_path),
        "fixture_logs_path": str(logs_path) if logs_path else None,
        "total_logs": expected["total_logs"],
        "user_count": expected["user_count"],
        "model_version": config.model_version,
        "clickhouse_loaded": False,
        "baseline_built": False,
        "comparison_done": False,
    }
    update_run_state(config, **state)
    return state


def _build_user_specs(config: AcceptanceConfig) -> list[UserSpec]:
    specs: list[UserSpec] = []
    specs.extend(_stable_specs(config))
    specs.extend(_multi_location_specs(config))
    specs.extend(_high_failure_specs(config))
    specs.extend(_offhour_specs(config))
    specs.extend(_edge_specs(config))
    return specs


def _stable_specs(config: AcceptanceConfig) -> list[UserSpec]:
    return [
        UserSpec(
            username=f"fixture_user_stable_{index:04d}",
            user_type="stable",
            sample_count=config.logs_per_main_user,
            hours=(9, 10, 14, 15),
            source_ips=(f"10.10.{index}.1", f"10.10.{index}.2"),
            source_ip_weights=(60, 40),
            cities=("北京",),
            city_weights=(100,),
            vpn_gateways=("vpn-gw-cn-01",),
            vpn_gateway_weights=(100,),
            results=("SUCCESS", "FAILED", "FAIL"),
            result_weights=(97, 2, 1),
        )
        for index in range(1, config.stable_user_count + 1)
    ]


def _multi_location_specs(config: AcceptanceConfig) -> list[UserSpec]:
    return [
        UserSpec(
            username=f"fixture_user_multi_{index:04d}",
            user_type="multi_location",
            sample_count=config.logs_per_main_user,
            hours=(9, 10, 14, 15),
            source_ips=tuple(f"10.20.{index}.{offset}" for offset in range(1, 6)),
            source_ip_weights=(30, 25, 20, 15, 10),
            cities=("北京", "上海", "深圳"),
            city_weights=(60, 30, 10),
            vpn_gateways=("vpn-gw-cn-01", "vpn-gw-cn-02"),
            vpn_gateway_weights=(70, 30),
            results=("SUCCESS", "FAILED", "FAIL"),
            result_weights=(97, 2, 1),
        )
        for index in range(1, config.multi_location_user_count + 1)
    ]


def _high_failure_specs(config: AcceptanceConfig) -> list[UserSpec]:
    return [
        UserSpec(
            username=f"fixture_user_failed_{index:04d}",
            user_type="high_failure",
            sample_count=config.logs_per_main_user,
            hours=(9, 10, 14, 15),
            source_ips=(f"10.30.{index}.1", f"10.30.{index}.2"),
            source_ip_weights=(60, 40),
            cities=("北京",),
            city_weights=(100,),
            vpn_gateways=("vpn-gw-cn-01",),
            vpn_gateway_weights=(100,),
            results=("SUCCESS", "FAILED", "FAIL"),
            result_weights=(85, 10, 5),
        )
        for index in range(1, config.high_failure_user_count + 1)
    ]


def _offhour_specs(config: AcceptanceConfig) -> list[UserSpec]:
    return [
        UserSpec(
            username=f"fixture_user_offhour_{index:04d}",
            user_type="offhour",
            sample_count=config.logs_per_main_user,
            hours=(9, 10, 14, 15),
            source_ips=(f"10.40.{index}.1", f"10.40.{index}.2"),
            source_ip_weights=(60, 40),
            cities=("北京",),
            city_weights=(100,),
            vpn_gateways=("vpn-gw-cn-01",),
            vpn_gateway_weights=(100,),
            results=("SUCCESS", "FAILED", "FAIL"),
            result_weights=(97, 2, 1),
            offhour_ratio=0.3,
        )
        for index in range(1, config.offhour_user_count + 1)
    ]


def _edge_specs(config: AcceptanceConfig) -> list[UserSpec]:
    return [
        UserSpec(
            username=f"fixture_user_edge_{sample_count:04d}",
            user_type="edge",
            sample_count=sample_count,
            hours=(9, 10, 14, 15),
            source_ips=(f"10.50.{sample_count}.1",),
            source_ip_weights=(100,),
            cities=("北京",),
            city_weights=(100,),
            vpn_gateways=("vpn-gw-cn-01",),
            vpn_gateway_weights=(100,),
            results=("SUCCESS",),
            result_weights=(100,),
        )
        for sample_count in config.edge_user_sample_counts
    ]


def _expand_weighted_values(values: tuple[str, ...], weights: tuple[int, ...], total: int) -> list[str]:
    raw_counts = [total * weight // sum(weights) for weight in weights]
    remainder = total - sum(raw_counts)
    for index in range(remainder):
        raw_counts[index % len(raw_counts)] += 1

    expanded: list[str] = []
    for value, count in zip(values, raw_counts):
        expanded.extend([value] * count)
    return expanded


def _hour_values(spec: UserSpec) -> list[int]:
    if spec.offhour_ratio > 0:
        offhour_count = round(spec.sample_count * spec.offhour_ratio)
        normal_count = spec.sample_count - offhour_count
        return _cycle_values(spec.hours, normal_count) + _cycle_values((0, 1, 2, 3), offhour_count)
    return _cycle_values(spec.hours, spec.sample_count)


def _cycle_values(values: tuple[int, ...], total: int) -> list[int]:
    return [values[index % len(values)] for index in range(total)]


def _timestamp_for(start_time: datetime, row_index: int, active_hour: int) -> datetime:
    day = row_index % 31
    minute = row_index % 60
    second = (row_index * 7) % 60
    return start_time + timedelta(days=day, hours=active_hour, minutes=minute, seconds=second)


def _parse_time(value: str) -> datetime:
    return datetime.strptime(value, "%Y-%m-%d %H:%M:%S")


def _empty_expected_user(username: str) -> dict[str, Any]:
    return {
        "username": username,
        "user_type": _user_type_from_username(username),
        "sample_count": 0,
        "failed_count": 0,
        "off_hours_count": 0,
        "unusual_ip_count": 0,
        "hour_frequency": Counter(),
        "source_ip_frequency": Counter(),
        "destination_ip_frequency": Counter(),
        "src_country_frequency": Counter(),
        "src_city_frequency": Counter(),
        "vpn_gateway_frequency": Counter(),
        "result_distribution": Counter(),
        "event_type_distribution": Counter(),
        "action_distribution": Counter(),
        "fail_reason_distribution": Counter(),
        "auth_method_distribution": Counter(),
        "client_software_distribution": Counter(),
        "protocol_distribution": Counter(),
        "daily_frequency": Counter(),
        "session_duration_total": 0,
        "bytes_sent_total": 0,
        "bytes_recv_total": 0,
    }


def _accumulate_log(user: dict[str, Any], log_row: dict[str, Any]) -> None:
    timestamp = _parse_time(str(log_row["timestamp"]))
    result = str(log_row["result"])
    fail_reason = str(log_row["fail_reason"])

    user["sample_count"] += 1
    user["failed_count"] += 1 if result in {"FAILED", "FAIL"} or log_row["event_type"] == "LOGIN_FAIL" else 0
    user["off_hours_count"] += 1 if bool(log_row["is_off_hours"]) else 0
    user["unusual_ip_count"] += 1 if bool(log_row["is_unusual_ip"]) else 0
    user["hour_frequency"][timestamp.hour] += 1
    user["source_ip_frequency"][str(log_row["source_ip"])] += 1
    user["destination_ip_frequency"][str(log_row["destination_ip"])] += 1
    user["src_country_frequency"][str(log_row["src_country"])] += 1
    user["src_city_frequency"][str(log_row["src_city"])] += 1
    user["vpn_gateway_frequency"][str(log_row["vpn_gateway"])] += 1
    user["result_distribution"][result] += 1
    user["event_type_distribution"][str(log_row["event_type"])] += 1
    user["action_distribution"][str(log_row["action"])] += 1
    if fail_reason:
        user["fail_reason_distribution"][fail_reason] += 1
    user["auth_method_distribution"][str(log_row["auth_method"])] += 1
    user["client_software_distribution"][str(log_row["client_software"])] += 1
    user["protocol_distribution"][str(log_row["protocol"])] += 1
    user["daily_frequency"][timestamp.strftime("%Y-%m-%d")] += 1
    user["session_duration_total"] += int(log_row["session_duration_sec"])
    user["bytes_sent_total"] += int(log_row["bytes_sent"])
    user["bytes_recv_total"] += int(log_row["bytes_recv"])


def _finalize_expected_user(user: dict[str, Any], config: AcceptanceConfig) -> None:
    sample_count = int(user["sample_count"])
    user["is_reliable"] = sample_count >= config.min_sample_count
    user["failed_rate"] = _ratio(user["failed_count"], sample_count)
    user["off_hours_rate"] = _ratio(user["off_hours_count"], sample_count)
    user["unusual_ip_rate"] = _ratio(user["unusual_ip_count"], sample_count)
    user["expected_common_active_hours"] = [
        hour for hour, count in sorted(user["hour_frequency"].items()) if _ratio(count, sample_count) >= 0.05
    ]
    user["active_days"] = len(user["daily_frequency"])
    user["active_day_avg_events"] = _ratio(sample_count, max(user["active_days"], 1))
    user["max_daily_events"] = max(user["daily_frequency"].values(), default=0)
    user["session_duration_avg"] = _ratio(user["session_duration_total"], sample_count)
    user["bytes_sent_avg"] = _ratio(user["bytes_sent_total"], sample_count)
    user["bytes_recv_avg"] = _ratio(user["bytes_recv_total"], sample_count)

    counter_keys = [
        "hour_frequency",
        "source_ip_frequency",
        "destination_ip_frequency",
        "src_country_frequency",
        "src_city_frequency",
        "vpn_gateway_frequency",
        "result_distribution",
        "event_type_distribution",
        "action_distribution",
        "fail_reason_distribution",
        "auth_method_distribution",
        "client_software_distribution",
        "protocol_distribution",
        "daily_frequency",
    ]
    for key in counter_keys:
        user[key] = dict(sorted(user[key].items(), key=lambda item: str(item[0])))


def _user_type_from_username(username: str) -> str:
    if "_stable_" in username:
        return "stable"
    if "_multi_" in username:
        return "multi_location"
    if "_failed_" in username:
        return "high_failure"
    if "_offhour_" in username:
        return "offhour"
    if "_edge_" in username:
        return "edge"
    return "unknown"


def _ratio(numerator: int | float, denominator: int | float) -> float:
    if denominator <= 0:
        return 0.0
    return round(float(numerator) / float(denominator), 6)


__all__ = [
    "generate_expected_baselines",
    "generate_fixture_outputs",
    "iter_fixture_logs",
]
