"""Behavior-backed data provider for the visualization dashboard."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List

from src.behavior.api import analyze_behavior_for_frontend
from src.visualization.mock_data import get_mock_dashboard_data


REPO_ROOT = Path(__file__).resolve().parents[2]
_RISK_ORDER = {
    "unknown": 0,
    "low": 1,
    "medium": 2,
    "high": 3,
}


def load_behavior_payload(path: str = "data/sample_logs.json") -> Dict[str, Any]:
    """Load the behavior payload from JSON file."""
    payload_path = _resolve_payload_path(path)
    with payload_path.open("r", encoding="utf-8") as file:
        payload = json.load(file)

    if not isinstance(payload, dict):
        raise ValueError("behavior payload must be a JSON object")
    return payload


def get_dashboard_data(path: str = "data/sample_logs.json") -> Dict[str, Any]:
    """Load behavior payload and convert the result for dashboard rendering."""
    try:
        payload = load_behavior_payload(path)
    except Exception as exc:
        return _build_mock_fallback(
            code="PAYLOAD_LOAD_ERROR",
            message=f"Failed to load behavior payload from {path}.",
            details=str(exc),
        )

    try:
        result = analyze_behavior_for_frontend(payload)
    except Exception as exc:
        return _build_mock_fallback(
            code="BEHAVIOR_API_ERROR",
            message="Behavior API invocation failed.",
            details=str(exc),
        )

    if not result.get("success"):
        return _build_mock_fallback(
            code="BEHAVIOR_ANALYSIS_FAILED",
            message="Behavior analysis returned an unsuccessful result.",
            details=result.get("error"),
            raw_behavior_result=result,
        )

    try:
        return _transform_behavior_result(result)
    except Exception as exc:
        return _build_mock_fallback(
            code="DASHBOARD_TRANSFORM_ERROR",
            message="Failed to convert behavior analysis result for dashboard rendering.",
            details=str(exc),
            raw_behavior_result=result,
        )


def _resolve_payload_path(path: str) -> Path:
    candidate = Path(path)
    if candidate.is_absolute():
        return candidate
    return REPO_ROOT / candidate


def _transform_behavior_result(result: Dict[str, Any]) -> Dict[str, Any]:
    target_user = result.get("target_user")
    summary = result.get("summary") if isinstance(result.get("summary"), dict) else {}
    anomalies = _normalize_anomalies(result.get("anomalies"))

    max_risk_score = _clamp_score(summary.get("max_risk_score", 0.0))
    overall_risk_level = _normalize_risk_level(summary.get("overall_risk_level"))
    highest_anomaly_risk_level = _highest_risk_level(anomalies)

    user_risk_level = overall_risk_level
    if user_risk_level == "unknown":
        user_risk_level = highest_anomaly_risk_level

    security_score = max(0, min(100, 100 - round(max_risk_score * 100)))
    anomaly_users = _build_anomaly_users(
        target_user=target_user,
        anomalies=anomalies,
        max_risk_score=max_risk_score,
        fallback_risk_level=highest_anomaly_risk_level,
        summary_risk_level=user_risk_level,
    )
    high_risk_users = sum(1 for user in anomaly_users if user["risk_level"] == "high")

    return {
        "source": "behavior",
        "success": True,
        "target_user": target_user,
        "summary": {
            "total_logs": int(summary.get("total_logs", 0) or 0),
            "anomaly_count": len(anomalies),
            "high_risk_users": high_risk_users,
            "security_score": security_score,
            "max_risk_score": max_risk_score,
            "overall_risk_level": user_risk_level,
        },
        "risk_distribution": _build_risk_distribution(anomalies),
        "anomaly_users": anomaly_users,
        "anomaly_events": anomalies,
        "raw_behavior_result": result,
        "error": None,
    }


def _normalize_anomalies(anomalies: Any) -> List[Dict[str, Any]]:
    normalized: List[Dict[str, Any]] = []
    if not isinstance(anomalies, list):
        return normalized

    for item in anomalies:
        if not isinstance(item, dict):
            continue
        normalized.append(
            {
                "timestamp": str(item.get("timestamp") or ""),
                "username": str(item.get("username") or ""),
                "anomaly_type": str(item.get("anomaly_type") or "unknown"),
                "risk_score": _clamp_score(item.get("risk_score", 0.0)),
                "risk_level": _normalize_risk_level(item.get("risk_level")),
                "reason": str(item.get("reason") or ""),
            }
        )
    return normalized


def _build_anomaly_users(
    target_user: Any,
    anomalies: List[Dict[str, Any]],
    max_risk_score: float,
    fallback_risk_level: str,
    summary_risk_level: str,
) -> List[Dict[str, Any]]:
    if not target_user or not anomalies:
        return []

    reasons = [item["reason"] for item in anomalies if item["reason"]]
    risk_level = summary_risk_level if summary_risk_level != "unknown" else fallback_risk_level
    return [
        {
            "username": str(target_user),
            "risk_score": max_risk_score,
            "risk_level": risk_level,
            "anomaly_count": len(anomalies),
            "reasons": reasons,
        }
    ]


def _build_risk_distribution(anomalies: List[Dict[str, Any]]) -> Dict[str, int]:
    distribution = {
        "low": 0,
        "medium": 0,
        "high": 0,
    }
    for item in anomalies:
        risk_level = item.get("risk_level")
        if risk_level in distribution:
            distribution[risk_level] += 1
    return distribution


def _highest_risk_level(anomalies: List[Dict[str, Any]]) -> str:
    highest_level = "unknown"
    for item in anomalies:
        candidate = _normalize_risk_level(item.get("risk_level"))
        if _RISK_ORDER[candidate] > _RISK_ORDER[highest_level]:
            highest_level = candidate
    return highest_level


def _normalize_risk_level(value: Any) -> str:
    normalized = str(value or "").strip().lower()
    if normalized in {"low", "medium", "high"}:
        return normalized
    if normalized == "":
        return "unknown"
    return "unknown"


def _clamp_score(value: Any) -> float:
    try:
        score = float(value)
    except (TypeError, ValueError):
        score = 0.0
    return max(0.0, min(1.0, round(score, 6)))


def _build_mock_fallback(
    *,
    code: str,
    message: str,
    details: Any,
    raw_behavior_result: Dict[str, Any] | None = None,
) -> Dict[str, Any]:
    fallback = get_mock_dashboard_data()
    fallback["raw_behavior_result"] = raw_behavior_result or {}
    fallback["error"] = {
        "code": code,
        "message": message,
        "details": details,
    }
    return fallback
