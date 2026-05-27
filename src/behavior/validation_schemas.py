"""UEBA validation data structures.

These dataclasses define the stable payloads exchanged by future validation
services, scoring logic, and result persistence. They do not access databases,
build scores, or store lists of raw source logs.
"""

from dataclasses import dataclass, field


@dataclass
class ValidationTargetLog:
    """A single structured log selected for later UEBA validation."""

    id: int
    timestamp: str
    username: str
    log_type: str = "vpn"
    source_ip: str | None = None
    destination_ip: str | None = None
    src_country: str | None = None
    src_city: str | None = None
    vpn_gateway: str | None = None
    action: str | None = None
    event_type: str | None = None
    result: str | None = None
    fail_reason: str | None = None
    auth_method: str | None = None
    client_software: str | None = None
    protocol: str | None = None
    is_off_hours: bool | None = None
    is_unusual_ip: bool | None = None
    request_id: str | None = None
    raw_log: str | None = None


@dataclass
class ScoreReason:
    """A single explainable score contribution."""

    code: str
    message: str
    score_delta: int
    evidence: dict[str, object] = field(default_factory=dict)


@dataclass
class UebaValidationResult:
    """UEBA validation result for one source log."""

    validation_id: str
    source_log_id: int
    timestamp: str
    username: str
    log_type: str
    baseline_model_version: str | None
    baseline_created_at: str | None
    baseline_is_reliable: bool
    ueba_score: int
    ueba_risk_level: str
    validation_status: str
    validated_at: str
    ueba_anomaly_reasons: list[ScoreReason] = field(default_factory=list)
    request_id: str | None = None
    error: str | None = None


@dataclass
class ValidationRunResult:
    """Summary of one UEBA validation batch run."""

    success: bool
    selected_count: int = 0
    scored_count: int = 0
    written_count: int = 0
    skipped_count: int = 0
    no_baseline_count: int = 0
    unreliable_baseline_count: int = 0
    failed_count: int = 0
    dry_run: bool = True
    message: str = ""
    error: str | None = None


__all__ = [
    "ValidationTargetLog",
    "ScoreReason",
    "UebaValidationResult",
    "ValidationRunResult",
]
