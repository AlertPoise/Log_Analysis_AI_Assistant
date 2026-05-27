"""Tests for UEBA validation schema dataclasses."""

from pathlib import Path

from src.behavior.validation_schemas import (
    ScoreReason,
    UebaValidationResult,
    ValidationRunResult,
    ValidationTargetLog,
)


def _validation_result(validation_id: str = "validation-1") -> UebaValidationResult:
    """Build a minimal validation result for schema tests."""
    return UebaValidationResult(
        validation_id=validation_id,
        source_log_id=101,
        timestamp="2024-03-01 10:00:00",
        username="alice",
        log_type="vpn",
        baseline_model_version="ueba_baseline_v1",
        baseline_created_at="2024-02-29 00:00:00",
        baseline_is_reliable=True,
        ueba_score=25,
        ueba_risk_level="MEDIUM",
        validation_status="VALIDATED",
        validated_at="2024-03-01 10:00:10",
    )


def test_validation_target_log_stores_core_fields():
    """ValidationTargetLog should preserve the fields needed by later scoring."""
    target = ValidationTargetLog(
        id=1001,
        timestamp="2024-03-01 10:00:00",
        username="alice",
        source_ip="10.0.0.10",
        destination_ip="10.0.1.20",
        src_country="CN",
        src_city="Shanghai",
        vpn_gateway="gw-1",
        action="LOGIN",
        event_type="LOGIN_SUCCESS",
        result="SUCCESS",
        is_off_hours=False,
        is_unusual_ip=True,
        request_id="req-1",
        raw_log="raw-ref",
    )

    assert target.id == 1001
    assert target.log_type == "vpn"
    assert target.source_ip == "10.0.0.10"
    assert target.destination_ip == "10.0.1.20"
    assert target.is_unusual_ip is True
    assert target.raw_log == "raw-ref"


def test_score_reason_default_evidence_is_not_shared():
    """ScoreReason evidence should be isolated between instances."""
    first = ScoreReason(code="NEW_SOURCE_IP", message="new source", score_delta=12)
    second = ScoreReason(code="NEW_CITY", message="new city", score_delta=15)

    first.evidence["source_ip"] = "10.0.0.10"

    assert second.evidence == {}


def test_validation_result_default_reasons_are_not_shared():
    """UebaValidationResult reasons should be isolated between instances."""
    first = _validation_result("validation-1")
    second = _validation_result("validation-2")

    first.ueba_anomaly_reasons.append(
        ScoreReason(code="OFF_HOURS", message="off hours", score_delta=10)
    )

    assert second.ueba_anomaly_reasons == []


def test_validation_run_result_defaults():
    """ValidationRunResult should default counts to zero and dry-run to true."""
    result = ValidationRunResult(success=True)

    assert result.selected_count == 0
    assert result.scored_count == 0
    assert result.written_count == 0
    assert result.skipped_count == 0
    assert result.no_baseline_count == 0
    assert result.unreliable_baseline_count == 0
    assert result.failed_count == 0
    assert result.dry_run is True
    assert result.message == ""
    assert result.error is None


def test_validation_schema_source_has_no_forbidden_markers():
    """Validation schema source should not carry stage-specific fixture markers."""
    source = Path("src/behavior/validation_schemas.py").read_text(encoding="utf-8")
    forbidden = [
        "." + "tox",
        "fixture" + "_user",
        "2026" + "-05",
        "2026" + "-06",
    ]

    for marker in forbidden:
        assert marker not in source


def test_risk_level_and_validation_status_store_expected_strings():
    """Result schema should carry validation status and risk level values."""
    result = _validation_result()
    result.ueba_risk_level = "CRITICAL"
    result.validation_status = "NO_BASELINE"

    assert result.ueba_risk_level == "CRITICAL"
    assert result.validation_status == "NO_BASELINE"
