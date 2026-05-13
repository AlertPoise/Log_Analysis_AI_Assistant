#!/usr/bin/env python3
"""Behavior 前端接口适配层测试。"""

from __future__ import annotations

from typing import Any, Dict, List

import pytest

from src.behavior.api import analyze_behavior_for_frontend
from tests.behavior.conftest import TARGET_USER


EXPECTED_TOP_LEVEL_FIELDS = {
    "success",
    "target_user",
    "baseline",
    "profile",
    "anomalies",
    "summary",
    "error",
}


def _assert_stable_top_level_fields(result: Dict[str, Any]) -> None:
    """断言返回顶层字段稳定。"""
    assert set(result.keys()) == EXPECTED_TOP_LEVEL_FIELDS


def test_analyze_behavior_for_frontend_returns_success_structure(
    history_logs: List[Dict[str, Any]],
    suspicious_detection_logs: List[Dict[str, Any]],
) -> None:
    """正常输入应返回稳定成功结构。"""
    result = analyze_behavior_for_frontend(
        {
            "target_user": TARGET_USER,
            "history_logs": history_logs,
            "detection_logs": suspicious_detection_logs,
        }
    )

    _assert_stable_top_level_fields(result)
    assert result["success"] is True
    assert result["target_user"] == TARGET_USER
    assert isinstance(result["baseline"], dict)
    assert isinstance(result["profile"], dict)
    assert isinstance(result["anomalies"], list)
    assert isinstance(result["summary"], dict)
    assert result["summary"]["total_logs"] == len(history_logs) + len(suspicious_detection_logs)
    assert result["summary"]["anomaly_count"] == len(result["anomalies"])
    assert result["error"] is None


def test_analyze_behavior_for_frontend_handles_anomalous_detection_logs(
    history_logs: List[Dict[str, Any]],
    suspicious_detection_logs: List[Dict[str, Any]],
) -> None:
    """明显偏离历史的检测日志应至少返回稳定异常列表结构。"""
    result = analyze_behavior_for_frontend(
        {
            "target_user": TARGET_USER,
            "history_logs": history_logs,
            "detection_logs": suspicious_detection_logs,
        }
    )

    assert result["success"] is True
    assert isinstance(result["anomalies"], list)
    assert result["summary"]["anomaly_count"] == len(result["anomalies"])

    if result["anomalies"]:
        first_anomaly = result["anomalies"][0]
        assert {
            "timestamp",
            "username",
            "anomaly_type",
            "risk_score",
            "risk_level",
            "reason",
        }.issubset(first_anomaly.keys())
        assert first_anomaly["username"] == TARGET_USER
        assert 0.0 <= float(first_anomaly["risk_score"]) <= 1.0


def test_analyze_behavior_for_frontend_handles_empty_logs() -> None:
    """空日志输入不应崩溃。"""
    result = analyze_behavior_for_frontend(
        {
            "target_user": TARGET_USER,
            "history_logs": [],
            "detection_logs": [],
        }
    )

    _assert_stable_top_level_fields(result)
    assert result["success"] is True
    assert result["target_user"] == TARGET_USER
    assert result["anomalies"] == []
    assert result["summary"]["total_logs"] == 0
    assert result["summary"]["anomaly_count"] == 0
    assert result["summary"]["overall_risk_level"] == "low"


def test_analyze_behavior_for_frontend_rejects_missing_target_user() -> None:
    """缺少 target_user 应返回固定错误结构。"""
    result = analyze_behavior_for_frontend(
        {
            "history_logs": [],
            "detection_logs": [],
        }
    )

    _assert_stable_top_level_fields(result)
    assert result["success"] is False
    assert result["target_user"] is None
    assert result["error"] == {
        "code": "INVALID_INPUT",
        "message": "缺少 target_user",
    }


@pytest.mark.parametrize(
    ("field_name", "field_value"),
    [
        ("history_logs", "not-a-list"),
        ("detection_logs", "not-a-list"),
    ],
)
def test_analyze_behavior_for_frontend_rejects_invalid_log_list_types(
    field_name: str,
    field_value: str,
) -> None:
    """history_logs 和 detection_logs 类型错误时应安全失败。"""
    payload = {
        "target_user": TARGET_USER,
        "history_logs": [],
        "detection_logs": [],
    }
    payload[field_name] = field_value

    result = analyze_behavior_for_frontend(payload)

    _assert_stable_top_level_fields(result)
    assert result["success"] is False
    assert result["error"]["code"] == "INVALID_INPUT"


def test_analyze_behavior_for_frontend_keeps_structure_stable_on_timestamp_and_shape_issues() -> None:
    """坏时间戳和混合日志形态不应破坏接口结构。"""
    success_result = analyze_behavior_for_frontend(
        {
            "target_user": TARGET_USER,
            "history_logs": [
                {
                    "timestamp": "bad-time",
                    "source_ip": "10.0.0.1",
                    "location": "北京",
                    "action": "LOGIN_SUCCESS",
                    "status": "SUCCESS",
                },
                {
                    "timestamp": "2026-04-02 09:00:00",
                    "username": TARGET_USER,
                    "source_ip": "10.0.0.2",
                    "location": "上海",
                    "action": "LOGIN_FAILED",
                    "status": "FAILED",
                },
                "skip-me",
            ],
            "detection_logs": [
                {
                    "timestamp": "also-bad-time",
                    "username": TARGET_USER,
                    "source_ip": "192.168.1.50",
                    "location": "上海",
                    "action": "LOGIN_FAILED",
                    "status": "FAILED",
                }
            ],
        }
    )
    failure_result = analyze_behavior_for_frontend({"target_user": "   "})

    _assert_stable_top_level_fields(success_result)
    _assert_stable_top_level_fields(failure_result)
    assert success_result["success"] is True
    assert failure_result["success"] is False
