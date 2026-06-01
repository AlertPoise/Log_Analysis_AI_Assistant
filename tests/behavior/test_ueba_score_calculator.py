"""UEBA score calculator core regression tests."""

from datetime import datetime

from src.behavior.schemas import CountRatioItem, UserBaseline
from src.behavior.score_calculator import UebaScoreCalculator, build_source_identity
from src.behavior.validation_schemas import ValidationTargetLog


def _baseline(*, reliable: bool = True, source_ips: list[CountRatioItem] | None = None) -> UserBaseline:
    return UserBaseline(
        username="alice",
        sample_count=100 if reliable else 5,
        is_reliable=reliable,
        common_active_hours=[CountRatioItem(9, 80, 0.8)],
        common_source_ips=source_ips or [CountRatioItem("10.0.0.1", 70, 0.7)],
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
        failed_rate=0.01,
        off_hours_rate=0.05,
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


def _calculate(target_log: ValidationTargetLog, baseline: UserBaseline | None = None, run_id: str = "run-1"):
    return UebaScoreCalculator().calculate(
        target_log,
        _baseline() if baseline is None else baseline,
        validated_at="2024-03-02 09:30:05",
        validation_run_id=run_id,
    )


def _reason_codes(result) -> list[str]:
    return [reason.code for reason in result.ueba_anomaly_reasons]


def test_normal_matching_log_is_low_risk() -> None:
    result = _calculate(_target())

    assert result.validation_status == "VALIDATED"
    assert result.ueba_score == 0
    assert result.ueba_risk_level == "LOW"
    assert result.ueba_anomaly_reasons == []


def test_dimension_anomalies_add_explainable_reasons() -> None:
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
            is_off_hours=True,
            is_unusual_ip=True,
        )
    )

    codes = set(_reason_codes(result))
    assert {
        "NEW_SOURCE_COUNTRY",
        "NEW_SOURCE_CITY",
        "NEW_SOURCE_IP",
        "NEW_DESTINATION_IP",
        "NEW_VPN_GATEWAY",
        "NEW_AUTH_METHOD",
        "NEW_CLIENT_SOFTWARE",
        "NEW_PROTOCOL",
        "OFF_HOURS",
        "UNUSUAL_IP",
    }.issubset(codes)


def test_failed_login_and_combined_anomalies_reach_high_risk() -> None:
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

    assert "LOGIN_FAILED" in _reason_codes(result)
    assert result.ueba_risk_level in {"HIGH", "CRITICAL"}
    assert result.ueba_score >= 50


def test_no_baseline_and_unreliable_baseline_statuses() -> None:
    no_baseline = UebaScoreCalculator().calculate(
        _target(),
        None,
        model_version="ueba_baseline_v1",
        validated_at="2024-03-02 09:30:05",
        validation_run_id="run-1",
    )
    unreliable = _calculate(_target(src_country="DE"), _baseline(reliable=False))

    assert no_baseline.validation_status == "NO_BASELINE"
    assert no_baseline.ueba_score == 15
    assert "NO_BASELINE" in _reason_codes(no_baseline)
    assert unreliable.validation_status == "UNRELIABLE_BASELINE"
    assert "UNRELIABLE_BASELINE" in _reason_codes(unreliable)


def test_score_is_clamped_to_one_hundred() -> None:
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


def test_source_identity_prefers_request_id_and_hashes_stably() -> None:
    with_request_id = _target(request_id="req-identity", source_ip="198.51.100.1")
    first = _target(id=0, request_id=None, source_ip="198.51.100.1")
    second = _target(id=999, request_id="", source_ip="198.51.100.1")
    different = _target(id=0, request_id=None, source_ip="198.51.100.2")

    assert build_source_identity(with_request_id) == "request_id:req-identity"
    assert build_source_identity(first) == build_source_identity(second)
    assert build_source_identity(first).startswith("source_hash:")
    assert build_source_identity(first) != build_source_identity(different)


def test_validation_id_is_deterministic_but_run_sensitive() -> None:
    calculator = UebaScoreCalculator()
    target = _target(id=0, request_id=None)
    baseline = _baseline()

    first = calculator.calculate(target, baseline, validated_at="2024-03-02 09:30:05", validation_run_id="run-1")
    second = calculator.calculate(target, baseline, validated_at="2024-03-02 09:31:05", validation_run_id="run-1")
    other_run = calculator.calculate(target, baseline, validation_run_id="run-2")

    assert first.validation_id == second.validation_id
    assert first.validation_id != other_run.validation_id
