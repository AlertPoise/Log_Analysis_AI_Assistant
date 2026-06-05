"""Pure Python UEBA validation score calculator.

The calculator compares one structured target log with one user baseline and
returns a validation result. It does not access databases, write ClickHouse, or
coordinate batch validation workflows.
"""

from datetime import datetime, timezone
import hashlib
import json
from typing import Iterable

from .schemas import CountRatioItem, UserBaseline
from .validation_schemas import ScoreReason, UebaValidationResult, ValidationTargetLog


class UebaScoreCalculator:
    """Calculate explainable UEBA validation scores for target logs."""

    NO_BASELINE_SCORE = 15
    UNRELIABLE_BASELINE_SCORE = 10
    UNRELIABLE_BASELINE_WEIGHT = 0.5
    LONG_TAIL_SOURCE_IP_SCORE = 5
    LONG_TAIL_SOURCE_IP_RATIO = 0.2

    def calculate(
        self,
        target_log: ValidationTargetLog,
        baseline: UserBaseline | None,
        model_version: str | None = None,
        validated_at: str | None = None,
        validation_run_id: str = "default_validation_run",
    ) -> UebaValidationResult:
        """Calculate a validation result for one target log."""
        effective_model_version = model_version or (baseline.model_version if baseline else None)
        effective_validated_at = validated_at or self._now_string()

        if baseline is None:
            reasons = [
                ScoreReason(
                    code="NO_BASELINE",
                    message="No user baseline is available for this log.",
                    score_delta=self.NO_BASELINE_SCORE,
                    evidence={"username": target_log.username},
                )
            ]
            score = self._clamp_score(self.NO_BASELINE_SCORE)
            return self._build_result(
                target_log=target_log,
                baseline_model_version=effective_model_version,
                baseline_created_at=None,
                baseline_is_reliable=False,
                score=score,
                risk_level=self._risk_level(score),
                reasons=reasons,
                validation_status="NO_BASELINE",
                validated_at=effective_validated_at,
                validation_run_id=validation_run_id,
            )

        reasons: list[ScoreReason] = []
        score = 0
        validation_status = "VALIDATED"
        baseline_weight = 1.0

        if not baseline.is_reliable:
            validation_status = "UNRELIABLE_BASELINE"
            baseline_weight = self.UNRELIABLE_BASELINE_WEIGHT
            self._add_reason(
                reasons,
                code="UNRELIABLE_BASELINE",
                message="The user baseline has too few samples for full-confidence scoring.",
                score_delta=self.UNRELIABLE_BASELINE_SCORE,
                evidence={"sample_count": baseline.sample_count},
            )
            score += self.UNRELIABLE_BASELINE_SCORE

        source_country_new = self._score_count_ratio_mismatch(
            reasons,
            code="NEW_SOURCE_COUNTRY",
            message="Source country is outside the user's common baseline countries.",
            actual=target_log.src_country,
            common_items=baseline.common_source_countries,
            base_score=30,
            baseline_weight=baseline_weight,
        )
        self._score_count_ratio_mismatch(
            reasons,
            code="NEW_SOURCE_CITY",
            message="Source city is outside the user's common baseline cities.",
            actual=target_log.src_city,
            common_items=baseline.common_source_cities,
            base_score=8 if source_country_new else 15,
            baseline_weight=baseline_weight,
            extra_evidence={"source_country_also_new": source_country_new},
        )
        self._score_source_ip(reasons, target_log, baseline, baseline_weight)
        self._score_count_ratio_mismatch(
            reasons,
            code="NEW_DESTINATION_IP",
            message="Destination IP is outside the user's common baseline destinations.",
            actual=target_log.destination_ip,
            common_items=baseline.common_destination_ips,
            base_score=8,
            baseline_weight=baseline_weight,
        )
        self._score_count_ratio_mismatch(
            reasons,
            code="NEW_VPN_GATEWAY",
            message="VPN gateway is outside the user's common baseline gateways.",
            actual=target_log.vpn_gateway,
            common_items=baseline.common_vpn_gateways,
            base_score=15,
            baseline_weight=baseline_weight,
        )
        self._score_distribution_mismatch(
            reasons,
            code="NEW_AUTH_METHOD",
            message="Authentication method is outside the user's baseline distribution.",
            actual=target_log.auth_method,
            distribution=baseline.auth_method_distribution,
            base_score=15,
            baseline_weight=baseline_weight,
        )
        self._score_distribution_mismatch(
            reasons,
            code="NEW_CLIENT_SOFTWARE",
            message="Client software is outside the user's baseline distribution.",
            actual=target_log.client_software,
            distribution=baseline.client_software_distribution,
            base_score=10,
            baseline_weight=baseline_weight,
        )
        self._score_distribution_mismatch(
            reasons,
            code="NEW_PROTOCOL",
            message="Protocol is outside the user's baseline distribution.",
            actual=target_log.protocol,
            distribution=baseline.protocol_distribution,
            base_score=10,
            baseline_weight=baseline_weight,
        )

        self._score_off_hours(reasons, target_log, baseline, baseline_weight)
        self._score_failed_login(reasons, target_log, baseline, baseline_weight)
        if target_log.is_unusual_ip:
            self._add_reason(
                reasons,
                code="UNUSUAL_IP",
                message="The source log is marked with an unusual IP signal.",
                score_delta=20,
                evidence={"is_unusual_ip": True, "baseline_unusual_ip_rate": baseline.unusual_ip_rate},
            )

        score += sum(reason.score_delta for reason in reasons if reason.code != "UNRELIABLE_BASELINE")
        score = self._clamp_score(score)
        return self._build_result(
            target_log=target_log,
            baseline_model_version=effective_model_version,
            baseline_created_at=None,
            baseline_is_reliable=baseline.is_reliable,
            score=score,
            risk_level=self._risk_level(score),
            reasons=reasons,
            validation_status=validation_status,
            validated_at=effective_validated_at,
            validation_run_id=validation_run_id,
        )

    def _score_source_ip(
        self,
        reasons: list[ScoreReason],
        target_log: ValidationTargetLog,
        baseline: UserBaseline,
        baseline_weight: float,
    ) -> None:
        """Score source IP mismatch with long-tail user down-weighting."""
        actual = self._normalize_value(target_log.source_ip)
        if actual is None:
            return

        common_values = self._common_values(baseline.common_source_ips)
        long_tail = self._is_long_tail_source_ip_user(baseline)
        if common_values and actual in common_values:
            return
        if not common_values and not long_tail:
            return

        base_score = self.LONG_TAIL_SOURCE_IP_SCORE if long_tail else 12
        self._add_reason(
            reasons,
            code="NEW_SOURCE_IP",
            message="Source IP is outside the user's common baseline source IPs.",
            score_delta=self._weighted_score(base_score, baseline_weight),
            evidence={
                "actual": actual,
                "common_values": sorted(common_values),
                "long_tail_source_ip_user": long_tail,
            },
        )

    def _score_count_ratio_mismatch(
        self,
        reasons: list[ScoreReason],
        *,
        code: str,
        message: str,
        actual: object,
        common_items: Iterable[CountRatioItem],
        base_score: int,
        baseline_weight: float,
        extra_evidence: dict[str, object] | None = None,
    ) -> bool:
        """Score a mismatch against CountRatioItem common values."""
        actual_value = self._normalize_value(actual)
        if actual_value is None:
            return False

        common_values = self._common_values(common_items)
        if not common_values or actual_value in common_values:
            return False

        evidence: dict[str, object] = {
            "actual": actual_value,
            "common_values": sorted(common_values),
        }
        if extra_evidence:
            evidence.update(extra_evidence)
        self._add_reason(
            reasons,
            code=code,
            message=message,
            score_delta=self._weighted_score(base_score, baseline_weight),
            evidence=evidence,
        )
        return True

    def _score_distribution_mismatch(
        self,
        reasons: list[ScoreReason],
        *,
        code: str,
        message: str,
        actual: object,
        distribution: dict[str, float],
        base_score: int,
        baseline_weight: float,
    ) -> bool:
        """Score a mismatch against a baseline distribution dictionary."""
        actual_value = self._normalize_value(actual)
        if actual_value is None or not distribution:
            return False

        common_values = {str(value) for value in distribution}
        if actual_value in common_values:
            return False

        self._add_reason(
            reasons,
            code=code,
            message=message,
            score_delta=self._weighted_score(base_score, baseline_weight),
            evidence={
                "actual": actual_value,
                "common_values": sorted(common_values),
            },
        )
        return True

    def _score_off_hours(
        self,
        reasons: list[ScoreReason],
        target_log: ValidationTargetLog,
        baseline: UserBaseline,
        baseline_weight: float,
    ) -> None:
        """Score off-hours activity with baseline-rate down-weighting."""
        if not target_log.is_off_hours:
            return

        base_score = 10
        if baseline.off_hours_rate >= 0.6:
            base_score = 3
        elif baseline.off_hours_rate >= 0.3:
            base_score = 5

        self._add_reason(
            reasons,
            code="OFF_HOURS",
            message="The log happened during off-hours.",
            score_delta=self._weighted_score(base_score, baseline_weight),
            evidence={"actual": True, "baseline_off_hours_rate": baseline.off_hours_rate},
        )

    def _score_failed_login(
        self,
        reasons: list[ScoreReason],
        target_log: ValidationTargetLog,
        baseline: UserBaseline,
        baseline_weight: float,
    ) -> None:
        """Score failed authentication events."""
        result = (target_log.result or "").upper()
        event_type = (target_log.event_type or "").upper()
        if result not in {"FAILED", "FAIL"} and event_type != "LOGIN_FAIL":
            return

        base_score = 15
        if baseline.failed_rate <= 0.02:
            base_score = 20
        elif baseline.failed_rate >= 0.2:
            base_score = 8

        self._add_reason(
            reasons,
            code="LOGIN_FAILED",
            message="The log records a failed login event.",
            score_delta=self._weighted_score(base_score, baseline_weight),
            evidence={
                "result": target_log.result,
                "event_type": target_log.event_type,
                "baseline_failed_rate": baseline.failed_rate,
            },
        )

    def _build_result(
        self,
        *,
        target_log: ValidationTargetLog,
        baseline_model_version: str | None,
        baseline_created_at: str | None,
        baseline_is_reliable: bool,
        score: int,
        risk_level: str,
        reasons: list[ScoreReason],
        validation_status: str,
        validated_at: str,
        validation_run_id: str,
    ) -> UebaValidationResult:
        """Create the validation result dataclass."""
        source_identity = build_source_identity(target_log)
        return UebaValidationResult(
            validation_id=self._validation_id(
                validation_run_id=validation_run_id,
                source_identity=source_identity,
                target_log=target_log,
                model_version=baseline_model_version,
            ),
            validation_run_id=validation_run_id,
            source_identity=source_identity,
            source_log_id=target_log.id,
            timestamp=str(target_log.timestamp),
            username=target_log.username,
            log_type=target_log.log_type,
            baseline_model_version=baseline_model_version,
            baseline_created_at=baseline_created_at,
            baseline_is_reliable=baseline_is_reliable,
            ueba_score=score,
            ueba_risk_level=risk_level,
            ueba_anomaly_reasons=reasons,
            validation_status=validation_status,
            validated_at=validated_at,
            request_id=target_log.request_id,
        )

    def _validation_id(
        self,
        *,
        validation_run_id: str,
        source_identity: str,
        target_log: ValidationTargetLog,
        model_version: str | None,
    ) -> str:
        """Generate a stable result id from run, source identity, and model version."""
        raw = "|".join(
            [
                validation_run_id,
                source_identity,
                model_version or "",
                str(target_log.timestamp),
                target_log.username,
            ]
        )
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:32]

    def _risk_level(self, score: int) -> str:
        """Map a clamped score to the first-version risk level scale."""
        if score >= 75:
            return "CRITICAL"
        if score >= 50:
            return "HIGH"
        if score >= 25:
            return "MEDIUM"
        return "LOW"

    def _common_values(self, items: Iterable[CountRatioItem]) -> set[str]:
        """Extract normalized common values from CountRatioItem instances."""
        return {
            normalized
            for normalized in (self._normalize_value(item.value) for item in items)
            if normalized is not None
        }

    def _is_long_tail_source_ip_user(self, baseline: UserBaseline) -> bool:
        """Detect users whose source IP baseline is too dispersed for full weight."""
        if baseline.sample_count <= 0:
            return False
        if not baseline.common_source_ips:
            return baseline.sample_count >= 20
        max_ratio = max((item.ratio for item in baseline.common_source_ips), default=0.0)
        return max_ratio < self.LONG_TAIL_SOURCE_IP_RATIO

    def _add_reason(
        self,
        reasons: list[ScoreReason],
        *,
        code: str,
        message: str,
        score_delta: int,
        evidence: dict[str, object],
    ) -> None:
        """Append a non-zero score reason."""
        if score_delta <= 0:
            return
        reasons.append(
            ScoreReason(
                code=code,
                message=message,
                score_delta=score_delta,
                evidence=evidence,
            )
        )

    def _weighted_score(self, score: int, weight: float) -> int:
        """Apply baseline reliability weight while preserving non-zero signals."""
        return max(1, int(round(score * weight)))

    def _clamp_score(self, score: int) -> int:
        """Clamp the final score into the 0-100 range."""
        return max(0, min(100, int(score)))

    def _normalize_value(self, value: object) -> str | None:
        """Normalize optional string-like values for comparison."""
        if value is None:
            return None
        normalized = str(value).strip()
        return normalized or None

    def _now_string(self) -> str:
        """Return a compact UTC timestamp string for validation results."""
        return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


SOURCE_IDENTITY_FIELDS = [
    "timestamp",
    "username",
    "log_type",
    "source_ip",
    "destination_ip",
    "src_country",
    "src_city",
    "vpn_gateway",
    "action",
    "event_type",
    "result",
    "auth_method",
    "client_software",
    "protocol",
    "raw_log",
]


def build_source_identity(target_log: ValidationTargetLog) -> str:
    """Build a stable source log identity without relying on source_log_id."""
    request_id = _normalize_identity_value(target_log.request_id)
    if request_id is not None:
        return f"request_id:{request_id}"

    existing_identity = _normalize_identity_value(target_log.source_identity)
    if existing_identity is not None:
        return existing_identity

    payload = [
        [field_name, _identity_text(getattr(target_log, field_name, None))]
        for field_name in SOURCE_IDENTITY_FIELDS
    ]
    encoded = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    digest = hashlib.sha256(encoded.encode("utf-8")).hexdigest()[:32]
    return f"source_hash:{digest}"


def _identity_text(value: object) -> str:
    """Normalize identity fields for deterministic hashing."""
    if value is None:
        return ""
    return str(value).strip()


def _normalize_identity_value(value: object) -> str | None:
    """Return a non-empty identity string when one is available."""
    normalized = _identity_text(value)
    return normalized or None


__all__ = ["UebaScoreCalculator", "build_source_identity"]
