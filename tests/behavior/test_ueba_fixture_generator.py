"""Tests for UEBA acceptance fixture generation."""

import re

from tests.behavior.ueba_baseline_acceptance.config import AcceptanceConfig
from tests.behavior.ueba_baseline_acceptance.fixture_generator import (
    generate_expected_baselines,
    generate_fixture_outputs,
    iter_fixture_logs,
)


TIMESTAMP_PATTERN = re.compile(r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}$")


def test_same_seed_generates_same_expected_baselines(tmp_path):
    """The default fixture must be deterministic for reviewable acceptance."""
    config_a = AcceptanceConfig(output_dir=tmp_path / "a")
    config_b = AcceptanceConfig(output_dir=tmp_path / "b")

    expected_a, summary_a = generate_expected_baselines(config_a)
    expected_b, summary_b = generate_expected_baselines(config_b)

    assert expected_a == expected_b
    assert summary_a == summary_b


def test_expected_baseline_totals_and_user_counts(tmp_path):
    """Generated expected baseline totals should be internally consistent."""
    config = AcceptanceConfig(output_dir=tmp_path)
    expected, summary = generate_expected_baselines(config)
    users = expected["users"]

    assert expected["fixture_id"] == "ueba_fixture_v2_seed_42"
    assert summary["model_version"] == "ueba_baseline_fixture_v2"
    assert expected["total_logs"] >= 30000
    assert expected["total_logs"] == 33065
    assert 10 <= expected["user_count"] <= 30
    assert expected["user_count"] == 26
    assert len(users) == expected["user_count"]
    assert expected["total_logs"] == sum(user["sample_count"] for user in users.values())
    assert summary["total_logs"] == expected["total_logs"]
    assert summary["user_count"] == expected["user_count"]
    assert summary["user_types"]["ip_long_tail"] == 2


def test_fixture_covers_enhanced_business_distributions(tmp_path):
    """Default fixture should contain varied geo, action, auth, client, protocol, and fail reasons."""
    expected, _summary = generate_expected_baselines(AcceptanceConfig(output_dir=tmp_path))
    users = expected["users"].values()

    cities = _all_keys(users, "src_city_frequency")
    countries = _all_keys(users, "src_country_frequency")
    actions = _all_keys(users, "action_distribution")
    auth_methods = _all_keys(users, "auth_method_distribution")
    client_softwares = _all_keys(users, "client_software_distribution")
    protocols = _all_keys(users, "protocol_distribution")
    fail_reasons = _all_keys(users, "fail_reason_distribution")
    gateways = _all_keys(users, "vpn_gateway_frequency")

    assert len(cities) >= 6
    assert {"北京", "上海", "广州", "深圳", "成都"}.issubset(cities)
    assert len(countries) >= 4
    assert {"中国", "新加坡", "日本", "德国"}.issubset(countries)
    assert {"LOGIN", "REAUTH", "LOGOUT", "VPN_CONNECT"}.issubset(actions)
    assert {"password+mfa", "sso+mfa", "certificate", "password_only"}.issubset(auth_methods)
    assert {"OpenVPN Connect", "Cisco AnyConnect", "Windows VPN Client", "Tunnelblick"}.issubset(client_softwares)
    assert {"SSLVPN", "IPSec", "WireGuard"}.issubset(protocols)
    assert {"PASSWORD_ERROR", "MFA_DENIED", "ACCOUNT_LOCKED", "TIMEOUT"}.issubset(fail_reasons)
    assert {"vpn-gw-cn-01", "vpn-gw-cn-02", "vpn-gw-hk-01", "vpn-gw-sg-01"}.issubset(gateways)


def test_unusual_ip_and_long_tail_source_ip_scenarios(tmp_path):
    """Fixture should include non-zero unusual IP rates and source IP long-tail users."""
    config = AcceptanceConfig(output_dir=tmp_path)
    expected, _summary = generate_expected_baselines(config)
    users = expected["users"]

    unusual_users = [user for user in users.values() if user["unusual_ip_rate"] > 0]
    tail_1 = users["fixture_user_iptail_0001"]
    tail_2 = users["fixture_user_iptail_0002"]
    tail_1_common = [
        ip for ip, count in tail_1["source_ip_frequency"].items() if count / tail_1["sample_count"] >= config.validation_common_min_ratio
    ]

    assert unusual_users
    assert tail_1["unusual_ip_rate"] > 0
    assert tail_2["unusual_ip_rate"] > 0
    assert len(tail_1["source_ip_frequency"]) == 105
    assert len(tail_1_common) == 5
    assert len(tail_2["source_ip_frequency"]) == 300
    assert max(tail_2["source_ip_frequency"].values()) / tail_2["sample_count"] < config.validation_common_min_ratio
    assert tail_1["max_daily_events"] > tail_1["active_day_avg_events"] * 3


def test_destination_ip_has_stable_and_long_tail_contrast(tmp_path):
    """Destination IP frequencies should include stable resources and long-tail targets."""
    expected, _summary = generate_expected_baselines(AcceptanceConfig(output_dir=tmp_path))
    stable = expected["users"]["fixture_user_stable_0001"]
    tail = expected["users"]["fixture_user_iptail_0002"]

    assert stable["destination_ip_frequency"]["172.20.10.10"] == 600
    assert stable["destination_ip_frequency"]["172.20.10.20"] == 450
    assert len(stable["destination_ip_frequency"]) == 5
    assert len(tail["destination_ip_frequency"]) == 150


def test_edge_users_have_expected_reliability(tmp_path):
    """Boundary users should cover both sides of min_sample_count = 20."""
    expected, _summary = generate_expected_baselines(AcceptanceConfig(output_dir=tmp_path))
    users = expected["users"]

    assert users["fixture_user_edge_0005"]["sample_count"] == 5
    assert users["fixture_user_edge_0019"]["sample_count"] == 19
    assert users["fixture_user_edge_0020"]["sample_count"] == 20
    assert users["fixture_user_edge_0021"]["sample_count"] == 21
    assert users["fixture_user_edge_0005"]["is_reliable"] is False
    assert users["fixture_user_edge_0019"]["is_reliable"] is False
    assert users["fixture_user_edge_0020"]["is_reliable"] is True
    assert users["fixture_user_edge_0021"]["is_reliable"] is True


def test_high_failure_users_include_failed_and_fail_results(tmp_path):
    """High-failure fixtures must exercise both FAILED and FAIL failure values."""
    expected, _summary = generate_expected_baselines(AcceptanceConfig(output_dir=tmp_path))
    user = expected["users"]["fixture_user_failed_0001"]

    assert user["result_distribution"]["FAILED"] > 0
    assert user["result_distribution"]["FAIL"] > 0
    assert len(user["fail_reason_distribution"]) == 4
    assert user["event_type_distribution"]["LOGIN_FAIL"] == (
        user["result_distribution"]["FAILED"] + user["result_distribution"]["FAIL"]
    )


def test_stable_user_failed_rate_is_three_percent(tmp_path):
    """Stable users should have the documented low failure rate."""
    expected, _summary = generate_expected_baselines(AcceptanceConfig(output_dir=tmp_path))
    user = expected["users"]["fixture_user_stable_0001"]

    assert abs(user["failed_rate"] - 0.03) < 1e-9
    assert user["failed_count"] == 45
    assert user["expected_common_active_hours"] == [9, 10, 14, 15]


def test_iter_fixture_logs_keeps_plain_timestamp_string_format(tmp_path):
    """Generated timestamps should stay as YYYY-MM-DD HH:MM:SS strings without timezone suffixes."""
    rows = list(iter_fixture_logs(AcceptanceConfig(output_dir=tmp_path)))[:25]

    assert rows
    for row in rows:
        assert isinstance(row["timestamp"], str)
        assert TIMESTAMP_PATTERN.fullmatch(row["timestamp"])
        assert "T" not in row["timestamp"]
        assert "Z" not in row["timestamp"]
        assert "+08:00" not in row["timestamp"]


def test_generate_fixture_outputs_does_not_dump_raw_logs_by_default(tmp_path):
    """The first-stage tool should write only reports unless explicitly requested."""
    config = AcceptanceConfig(output_dir=tmp_path)
    state = generate_fixture_outputs(config)

    assert (tmp_path / "expected_baselines.json").exists()
    assert (tmp_path / "fixture_summary.json").exists()
    assert (tmp_path / "run_state.json").exists()
    assert state["fixture_logs_path"] is None
    assert not (tmp_path / "fixture_logs.jsonl").exists()
    assert not (tmp_path / "debug_fixture_logs.jsonl").exists()


def _all_keys(users, field):
    keys = set()
    for user in users:
        keys.update(user[field])
    return keys
