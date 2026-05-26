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

    assert expected["fixture_id"] == "ueba_fixture_v2_monthly_seed_42"
    assert summary["model_version"] == "ueba_baseline_fixture_v2_monthly"
    assert expected["total_logs"] >= 60000
    assert expected["total_logs"] == 66130
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

    assert stable["destination_ip_frequency"]["172.20.10.10"] == 1200
    assert stable["destination_ip_frequency"]["172.20.10.20"] == 900
    assert len(stable["destination_ip_frequency"]) == 5
    assert len(tail["destination_ip_frequency"]) == 150


def test_edge_users_have_expected_reliability(tmp_path):
    """Boundary users should cover both sides of min_sample_count = 20."""
    expected, _summary = generate_expected_baselines(AcceptanceConfig(output_dir=tmp_path))
    users = expected["users"]

    assert users["fixture_user_edge_0005"]["sample_count"] == 10
    assert users["fixture_user_edge_0019"]["sample_count"] == 38
    assert users["fixture_user_edge_0020"]["sample_count"] == 40
    assert users["fixture_user_edge_0021"]["sample_count"] == 42
    assert users["fixture_user_edge_0005"]["is_reliable"] is False
    assert users["fixture_user_edge_0019"]["is_reliable"] is True
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


def test_stable_user_failed_rate_reflects_monthly_change(tmp_path):
    """Stable users should reflect the documented June failure-rate change."""
    expected, _summary = generate_expected_baselines(AcceptanceConfig(output_dir=tmp_path))
    user = expected["users"]["fixture_user_stable_0001"]

    assert abs(user["failed_rate"] - 0.055) < 1e-9
    assert user["failed_count"] == 165
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



def test_fixture_generates_full_may_and_june_windows(tmp_path):
    """Default v2 fixture should cover May and June with the same 26 users per month."""
    rows = list(iter_fixture_logs(AcceptanceConfig(output_dir=tmp_path)))
    may_rows = [row for row in rows if "2026-05-01 00:00:00" <= row["timestamp"] < "2026-06-01 00:00:00"]
    june_rows = [row for row in rows if "2026-06-01 00:00:00" <= row["timestamp"] < "2026-07-01 00:00:00"]
    may_users = {row["username"] for row in may_rows}
    june_users = {row["username"] for row in june_rows}

    assert len(rows) >= 60000
    assert len(rows) == 66130
    assert len(may_rows) == 33065
    assert len(june_rows) == 33065
    assert len(may_users) == 26
    assert len(june_users) == 26
    assert may_users == june_users
    assert max(row["timestamp"] for row in rows) < "2026-07-01 00:00:00"


def test_may_and_june_have_required_distribution_coverage(tmp_path):
    """Both months should independently cover the core UEBA fixture dimensions."""
    rows = list(iter_fixture_logs(AcceptanceConfig(output_dir=tmp_path)))
    for start, end in (("2026-05-01 00:00:00", "2026-06-01 00:00:00"), ("2026-06-01 00:00:00", "2026-07-01 00:00:00")):
        month_rows = [row for row in rows if start <= row["timestamp"] < end]
        assert len({row["src_city"] for row in month_rows}) >= 6
        assert len({row["src_country"] for row in month_rows}) >= 4
        assert {"LOGIN", "REAUTH", "VPN_CONNECT"}.issubset({row["action"] for row in month_rows})
        assert {"password+mfa", "sso+mfa", "certificate"}.issubset({row["auth_method"] for row in month_rows})
        assert {"OpenVPN Connect", "Cisco AnyConnect", "Windows VPN Client", "Tunnelblick"}.issubset(
            {row["client_software"] for row in month_rows}
        )
        assert {"SSLVPN", "IPSec", "WireGuard"}.issubset({row["protocol"] for row in month_rows})
        assert {"PASSWORD_ERROR", "MFA_DENIED", "ACCOUNT_LOCKED", "TIMEOUT"}.issubset(
            {row["fail_reason"] for row in month_rows if row["fail_reason"]}
        )
        assert any(row["is_unusual_ip"] for row in month_rows)
        assert {"vpn-gw-cn-01", "vpn-gw-cn-02", "vpn-gw-hk-01", "vpn-gw-sg-01"}.issubset(
            {row["vpn_gateway"] for row in month_rows}
        )


def test_june_distribution_differs_from_may(tmp_path):
    """June should include stable, detectable behavior changes from May."""
    rows = list(iter_fixture_logs(AcceptanceConfig(output_dir=tmp_path)))
    may_rows = [row for row in rows if "2026-05-01 00:00:00" <= row["timestamp"] < "2026-06-01 00:00:00"]
    june_rows = [row for row in rows if "2026-06-01 00:00:00" <= row["timestamp"] < "2026-07-01 00:00:00"]

    assert _ratio_for(may_rows, "result", "FAILED") != _ratio_for(june_rows, "result", "FAILED")
    assert _ratio_for(may_rows, "protocol", "WireGuard") != _ratio_for(june_rows, "protocol", "WireGuard")
    assert _ratio_for(may_rows, "vpn_gateway", "vpn-gw-sg-01") != _ratio_for(june_rows, "vpn_gateway", "vpn-gw-sg-01")
    assert sum(1 for row in june_rows if row["is_off_hours"]) > sum(1 for row in may_rows if row["is_off_hours"])
    assert sum(1 for row in june_rows if row["is_unusual_ip"]) > sum(1 for row in may_rows if row["is_unusual_ip"])


def _ratio_for(rows, field, value):
    return sum(1 for row in rows if row[field] == value) / len(rows)
