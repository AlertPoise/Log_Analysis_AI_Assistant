"""Tests for UEBA acceptance fixture generation."""

from tests.behavior.ueba_baseline_acceptance.config import AcceptanceConfig
from tests.behavior.ueba_baseline_acceptance.fixture_generator import (
    generate_expected_baselines,
    generate_fixture_outputs,
)


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

    assert expected["total_logs"] >= 30000
    assert 10 <= expected["user_count"] <= 30
    assert len(users) == expected["user_count"]
    assert expected["total_logs"] == sum(user["sample_count"] for user in users.values())
    assert summary["total_logs"] == expected["total_logs"]
    assert summary["user_count"] == expected["user_count"]


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
