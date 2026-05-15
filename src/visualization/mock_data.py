"""Visualization fallback data."""

from __future__ import annotations

from copy import deepcopy
from typing import Any, Dict


_MOCK_DASHBOARD_DATA: Dict[str, Any] = {
    "source": "mock",
    "success": False,
    "target_user": None,
    "summary": {
        "total_logs": 0,
        "anomaly_count": 0,
        "high_risk_users": 0,
        "security_score": 100,
        "max_risk_score": 0.0,
        "overall_risk_level": "unknown",
    },
    "risk_distribution": {
        "low": 0,
        "medium": 0,
        "high": 0,
    },
    "anomaly_users": [],
    "anomaly_events": [],
    "raw_behavior_result": {},
    "error": {
        "code": "MOCK_FALLBACK",
        "message": "Behavior data is unavailable.",
    },
}


def get_mock_dashboard_data() -> Dict[str, Any]:
    """Return a stable fallback structure for dashboard rendering."""
    return deepcopy(_MOCK_DASHBOARD_DATA)
