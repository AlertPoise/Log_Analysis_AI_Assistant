"""Deterministic UEBA baseline acceptance fixture generator.

The generator creates logs_structured-compatible rows and expected baseline
statistics without connecting to ClickHouse. Raw logs are streamed and are not
written to disk unless the config explicitly enables dump_logs_jsonl.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterator
from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any
import json

from .config import AcceptanceConfig
from .report_writer import ensure_output_dir, update_run_state, write_json


PARSER_NAME = "ueba_fixture_v2"
EDGE_SAMPLE_COUNTS = (5, 19, 20, 21)
DEFAULT_DAYS_IN_MONTH = 31

DEFAULT_AUTH_METHODS = ("password+mfa", "sso+mfa", "certificate")
DEFAULT_AUTH_METHOD_WEIGHTS = (70, 20, 10)
DEFAULT_CLIENT_SOFTWARES = ("OpenVPN Connect", "Cisco AnyConnect", "Windows VPN Client", "Tunnelblick")
DEFAULT_CLIENT_SOFTWARE_WEIGHTS = (50, 30, 15, 5)
DEFAULT_PROTOCOLS = ("SSLVPN", "IPSec", "WireGuard")
DEFAULT_PROTOCOL_WEIGHTS = (80, 15, 5)
DEFAULT_FAIL_REASONS = ("PASSWORD_ERROR", "MFA_DENIED", "ACCOUNT_LOCKED", "TIMEOUT")
DEFAULT_FAIL_REASON_WEIGHTS = (60, 20, 10, 10)


@dataclass(frozen=True, slots=True)
class UserSpec:
    """Deterministic profile used to generate one fixture user."""

    username: str
    user_type: str
    sample_count: int
    hours: tuple[int, ...]
    source_ips: tuple[str, ...]
    source_ip_weights: tuple[int, ...]
    countries: tuple[str, ...]
    country_weights: tuple[int, ...]
    cities: tuple[str, ...]
    city_weights: tuple[int, ...]
    vpn_gateways: tuple[str, ...]
    vpn_gateway_weights: tuple[int, ...]
    results: tuple[str, ...]
    result_weights: tuple[int, ...]
    actions: tuple[str, ...]
    action_weights: tuple[int, ...]
    auth_methods: tuple[str, ...] = DEFAULT_AUTH_METHODS
    auth_method_weights: tuple[int, ...] = DEFAULT_AUTH_METHOD_WEIGHTS
    client_softwares: tuple[str, ...] = DEFAULT_CLIENT_SOFTWARES
    client_software_weights: tuple[int, ...] = DEFAULT_CLIENT_SOFTWARE_WEIGHTS
    protocols: tuple[str, ...] = DEFAULT_PROTOCOLS
    protocol_weights: tuple[int, ...] = DEFAULT_PROTOCOL_WEIGHTS
    fail_reasons: tuple[str, ...] = DEFAULT_FAIL_REASONS
    fail_reason_weights: tuple[int, ...] = DEFAULT_FAIL_REASON_WEIGHTS
    destination_ips: tuple[str, ...] | None = None
    destination_ip_weights: tuple[int, ...] | None = None
    offhour_ratio: float = 0.0
    unusual_ip_ratio: float = 0.0
    burst_day_indexes: tuple[int, ...] = ()


def iter_fixture_logs(config: AcceptanceConfig) -> Iterator[dict[str, Any]]:
    """Yield logs_structured-compatible fixture rows according to config."""
    start_time = _parse_time(config.start_time)
    end_time = _parse_time(config.end_time)
    for month_index, month_start, month_end in _month_windows(start_time, end_time):
        days_in_window = max(1, (month_end - month_start).days)
        for user_index, base_spec in enumerate(_build_user_specs(config), start=1):
            spec = _monthly_variant_spec(base_spec, month_index)
            result_values = _expand_weighted_values(spec.results, spec.result_weights, spec.sample_count)
            failure_count = sum(1 for value in result_values if value in {"FAILED", "FAIL"})
            fail_reason_values = _expand_weighted_values(spec.fail_reasons, spec.fail_reason_weights, failure_count)
            source_ip_values = _expand_weighted_values(spec.source_ips, spec.source_ip_weights, spec.sample_count)
            country_values = _expand_weighted_values(spec.countries, spec.country_weights, spec.sample_count)
            city_values = _expand_weighted_values(spec.cities, spec.city_weights, spec.sample_count)
            gateway_values = _expand_weighted_values(spec.vpn_gateways, spec.vpn_gateway_weights, spec.sample_count)
            action_values = _expand_weighted_values(spec.actions, spec.action_weights, spec.sample_count)
            auth_method_values = _expand_weighted_values(spec.auth_methods, spec.auth_method_weights, spec.sample_count)
            client_values = _expand_weighted_values(spec.client_softwares, spec.client_software_weights, spec.sample_count)
            protocol_values = _expand_weighted_values(spec.protocols, spec.protocol_weights, spec.sample_count)
            destination_values = _destination_ip_values(spec, user_index)
            unusual_ip_values = _boolean_ratio_values(spec.unusual_ip_ratio, spec.sample_count)
            hour_values = _hour_values(spec)
            day_values = _day_values(spec, days_in_window)
            failure_index = 0

            for row_index in range(spec.sample_count):
                result = result_values[row_index]
                is_failure = result in {"FAILED", "FAIL"}
                action = action_values[row_index]
                active_hour = hour_values[row_index]
                timestamp = _timestamp_for(month_start, row_index, active_hour, day_values[row_index])
                is_off_hours = active_hour in {0, 1, 2, 3}
                if is_failure:
                    fail_reason = fail_reason_values[failure_index]
                    failure_index += 1
                else:
                    fail_reason = ""
                session_duration, bytes_sent, bytes_recv = _session_and_traffic_metrics(
                    row_index=row_index + month_index * spec.sample_count,
                    user_index=user_index,
                    is_failure=is_failure,
                    user_type=spec.user_type,
                )

                yield {
                    "timestamp": timestamp.strftime("%Y-%m-%d %H:%M:%S"),
                    "log_type": config.log_type,
                    "username": spec.username,
                    "source_ip": source_ip_values[row_index],
                    "destination_ip": destination_values[row_index],
                    "src_country": country_values[row_index],
                    "src_city": city_values[row_index],
                    "vpn_gateway": gateway_values[row_index],
                    "action": action,
                    "event_type": "LOGIN_FAIL" if is_failure else "LOGIN_SUCCESS",
                    "result": result,
                    "fail_reason": fail_reason,
                    "auth_method": auth_method_values[row_index],
                    "client_software": client_values[row_index],
                    "protocol": protocol_values[row_index],
                    "session_duration_sec": session_duration,
                    "bytes_sent": bytes_sent,
                    "bytes_recv": bytes_recv,
                    "is_off_hours": is_off_hours,
                    "is_unusual_ip": unusual_ip_values[row_index],
                    "parser": PARSER_NAME,
                    "raw_log": f"ueba fixture generated log fixture_id={config.fixture_id} month_index={month_index}",
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
            "ip_long_tail": config.ip_long_tail_user_count,
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


def _month_windows(start_time: datetime, end_time: datetime) -> Iterator[tuple[int, datetime, datetime]]:
    """Yield calendar-month windows within the configured half-open interval."""
    if start_time >= end_time:
        return
    current = start_time
    month_index = 0
    while current < end_time:
        next_month = _next_month_start(current)
        window_end = min(next_month, end_time)
        yield month_index, current, window_end
        current = window_end
        month_index += 1


def _next_month_start(value: datetime) -> datetime:
    if value.month == 12:
        return value.replace(year=value.year + 1, month=1, day=1, hour=0, minute=0, second=0, microsecond=0)
    return value.replace(month=value.month + 1, day=1, hour=0, minute=0, second=0, microsecond=0)


def _monthly_variant_spec(spec: UserSpec, month_index: int) -> UserSpec:
    """Apply stable June+ behavior shifts while keeping the same fixture users."""
    if month_index == 0:
        return spec
    if spec.user_type == "stable":
        return replace(
            spec,
            cities=(spec.cities[0], "深圳") if spec.cities else ("深圳",),
            city_weights=(75, 25) if spec.cities else (100,),
            vpn_gateways=("vpn-gw-cn-02", "vpn-gw-hk-01"),
            vpn_gateway_weights=(70, 30),
            results=("SUCCESS", "FAILED", "FAIL"),
            result_weights=(92, 5, 3),
            actions=("LOGIN", "REAUTH", "LOGOUT", "VPN_CONNECT"),
            action_weights=(72, 12, 6, 10),
            protocols=("SSLVPN", "IPSec", "WireGuard"),
            protocol_weights=(62, 25, 13),
            offhour_ratio=max(spec.offhour_ratio, 0.06),
            unusual_ip_ratio=max(spec.unusual_ip_ratio, 0.02),
        )
    if spec.user_type == "multi_location":
        return replace(
            spec,
            countries=("中国", "新加坡", "日本", "德国"),
            country_weights=(55, 20, 15, 10),
            cities=("上海", "新加坡", "东京", "法兰克福"),
            city_weights=(50, 22, 18, 10),
            vpn_gateways=("vpn-gw-cn-02", "vpn-gw-sg-01", "vpn-gw-hk-01"),
            vpn_gateway_weights=(45, 35, 20),
            results=("SUCCESS", "FAILED", "FAIL"),
            result_weights=(94, 4, 2),
            actions=("LOGIN", "VPN_CONNECT", "REAUTH", "LOGOUT"),
            action_weights=(66, 22, 8, 4),
            protocols=("SSLVPN", "IPSec", "WireGuard"),
            protocol_weights=(55, 25, 20),
            unusual_ip_ratio=max(spec.unusual_ip_ratio, 0.08),
        )
    if spec.user_type == "high_failure":
        return replace(
            spec,
            vpn_gateways=("vpn-gw-cn-02", "vpn-gw-hk-01"),
            vpn_gateway_weights=(50, 50),
            results=("SUCCESS", "FAILED", "FAIL"),
            result_weights=(75, 18, 7),
            fail_reasons=("MFA_DENIED", "PASSWORD_ERROR", "ACCOUNT_LOCKED", "TIMEOUT"),
            fail_reason_weights=(45, 30, 15, 10),
            actions=("LOGIN", "REAUTH", "VPN_CONNECT"),
            action_weights=(80, 10, 10),
            protocols=("SSLVPN", "IPSec", "WireGuard"),
            protocol_weights=(58, 30, 12),
            offhour_ratio=max(spec.offhour_ratio, 0.08),
            unusual_ip_ratio=max(spec.unusual_ip_ratio, 0.1),
        )
    if spec.user_type == "offhour":
        return replace(
            spec,
            hours=(8, 9, 15, 16),
            vpn_gateways=("vpn-gw-cn-01", "vpn-gw-cn-02"),
            vpn_gateway_weights=(55, 45),
            results=("SUCCESS", "FAILED", "FAIL"),
            result_weights=(93, 4, 3),
            actions=("LOGIN", "REAUTH", "VPN_CONNECT"),
            action_weights=(70, 15, 15),
            offhour_ratio=0.45,
            unusual_ip_ratio=max(spec.unusual_ip_ratio, 0.09),
        )
    if spec.user_type == "ip_long_tail":
        return replace(
            spec,
            cities=("杭州", "深圳", "上海"),
            city_weights=(45, 35, 20),
            vpn_gateways=("vpn-gw-hk-01", "vpn-gw-cn-02", "vpn-gw-sg-01"),
            vpn_gateway_weights=(45, 35, 20),
            results=("SUCCESS", "FAILED", "FAIL"),
            result_weights=(92, 5, 3),
            actions=("LOGIN", "VPN_CONNECT", "REAUTH", "LOGOUT"),
            action_weights=(68, 20, 8, 4),
            protocols=("SSLVPN", "IPSec", "WireGuard"),
            protocol_weights=(50, 28, 22),
            unusual_ip_ratio=max(spec.unusual_ip_ratio, 0.12),
            burst_day_indexes=tuple(day + 2 for day in spec.burst_day_indexes),
        )
    if spec.user_type == "edge":
        return replace(
            spec,
            vpn_gateways=("vpn-gw-cn-02",),
            vpn_gateway_weights=(100,),
            protocols=("IPSec",),
            protocol_weights=(100,),
            actions=("VPN_CONNECT",),
            action_weights=(100,),
            unusual_ip_ratio=0.1 if spec.sample_count >= 20 else 0.0,
        )
    return spec


def _build_user_specs(config: AcceptanceConfig) -> list[UserSpec]:
    specs: list[UserSpec] = []
    specs.extend(_stable_specs(config))
    specs.extend(_multi_location_specs(config))
    specs.extend(_high_failure_specs(config))
    specs.extend(_offhour_specs(config))
    specs.extend(_ip_long_tail_specs(config))
    specs.extend(_edge_specs(config))
    return specs


def _stable_specs(config: AcceptanceConfig) -> list[UserSpec]:
    city_by_index = {
        1: "北京",
        2: "北京",
        3: "北京",
        4: "上海",
        5: "上海",
        6: "上海",
        7: "广州",
        8: "广州",
        9: "杭州",
        10: "成都",
    }
    specs: list[UserSpec] = []
    for index in range(1, config.stable_user_count + 1):
        city = city_by_index.get(index, ("北京", "上海", "广州", "杭州", "成都")[(index - 1) % 5])
        gateway = "vpn-gw-cn-02" if city == "上海" else "vpn-gw-cn-01"
        specs.append(
            UserSpec(
                username=f"fixture_user_stable_{index:04d}",
                user_type="stable",
                sample_count=config.logs_per_main_user,
                hours=(9, 10, 14, 15),
                source_ips=(f"10.10.{index}.1", f"10.10.{index}.2"),
                source_ip_weights=(60, 40),
                countries=("中国",),
                country_weights=(100,),
                cities=(city,),
                city_weights=(100,),
                vpn_gateways=(gateway,),
                vpn_gateway_weights=(100,),
                results=("SUCCESS", "FAILED", "FAIL"),
                result_weights=(97, 2, 1),
                actions=("LOGIN", "REAUTH", "LOGOUT"),
                action_weights=(85, 10, 5),
                destination_ips=_stable_destination_ips(index),
                destination_ip_weights=(40, 30, 10, 10, 10),
            )
        )
    return specs


def _multi_location_specs(config: AcceptanceConfig) -> list[UserSpec]:
    specs: list[UserSpec] = []
    for index in range(1, config.multi_location_user_count + 1):
        if index == 4:
            countries = ("中国", "新加坡", "日本", "德国")
            country_weights = (70, 10, 10, 10)
            cities = ("北京", "新加坡", "东京", "法兰克福")
            city_weights = (70, 10, 10, 10)
            gateways = ("vpn-gw-cn-01", "vpn-gw-sg-01", "vpn-gw-hk-01")
            gateway_weights = (70, 15, 15)
        else:
            countries = ("中国",)
            country_weights = (100,)
            cities = ("北京", "上海", "深圳", "广州")
            city_weights = (50, 25, 15, 10)
            gateways = ("vpn-gw-cn-01", "vpn-gw-cn-02", "vpn-gw-hk-01")
            gateway_weights = (55, 30, 15)
        specs.append(
            UserSpec(
                username=f"fixture_user_multi_{index:04d}",
                user_type="multi_location",
                sample_count=config.logs_per_main_user,
                hours=(9, 10, 14, 15),
                source_ips=tuple(f"10.20.{index}.{offset}" for offset in range(1, 6)),
                source_ip_weights=(30, 25, 20, 15, 10),
                countries=countries,
                country_weights=country_weights,
                cities=cities,
                city_weights=city_weights,
                vpn_gateways=gateways,
                vpn_gateway_weights=gateway_weights,
                results=("SUCCESS", "FAILED", "FAIL"),
                result_weights=(97, 2, 1),
                actions=("LOGIN", "VPN_CONNECT", "REAUTH"),
                action_weights=(80, 15, 5),
                destination_ips=_multi_destination_ips(index),
                destination_ip_weights=(35, 25, 15, 10, 8, 7),
                unusual_ip_ratio=0.03,
            )
        )
    return specs


def _high_failure_specs(config: AcceptanceConfig) -> list[UserSpec]:
    city_by_index = {1: "北京", 2: "上海", 3: "深圳"}
    return [
        UserSpec(
            username=f"fixture_user_failed_{index:04d}",
            user_type="high_failure",
            sample_count=config.logs_per_main_user,
            hours=(9, 10, 14, 15),
            source_ips=(f"10.30.{index}.1", f"10.30.{index}.2"),
            source_ip_weights=(60, 40),
            countries=("中国",),
            country_weights=(100,),
            cities=(city_by_index.get(index, "北京"),),
            city_weights=(100,),
            vpn_gateways=("vpn-gw-cn-01", "vpn-gw-cn-02"),
            vpn_gateway_weights=(60, 40),
            results=("SUCCESS", "FAILED", "FAIL"),
            result_weights=(85, 10, 5),
            actions=("LOGIN", "REAUTH"),
            action_weights=(95, 5),
            auth_methods=("password+mfa", "sso+mfa", "certificate", "password_only"),
            auth_method_weights=(60, 20, 10, 10),
            destination_ips=_failure_destination_ips(index),
            destination_ip_weights=(50, 25, 10, 8, 7),
            unusual_ip_ratio=0.05,
        )
        for index in range(1, config.high_failure_user_count + 1)
    ]


def _offhour_specs(config: AcceptanceConfig) -> list[UserSpec]:
    city_by_index = {1: "北京", 2: "广州", 3: "成都"}
    return [
        UserSpec(
            username=f"fixture_user_offhour_{index:04d}",
            user_type="offhour",
            sample_count=config.logs_per_main_user,
            hours=(9, 10, 14, 15),
            source_ips=(f"10.40.{index}.1", f"10.40.{index}.2"),
            source_ip_weights=(60, 40),
            countries=("中国",),
            country_weights=(100,),
            cities=(city_by_index.get(index, "北京"),),
            city_weights=(100,),
            vpn_gateways=("vpn-gw-cn-01",),
            vpn_gateway_weights=(100,),
            results=("SUCCESS", "FAILED", "FAIL"),
            result_weights=(97, 2, 1),
            actions=("LOGIN", "REAUTH"),
            action_weights=(85, 15),
            destination_ips=_offhour_destination_ips(index),
            destination_ip_weights=(45, 35, 10, 10),
            offhour_ratio=0.3,
            unusual_ip_ratio=0.05,
        )
        for index in range(1, config.offhour_user_count + 1)
    ]


def _ip_long_tail_specs(config: AcceptanceConfig) -> list[UserSpec]:
    specs: list[UserSpec] = []
    for index in range(1, config.ip_long_tail_user_count + 1):
        if index == 1:
            source_ips = tuple(f"10.60.1.{offset}" for offset in range(1, 6)) + tuple(
                f"100.64.1.{offset}" for offset in range(1, 101)
            )
            source_weights = (30, 30, 30, 30, 30) + tuple(1 for _ in range(100))
            destination_ips = tuple(f"172.30.1.{offset}" for offset in range(1, 6)) + tuple(
                f"172.31.1.{offset}" for offset in range(1, 81)
            )
            destination_weights = (35, 25, 20, 10, 10) + tuple(1 for _ in range(80))
            burst_days = (3, 4, 5)
        else:
            source_ips = tuple(f"100.65.{index}.{offset}" for offset in range(1, 301))
            source_weights = tuple(1 for _ in range(300))
            destination_ips = tuple(f"172.32.{index}.{offset}" for offset in range(1, 151))
            destination_weights = tuple(1 for _ in range(150))
            burst_days = (10, 11)
        specs.append(
            UserSpec(
                username=f"fixture_user_iptail_{index:04d}",
                user_type="ip_long_tail",
                sample_count=config.logs_per_main_user,
                hours=(8, 9, 10, 14, 15, 16),
                source_ips=source_ips,
                source_ip_weights=source_weights,
                countries=("中国",),
                country_weights=(100,),
                cities=("上海", "杭州", "深圳"),
                city_weights=(50, 30, 20),
                vpn_gateways=("vpn-gw-cn-02", "vpn-gw-hk-01"),
                vpn_gateway_weights=(70, 30),
                results=("SUCCESS", "FAILED", "FAIL"),
                result_weights=(96, 3, 1),
                actions=("LOGIN", "VPN_CONNECT", "REAUTH"),
                action_weights=(82, 12, 6),
                destination_ips=destination_ips,
                destination_ip_weights=destination_weights,
                unusual_ip_ratio=0.04,
                burst_day_indexes=burst_days,
            )
        )
    return specs


def _edge_specs(config: AcceptanceConfig) -> list[UserSpec]:
    return [
        UserSpec(
            username=f"fixture_user_edge_{sample_count:04d}",
            user_type="edge",
            sample_count=sample_count,
            hours=(9, 10, 14, 15),
            source_ips=(f"10.50.{sample_count}.1",),
            source_ip_weights=(100,),
            countries=("中国",),
            country_weights=(100,),
            cities=("北京",),
            city_weights=(100,),
            vpn_gateways=("vpn-gw-cn-01",),
            vpn_gateway_weights=(100,),
            results=("SUCCESS",),
            result_weights=(100,),
            actions=("LOGIN",),
            action_weights=(100,),
            auth_methods=("password+mfa",),
            auth_method_weights=(100,),
            client_softwares=("OpenVPN Connect",),
            client_software_weights=(100,),
            protocols=("SSLVPN",),
            protocol_weights=(100,),
            fail_reasons=("PASSWORD_ERROR",),
            fail_reason_weights=(100,),
            destination_ips=(f"172.20.50.{sample_count}",),
            destination_ip_weights=(100,),
        )
        for sample_count in config.edge_user_sample_counts
    ]


def _stable_destination_ips(index: int) -> tuple[str, ...]:
    return (
        "172.20.10.10",
        "172.20.10.20",
        f"172.20.{index}.30",
        f"172.21.{index}.101",
        f"172.21.{index}.102",
    )


def _multi_destination_ips(index: int) -> tuple[str, ...]:
    return (
        "172.20.20.10",
        "172.20.20.20",
        "172.20.20.30",
        f"172.22.{index}.10",
        f"172.22.{index}.11",
        f"172.22.{index}.12",
    )


def _failure_destination_ips(index: int) -> tuple[str, ...]:
    return (
        "172.20.30.10",
        "172.20.30.20",
        f"172.23.{index}.10",
        f"172.23.{index}.11",
        f"172.23.{index}.12",
    )


def _offhour_destination_ips(index: int) -> tuple[str, ...]:
    return (
        "172.20.40.10",
        "172.20.40.20",
        f"172.24.{index}.10",
        f"172.24.{index}.11",
    )


def _destination_ip_values(spec: UserSpec, user_index: int) -> list[str]:
    if spec.destination_ips and spec.destination_ip_weights:
        return _expand_weighted_values(spec.destination_ips, spec.destination_ip_weights, spec.sample_count)
    fallback = tuple(f"172.20.{user_index % 20}.{offset}" for offset in range(1, 6))
    return _expand_weighted_values(fallback, (40, 25, 15, 10, 10), spec.sample_count)


def _expand_weighted_values(values: tuple[str, ...], weights: tuple[int, ...], total: int) -> list[str]:
    if total <= 0:
        return []
    if len(values) != len(weights):
        raise ValueError("values and weights must have the same length")
    weight_total = sum(weights)
    if weight_total <= 0:
        raise ValueError("weights must sum to a positive value")
    raw_counts = [total * weight // weight_total for weight in weights]
    remainder = total - sum(raw_counts)
    for index in range(remainder):
        raw_counts[index % len(raw_counts)] += 1

    expanded: list[str] = []
    for value, count in zip(values, raw_counts):
        expanded.extend([value] * count)
    return expanded


def _boolean_ratio_values(ratio: float, total: int) -> list[bool]:
    true_count = round(total * ratio)
    false_count = total - true_count
    return [False] * false_count + [True] * true_count


def _hour_values(spec: UserSpec) -> list[int]:
    if spec.offhour_ratio > 0:
        offhour_count = round(spec.sample_count * spec.offhour_ratio)
        normal_count = spec.sample_count - offhour_count
        return _cycle_values(spec.hours, normal_count) + _cycle_values((0, 1, 2, 3), offhour_count)
    return _cycle_values(spec.hours, spec.sample_count)


def _day_values(spec: UserSpec, days_in_window: int = DEFAULT_DAYS_IN_MONTH) -> list[int]:
    if not spec.burst_day_indexes:
        return [index % days_in_window for index in range(spec.sample_count)]
    burst_count = round(spec.sample_count * 0.35)
    normal_count = spec.sample_count - burst_count
    normal_days = tuple(day for day in range(days_in_window) if day not in set(spec.burst_day_indexes))
    return _cycle_values(spec.burst_day_indexes, burst_count) + _cycle_values(normal_days, normal_count)


def _cycle_values(values: tuple[int, ...], total: int) -> list[int]:
    return [values[index % len(values)] for index in range(total)]


def _timestamp_for(start_time: datetime, row_index: int, active_hour: int, day: int) -> datetime:
    minute = row_index % 60
    second = (row_index * 7) % 60
    return start_time + timedelta(days=day, hours=active_hour, minutes=minute, seconds=second)


def _session_and_traffic_metrics(
    *,
    row_index: int,
    user_index: int,
    is_failure: bool,
    user_type: str,
) -> tuple[int, int, int]:
    type_offset = {
        "stable": 0,
        "multi_location": 400,
        "high_failure": 80,
        "offhour": 250,
        "ip_long_tail": 550,
        "edge": 0,
    }.get(user_type, 0)
    if is_failure:
        duration = 5 + ((row_index + user_index) % 86)
        bytes_sent = 256 + ((row_index * 11 + user_index) % 1024)
        bytes_recv = 512 + ((row_index * 13 + user_index) % 2048)
        return duration, bytes_sent, bytes_recv
    duration = 600 + ((row_index * 17 + user_index * 13 + type_offset) % 3001)
    bytes_sent = 6000 + type_offset * 3 + ((row_index * 97 + user_index) % 120000)
    bytes_recv = 24000 + type_offset * 5 + ((row_index * 131 + user_index) % 360000)
    return duration, bytes_sent, bytes_recv


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
    if "_iptail_" in username:
        return "ip_long_tail"
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
