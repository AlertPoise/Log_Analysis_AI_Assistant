"""Tests for the pure Python UEBA score calculator."""

from datetime import datetime
from pathlib import Path

from src.behavior.schemas import CountRatioItem, UserBaseline
from src.behavior.score_calculator import UebaScoreCalculator
from src.behavior.validation_schemas import ValidationTargetLog


def _baseline(
    *,
    reliable: bool = True,
    source_ips: list[CountRatioItem] | None = None,
    failed_rate: float = 0.01,
    off_hours_rate: float = 0.05,
) -> UserBaseline:
    """Build a baseline sample for score tests."""
    return UserBaseline(
        username="alice",
        sample_count=100 if reliable else 5,
        is_reliable=reliable,
        common_active_hours=[CountRatioItem(9, 80, 0.8)],
        common_source_ips=source_ips
        if source_ips is not None
        else [CountRatioItem("10.0.0.1", 70, 0.7)],
        common_destination_ips=[CountRatioItem("10.0.1.1", 80, 0.8)],
        common_source_countries=[CountRatioItem("CN", 90, 0.9)],
        common_source_cities=[CountRatioItem("Shanghai", 90, 0.9)],
        common_vpn_gateways=[CountRatioItem("gw-1", 90, 0.9)],
        action_distribution={"LOGIN": 1.0},
        event_type_distribution={"LOGIN_SUCCESS": 0.99, "LOGIN_FAIL": 0.01},
        result_distribution={"SUCCESS": 0.99, "FAIL": 0.01},
        fail_reason_distribution={"PASSWORD_ERROR": 0.01},
        auth_method_distribution={"password": 1.0},
        client_software_distribution={"OpenVPN": 1.0},
        protocol_distribution={"tcp": 1.0},
        failed_rate=failed_rate,
        off_hours_rate=off_hours_rate,
        unusual_ip_rate=0.02,
        avg_daily_events=10.0,
        session_duration_avg=300.0,
        session_duration_p50=280.0,
        session_duration_p95=550.0,
        bytes_sent_avg=1024.0,
        bytes_recv_avg=4096.0,
        active_day_avg_events=10.0,
        max_daily_events=18,
        baseline_start_time=datetime(2024, 2, 1),
        baseline_end_time=datetime(2024, 3, 1),
        model_version="ueba_baseline_v1",
    )


def _target(**overrides) -> ValidationTargetLog:
    """Build a target log that matches the default baseline."""
    values = {
        "id": 1001,
        "timestamp": "2024-03-02 09:30:00",
        "username": "alice",
        "log_type": "vpn",
        "source_ip": "10.0.0.1",
        "destination_ip": "10.0.1.1",
        "src_country": "CN",
        "src_city": "Shanghai",
        "vpn_gateway": "gw-1",
        "action": "LOGIN",
        "event_type": "LOGIN_SUCCESS",
        "result": "SUCCESS",
        "auth_method": "password",
        "client_software": "OpenVPN",
        "protocol": "tcp",
        "is_off_hours": False,
        "is_unusual_ip": False,
        "request_id": "req-1",
    }
    values.update(overrides)
    return ValidationTargetLog(**values)


def _calculate(target_log, baseline=None):
    """Calculate with a deterministic validation timestamp."""
    return UebaScoreCalculator().calculate(
        target_log,
        _baseline() if baseline is None else baseline,
        validated_at="2024-03-02 09:30:05",
    )


def _reason_codes(result):
    """Return reason codes from a validation result."""
    return [reason.code for reason in result.ueba_anomaly_reasons]


def _reason(result, code):
    """Return one reason by code."""
    return next(reason for reason in result.ueba_anomaly_reasons if reason.code == code)


def test_no_baseline_returns_low_risk_no_baseline_status():
    """Missing baseline should not directly become high risk."""
    result = UebaScoreCalculator().calculate(
        _target(),
        None,
        model_version="ueba_baseline_v1",
        validated_at="2024-03-02 09:30:05",
    )

    assert result.validation_status == "NO_BASELINE"
    assert result.ueba_risk_level == "LOW"
    assert result.ueba_score == 15
    assert "NO_BASELINE" in _reason_codes(result)


def test_unreliable_baseline_marks_status_and_still_scores():
    """Unreliable baselines should be marked but still produce a score."""
    result = _calculate(
        _target(src_country="DE"),
        _baseline(reliable=False),
    )

    assert result.validation_status == "UNRELIABLE_BASELINE"
    assert "UNRELIABLE_BASELINE" in _reason_codes(result)
    assert "NEW_SOURCE_COUNTRY" in _reason_codes(result)
    assert result.ueba_score > 10


def test_normal_matching_log_is_low_risk():
    """A log matching common baseline values should stay low risk."""
    result = _calculate(_target())

    assert result.validation_status == "VALIDATED"
    assert result.ueba_score == 0
    assert result.ueba_risk_level == "LOW"
    assert result.ueba_anomaly_reasons == []


def test_new_country_and_city_add_reasons():
    """New source geography should raise score and explain both dimensions."""
    result = _calculate(_target(src_country="DE", src_city="Berlin"))

    assert result.ueba_score >= 30
    assert "NEW_SOURCE_COUNTRY" in _reason_codes(result)
    assert "NEW_SOURCE_CITY" in _reason_codes(result)
    assert _reason(result, "NEW_SOURCE_COUNTRY").evidence["actual"] == "DE"


def test_new_source_ip_adds_reason():
    """New source IP should be scored against common source IPs."""
    result = _calculate(_target(source_ip="10.0.0.99"))

    assert "NEW_SOURCE_IP" in _reason_codes(result)
    assert _reason(result, "NEW_SOURCE_IP").score_delta == 12


def test_long_tail_source_ip_is_down_weighted():
    """Dispersed source IP users should get lower new-IP weight."""
    ordinary = _calculate(_target(source_ip="10.0.0.99"))
    long_tail_baseline = _baseline(
        source_ips=[CountRatioItem("10.0.0.1", 5, 0.05), CountRatioItem("10.0.0.2", 4, 0.04)]
    )
    long_tail = _calculate(_target(source_ip="10.0.0.99"), long_tail_baseline)

    assert _reason(long_tail, "NEW_SOURCE_IP").score_delta < _reason(ordinary, "NEW_SOURCE_IP").score_delta


def test_off_hours_adds_reason():
    """Off-hours input signal should contribute an explainable reason."""
    result = _calculate(_target(is_off_hours=True))

    assert "OFF_HOURS" in _reason_codes(result)


def test_failed_login_adds_reason():
    """Failed login result or event type should add LOGIN_FAILED."""
    result = _calculate(_target(result="FAILED", event_type="LOGIN_FAIL"))

    assert "LOGIN_FAILED" in _reason_codes(result)
    assert _reason(result, "LOGIN_FAILED").score_delta == 20


def test_unusual_ip_adds_reason():
    """Input unusual-IP signal should be preserved as a score reason."""
    result = _calculate(_target(is_unusual_ip=True))

    assert "UNUSUAL_IP" in _reason_codes(result)
    assert _reason(result, "UNUSUAL_IP").score_delta == 20


def test_multi_signal_reaches_high_risk():
    """Multiple deviations should combine into at least HIGH risk."""
    result = _calculate(
        _target(
            source_ip="198.51.100.10",
            destination_ip="10.0.2.50",
            src_country="DE",
            src_city="Berlin",
            vpn_gateway="gw-9",
            auth_method="mfa-push",
            client_software="UnknownVPN",
            protocol="udp",
            result="FAIL",
            event_type="LOGIN_FAIL",
            is_off_hours=True,
            is_unusual_ip=True,
        )
    )

    assert result.ueba_risk_level in {"HIGH", "CRITICAL"}
    assert result.ueba_score >= 50


def test_score_is_clamped_to_one_hundred():
    """Strong multi-signal results should not exceed score 100."""
    result = _calculate(
        _target(
            source_ip="198.51.100.10",
            destination_ip="10.0.2.50",
            src_country="DE",
            src_city="Berlin",
            vpn_gateway="gw-9",
            auth_method="mfa-push",
            client_software="UnknownVPN",
            protocol="udp",
            result="FAIL",
            event_type="LOGIN_FAIL",
            is_off_hours=True,
            is_unusual_ip=True,
        )
    )

    assert result.ueba_score == 100
    assert result.ueba_risk_level == "CRITICAL"


def test_validation_id_is_deterministic():
    """The same log and model version should produce the same validation id."""
    calculator = UebaScoreCalculator()
    target = _target()
    baseline = _baseline()

    first = calculator.calculate(target, baseline, validated_at="2024-03-02 09:30:05")
    second = calculator.calculate(target, baseline, validated_at="2024-03-02 09:31:05")

    assert first.validation_id == second.validation_id


def test_score_calculator_source_has_no_forbidden_markers():
    """Runtime score calculator should not carry stage-only fixture markers."""
    source = Path("src/behavior/score_calculator.py").read_text(encoding="utf-8")
    forbidden = [
        "." + "tox",
        "fixture" + "_user",
        "2026" + "-05",
        "2026" + "-06",
        "accept" + "ance",
        "manual" + "_training" + "_update",
        "monthly" + "_training" + "_update",
    ]

    for marker in forbidden:
        assert marker not in source
