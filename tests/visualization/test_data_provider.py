"""Tests for the visualization behavior data provider."""

from __future__ import annotations

from src.visualization.data_provider import get_dashboard_data


def test_get_dashboard_data_returns_required_fields() -> None:
    data = get_dashboard_data()

    assert isinstance(data, dict)
    assert "summary" in data
    assert "risk_distribution" in data
    assert "anomaly_users" in data
    assert "anomaly_events" in data

    summary = data["summary"]
    assert "total_logs" in summary
    assert "anomaly_count" in summary
    assert "high_risk_users" in summary
    assert "security_score" in summary


def test_get_dashboard_data_uses_behavior_source_when_sample_logs_exist() -> None:
    data = get_dashboard_data()

    assert data["source"] == "behavior"
    assert data["success"] is True
    assert data["summary"]["total_logs"] > 0


def test_get_dashboard_data_returns_multiple_anomaly_users_when_target_users_exist() -> None:
    data = get_dashboard_data()

    usernames = {item["username"] for item in data["anomaly_users"]}
    assert len(usernames) >= 2
    assert "zhangsan" in usernames


def test_get_dashboard_data_falls_back_to_mock_when_file_missing() -> None:
    data = get_dashboard_data("not_exists.json")

    assert data["source"] == "mock"
    assert data["success"] is False
