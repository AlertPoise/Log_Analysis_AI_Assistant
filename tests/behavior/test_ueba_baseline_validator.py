"""Tests for UEBA acceptance baseline validator."""

from __future__ import annotations

import json

from tests.behavior.ueba_baseline_acceptance.baseline_validator import (
    compare_expected_and_actual,
    fetch_actual_baselines,
    validate_fixture_baselines,
)
from tests.behavior.ueba_baseline_acceptance.config import AcceptanceConfig


class FakeClickHouseClient:
    """Fake ClickHouse client for validator tests."""

    def __init__(self, rows=None):
        self.rows = rows or []
        self.queries = []
        self.closed = False

    def query(self, sql, parameters=None):
        self.queries.append((sql, parameters))
        return self.rows

    def close(self):
        self.closed = True


def test_expected_missing_fails_without_querying_clickhouse(tmp_path):
    """Missing expected_baselines.json should fail before ClickHouse access."""
    config = AcceptanceConfig(output_dir=tmp_path)
    calls = []

    def factory(_config):
        calls.append(_config)
        raise AssertionError("should not query")

    result = validate_fixture_baselines(config, client_factory=factory)

    assert result["success"] is False
    assert "expected_baselines.json missing" in result["error"]
    assert calls == []
    assert (tmp_path / "validation_report.json").exists()
    assert json.loads((tmp_path / "failed_diff.json").read_text(encoding="utf-8"))[0]["field"] == "precheck"


def test_build_result_missing_or_failed_stops_validation(tmp_path):
    """Missing or unsuccessful build_result should ask for menu item 3 first."""
    _write_json(tmp_path / "expected_baselines.json", _expected_payload())

    result = validate_fixture_baselines(AcceptanceConfig(output_dir=tmp_path), client_factory=_forbidden_factory)
    assert result["success"] is False
    assert "menu item 3" in result["error"]

    _write_json(tmp_path / "build_result.json", {"success": False})
    result = validate_fixture_baselines(AcceptanceConfig(output_dir=tmp_path), client_factory=_forbidden_factory)
    assert result["success"] is False
    assert "menu item 3" in result["error"]


def test_fetch_actual_baselines_uses_final_fixture_filter_and_enhanced_columns(tmp_path):
    """Actual query must use FINAL, fixture_user_% users, and enhanced baseline columns."""
    config = AcceptanceConfig(output_dir=tmp_path)
    client = FakeClickHouseClient(rows=[_actual_row()])

    actual = fetch_actual_baselines(config, client_factory=lambda _config: client)

    sql, parameters = client.queries[0]
    assert "FINAL" in sql
    assert "username LIKE 'fixture_user_%%'" in sql
    assert "model_version = %(model_version)s" in sql
    for column in (
        "common_destination_ips",
        "common_source_countries",
        "fail_reason_distribution",
        "client_software_distribution",
    ):
        assert column in sql
    assert parameters == {"model_version": config.model_version}
    assert actual["user_count"] == 1
    assert actual["total_sample_count"] == 100
    assert client.closed is True


def test_sample_count_mismatch_writes_failed_diff_field():
    """sample_count mismatch should produce a clear failed diff."""
    expected = _expected_payload()
    actual = _actual_payload(sample_count=99)

    report = compare_expected_and_actual(expected, actual, AcceptanceConfig())

    assert report["success"] is False
    assert any(item["field"] == "sample_count" for item in report["failed_diff"])


def test_is_reliable_boundary_users_match_expected():
    """5/19 should be unreliable and 20/21 reliable."""
    expected = _expected_payload(
        users={
            "fixture_user_edge_0005": _expected_user(sample_count=5, is_reliable=False),
            "fixture_user_edge_0019": _expected_user(sample_count=19, is_reliable=False),
            "fixture_user_edge_0020": _expected_user(sample_count=20, is_reliable=True),
            "fixture_user_edge_0021": _expected_user(sample_count=21, is_reliable=True),
        }
    )
    actual = _actual_payload(
        users={name: _actual_user_from_expected(user) for name, user in expected["users"].items()}
    )

    report = compare_expected_and_actual(expected, actual, AcceptanceConfig())

    assert report["summary"]["is_reliable"] == "PASS"
    assert report["success"] is True


def test_failed_rate_and_unusual_ip_rate_use_tolerance():
    """Float fields should pass within tolerance and fail outside tolerance."""
    config = AcceptanceConfig(validation_float_tolerance=0.0001)
    expected_user = _expected_user(unusual_ip_rate=0.03)
    expected = _expected_payload(users={"fixture_user_multi_0001": expected_user})
    within_user = _actual_user_from_expected(expected_user, failed_rate=0.03005)
    within_user["unusual_ip_rate"] = 0.03005
    outside_user = _actual_user_from_expected(expected_user, failed_rate=0.031)
    outside_user["unusual_ip_rate"] = 0.031

    within = compare_expected_and_actual(
        expected,
        _actual_payload(users={"fixture_user_multi_0001": within_user}),
        config,
    )
    outside = compare_expected_and_actual(
        expected,
        _actual_payload(users={"fixture_user_multi_0001": outside_user}),
        config,
    )

    assert within["summary"]["failed_rate"] == "PASS"
    assert within["summary"]["unusual_ip_rate"] == "PASS"
    assert outside["summary"]["failed_rate"] == "FAIL"
    assert outside["summary"]["unusual_ip_rate"] == "FAIL"


def test_enhanced_distribution_mismatches_fail():
    """New distribution key/count differences should fail."""
    expected = _expected_payload()
    actual = _actual_payload()
    actual_user = actual["users"]["fixture_user_stable_0001"]
    actual_user["fail_reason_distribution"] = {"PASSWORD_ERROR": 3}
    actual_user["client_software_distribution"] = {"OpenVPN Connect": 100}

    report = compare_expected_and_actual(expected, actual, AcceptanceConfig())

    assert report["success"] is False
    assert any(item["field"] == "fail_reason_distribution" for item in report["failed_diff"])
    assert any(item["field"] == "client_software_distribution" for item in report["failed_diff"])


def test_common_values_require_expected_high_frequency_keys():
    """Expected high-frequency common keys must appear in actual common values."""
    expected = _expected_payload()
    actual = _actual_payload()
    actual["users"]["fixture_user_stable_0001"]["common_source_ips"] = ["10.10.1.1"]

    report = compare_expected_and_actual(expected, actual, AcceptanceConfig())

    assert report["success"] is False
    assert any(item["field"] == "common_source_ips" for item in report["failed_diff"])


def test_common_source_countries_are_compared():
    """Expected high-frequency source countries should be required in actual common values."""
    expected = _expected_payload()
    actual = _actual_payload()
    actual["users"]["fixture_user_stable_0001"]["common_source_countries"] = []

    report = compare_expected_and_actual(expected, actual, AcceptanceConfig())

    assert report["success"] is False
    assert any(item["field"] == "common_source_countries" for item in report["failed_diff"])


def test_common_destination_ips_are_compared_when_supported():
    """Expected high-frequency destination IPs should be required in actual common values."""
    expected = _expected_payload()
    actual = _actual_payload()
    actual["users"]["fixture_user_stable_0001"]["common_destination_ips"] = ["172.20.10.10"]

    report = compare_expected_and_actual(expected, actual, AcceptanceConfig())

    assert report["success"] is False
    assert any(item["field"] == "common_destination_ips" for item in report["failed_diff"])


def test_long_tail_source_ip_low_frequency_values_are_not_required():
    """Low-frequency source IP tail values should not all be required in common_source_ips."""
    sample_count = 300
    expected_user = _expected_user(
        sample_count=sample_count,
        source_ip_frequency={f"100.65.2.{index}": 1 for index in range(1, sample_count + 1)},
    )
    expected = _expected_payload(users={"fixture_user_iptail_0002": expected_user})
    actual_user = _actual_user_from_expected(expected_user)
    actual_user["common_source_ips"] = []
    actual = _actual_payload(users={"fixture_user_iptail_0002": actual_user})

    report = compare_expected_and_actual(expected, actual, AcceptanceConfig())

    assert report["summary"]["common_source_ips"] == "PASS"
    assert report["success"] is True


def test_validate_fixture_baselines_success_writes_reports_and_run_state(tmp_path):
    """All matching values should write success reports and run_state."""
    _write_json(tmp_path / "expected_baselines.json", _expected_payload())
    _write_json(tmp_path / "build_result.json", {"success": True})
    client = FakeClickHouseClient(rows=[_actual_row()])
    config = AcceptanceConfig(output_dir=tmp_path)

    result = validate_fixture_baselines(config, client_factory=lambda _config: client)

    failed_diff = json.loads((tmp_path / "failed_diff.json").read_text(encoding="utf-8"))
    run_state = json.loads((tmp_path / "run_state.json").read_text(encoding="utf-8"))
    assert result["success"] is True
    assert result["failed_items"] == 0
    assert failed_diff == []
    assert run_state["comparison_done"] is True
    assert run_state["validation_success"] is True
    assert (tmp_path / "actual_baselines.json").exists()


def test_validate_fixture_baselines_failure_writes_failed_report(tmp_path):
    """Mismatches should produce failed_diff and validation_success=false."""
    _write_json(tmp_path / "expected_baselines.json", _expected_payload())
    _write_json(tmp_path / "build_result.json", {"success": True})
    client = FakeClickHouseClient(rows=[_actual_row(sample_count=99)])
    config = AcceptanceConfig(output_dir=tmp_path)

    result = validate_fixture_baselines(config, client_factory=lambda _config: client)

    failed_diff = json.loads((tmp_path / "failed_diff.json").read_text(encoding="utf-8"))
    run_state = json.loads((tmp_path / "run_state.json").read_text(encoding="utf-8"))
    assert result["success"] is False
    assert result["failed_items"] > 0
    assert failed_diff
    assert run_state["comparison_done"] is True
    assert run_state["validation_success"] is False


def _expected_payload(users=None):
    users = users or {"fixture_user_stable_0001": _expected_user()}
    return {
        "fixture_id": "ueba_fixture_v2_seed_42",
        "total_logs": sum(user["sample_count"] for user in users.values()),
        "user_count": len(users),
        "users": users,
    }


def _expected_user(sample_count=100, is_reliable=True, unusual_ip_rate=0.0, source_ip_frequency=None):
    failed_count = 3
    return {
        "sample_count": sample_count,
        "is_reliable": is_reliable,
        "failed_rate": 0.03,
        "off_hours_rate": 0.0,
        "unusual_ip_rate": unusual_ip_rate,
        "expected_common_active_hours": [9, 10],
        "source_ip_frequency": source_ip_frequency
        or {"10.10.1.1": int(sample_count * 0.6), "10.10.1.2": sample_count - int(sample_count * 0.6)},
        "destination_ip_frequency": {"172.20.10.10": int(sample_count * 0.6), "172.20.10.20": sample_count - int(sample_count * 0.6)},
        "src_country_frequency": {"中国": sample_count},
        "src_city_frequency": {"北京": sample_count},
        "vpn_gateway_frequency": {"vpn-gw-cn-01": sample_count},
        "result_distribution": {"SUCCESS": sample_count - failed_count, "FAILED": 2, "FAIL": 1},
        "event_type_distribution": {"LOGIN_SUCCESS": sample_count - failed_count, "LOGIN_FAIL": failed_count},
        "action_distribution": {"LOGIN": int(sample_count * 0.85), "REAUTH": int(sample_count * 0.1), "LOGOUT": sample_count - int(sample_count * 0.85) - int(sample_count * 0.1)},
        "fail_reason_distribution": {"PASSWORD_ERROR": 2, "MFA_DENIED": 1},
        "auth_method_distribution": {"password+mfa": int(sample_count * 0.7), "sso+mfa": int(sample_count * 0.2), "certificate": sample_count - int(sample_count * 0.7) - int(sample_count * 0.2)},
        "client_software_distribution": {"OpenVPN Connect": int(sample_count * 0.5), "Cisco AnyConnect": int(sample_count * 0.3), "Windows VPN Client": int(sample_count * 0.15), "Tunnelblick": sample_count - int(sample_count * 0.5) - int(sample_count * 0.3) - int(sample_count * 0.15)},
        "protocol_distribution": {"SSLVPN": int(sample_count * 0.8), "IPSec": int(sample_count * 0.15), "WireGuard": sample_count - int(sample_count * 0.8) - int(sample_count * 0.15)},
        "active_day_avg_events": 50.0,
        "max_daily_events": 60,
    }


def _actual_payload(sample_count=100, failed_rate=0.03, unusual_ip_rate=None, users=None):
    if users is None:
        expected = _expected_user(sample_count=sample_count, is_reliable=sample_count >= 20)
        if unusual_ip_rate is not None:
            expected["unusual_ip_rate"] = unusual_ip_rate
        users = {"fixture_user_stable_0001": _actual_user_from_expected(expected, failed_rate=failed_rate)}
    return {
        "model_version": "ueba_baseline_fixture_v2",
        "user_count": len(users),
        "total_sample_count": sum(user["sample_count"] for user in users.values()),
        "users": users,
    }


def _actual_user_from_expected(expected_user, failed_rate=None):
    return {
        "sample_count": expected_user["sample_count"],
        "is_reliable": expected_user["is_reliable"],
        "failed_rate": expected_user["failed_rate"] if failed_rate is None else failed_rate,
        "off_hours_rate": expected_user["off_hours_rate"],
        "unusual_ip_rate": expected_user["unusual_ip_rate"],
        "common_active_hours": [str(hour) for hour in expected_user["expected_common_active_hours"]],
        "common_source_ips": list(expected_user["source_ip_frequency"].keys()),
        "common_destination_ips": list(expected_user["destination_ip_frequency"].keys()),
        "common_source_countries": list(expected_user["src_country_frequency"].keys()),
        "common_source_cities": list(expected_user["src_city_frequency"].keys()),
        "common_vpn_gateways": list(expected_user["vpn_gateway_frequency"].keys()),
        "result_distribution": expected_user["result_distribution"],
        "event_type_distribution": expected_user["event_type_distribution"],
        "action_distribution": expected_user["action_distribution"],
        "fail_reason_distribution": expected_user["fail_reason_distribution"],
        "auth_method_distribution": expected_user["auth_method_distribution"],
        "client_software_distribution": expected_user["client_software_distribution"],
        "protocol_distribution": expected_user["protocol_distribution"],
        "active_day_avg_events": expected_user["active_day_avg_events"],
        "max_daily_events": expected_user["max_daily_events"],
    }


def _actual_row(sample_count=100):
    return {
        "username": "fixture_user_stable_0001",
        "sample_count": sample_count,
        "is_reliable": 1 if sample_count >= 20 else 0,
        "common_active_hours": json.dumps([{"value": 9}, {"value": 10}]),
        "common_source_ips": json.dumps([{"value": "10.10.1.1"}, {"value": "10.10.1.2"}]),
        "common_destination_ips": json.dumps([{"value": "172.20.10.10"}, {"value": "172.20.10.20"}]),
        "common_source_countries": json.dumps([{"value": "中国"}]),
        "common_source_cities": json.dumps([{"value": "北京"}]),
        "common_vpn_gateways": json.dumps([{"value": "vpn-gw-cn-01"}]),
        "result_distribution": json.dumps({"SUCCESS": (sample_count - 3) / sample_count, "FAILED": 2 / sample_count, "FAIL": 1 / sample_count}),
        "event_type_distribution": json.dumps({"LOGIN_SUCCESS": (sample_count - 3) / sample_count, "LOGIN_FAIL": 3 / sample_count}),
        "action_distribution": json.dumps({"LOGIN": 0.85, "REAUTH": 0.1, "LOGOUT": 0.05}),
        "fail_reason_distribution": json.dumps({"PASSWORD_ERROR": 0.02, "MFA_DENIED": 0.01}),
        "auth_method_distribution": json.dumps({"password+mfa": 0.7, "sso+mfa": 0.2, "certificate": 0.1}),
        "client_software_distribution": json.dumps({"OpenVPN Connect": 0.5, "Cisco AnyConnect": 0.3, "Windows VPN Client": 0.15, "Tunnelblick": 0.05}),
        "protocol_distribution": json.dumps({"SSLVPN": 0.8, "IPSec": 0.15, "WireGuard": 0.05}),
        "failed_rate": 0.03,
        "off_hours_rate": 0.0,
        "unusual_ip_rate": 0.0,
        "active_day_avg_events": 50.0,
        "max_daily_events": 60,
        "model_version": "ueba_baseline_fixture_v2",
        "created_at": "2026-05-25 00:00:00",
    }


def _write_json(path, payload):
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


def _forbidden_factory(_config):
    raise AssertionError("should not query ClickHouse")
