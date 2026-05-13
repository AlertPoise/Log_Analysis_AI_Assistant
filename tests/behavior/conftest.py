"""Behavior 模块测试共享 fixture。"""

from __future__ import annotations

from typing import Any, Dict, List

import pytest

from src.behavior.baseline import BehaviorBaseline
from src.utils.config import settings


TARGET_USER = "zhangsan"
OTHER_USER = "lisi"


@pytest.fixture
def history_logs() -> List[Dict[str, Any]]:
    """提供目标用户的正常历史行为和混合用户样本。"""
    return [
        {
            "id": 1,
            "timestamp": "2026-04-01 09:00:00",
            "username": TARGET_USER,
            "src_ip": "10.0.0.1",
            "src_city": "北京",
            "event_type": "LOGIN_SUCCESS",
            "result": "SUCCESS",
            "client_software": "FortiClient 7.2",
        },
        {
            "id": 2,
            "timestamp": "2026-04-01 09:30:00",
            "account": TARGET_USER,
            "remote_addr": "10.0.0.1",
            "location": "北京",
            "action": "LOGIN",
            "status": "SUCCESS",
            "user_agent": "FortiClient 7.2",
        },
        {
            "id": 3,
            "timestamp": "2026-04-01 10:00:00",
            "user": TARGET_USER,
            "ip": "10.0.0.1",
            "location": "北京",
            "action": "API_CALL",
            "endpoint": "/api/orders",
            "status": "SUCCESS",
            "method": "GET",
        },
        {
            "id": 4,
            "timestamp": "2026-04-01 10:30:00",
            "username": TARGET_USER,
            "src_ip": "10.0.0.1",
            "src_city": "北京",
            "event_type": "LOGIN_FAIL",
            "result": "FAIL",
            "fail_reason": "bad password",
        },
        {
            "id": 5,
            "timestamp": "2026-04-01 11:00:00",
            "username": TARGET_USER,
            "source_ip": "10.0.0.1",
            "location": "北京",
            "action": "LOGIN",
            "status": "SUCCESS",
        },
        {
            "id": 6,
            "timestamp": "2026-04-01 11:15:00",
            "username": OTHER_USER,
            "source_ip": "8.8.8.8",
            "location": "上海",
            "action": "LOGIN",
            "status": "SUCCESS",
        },
        {
            "id": 7,
            "timestamp": "2026-04-01 11:20:00",
            "source_ip": "172.16.0.1",
            "location": "未知",
            "action": "LOGIN",
            "status": "SUCCESS",
        },
    ]


@pytest.fixture
def suspicious_detection_logs() -> List[Dict[str, Any]]:
    """提供目标用户的可疑检测样本。"""
    return [
        {
            "id": 101,
            "timestamp": "2026-04-02 03:00:00",
            "username": TARGET_USER,
            "source_ip": "8.8.8.8",
            "location": "广州",
            "action": "LOGIN",
            "status": "SUCCESS",
        },
        {
            "id": 102,
            "timestamp": "2026-04-02 03:10:00",
            "username": TARGET_USER,
            "source_ip": "8.8.4.4",
            "location": "广州",
            "action": "API_CALL",
            "endpoint": "/api/admin/export",
            "status": "SUCCESS",
        },
        {
            "id": 103,
            "timestamp": "2026-04-02 09:00:00",
            "username": TARGET_USER,
            "source_ip": "10.0.0.1",
            "location": "北京",
            "action": "LOGIN",
            "status": "SUCCESS",
        },
        {
            "id": 104,
            "timestamp": "2026-04-02 09:10:00",
            "username": TARGET_USER,
            "source_ip": "10.0.0.2",
            "location": "北京",
            "action": "LOGIN",
            "status": "SUCCESS",
        },
        {
            "id": 105,
            "timestamp": "2026-04-02 09:20:00",
            "username": TARGET_USER,
            "source_ip": "10.0.0.1",
            "location": "北京",
            "action": "API_CALL",
            "endpoint": "/api/orders",
            "status": "SUCCESS",
        },
        {
            "id": 106,
            "timestamp": "2026-04-02 09:25:00",
            "username": TARGET_USER,
            "source_ip": "10.0.0.1",
            "location": "北京",
            "action": "API_CALL",
            "endpoint": "/api/orders",
            "status": "SUCCESS",
        },
        {
            "id": 107,
            "timestamp": "2026-04-02 09:30:00",
            "username": TARGET_USER,
            "src_ip": "10.0.0.1",
            "src_city": "北京",
            "event_type": "LOGIN_FAIL",
            "result": "FAIL",
        },
        {
            "id": 108,
            "timestamp": "2026-04-02 09:35:00",
            "username": TARGET_USER,
            "src_ip": "10.0.0.1",
            "src_city": "北京",
            "event_type": "LOGIN_FAIL",
            "result": "FAIL",
        },
        {
            "id": 109,
            "timestamp": "2026-04-02 09:40:00",
            "username": OTHER_USER,
            "source_ip": "203.0.113.10",
            "location": "深圳",
            "action": "LOGIN",
            "status": "SUCCESS",
        },
    ]


@pytest.fixture
def invalid_logs() -> List[Dict[str, Any]]:
    """提供异常输入样本。"""
    return [
        {"timestamp": "bad-time", "username": TARGET_USER},
        {"timestamp": "2026-04-02 03:00:00"},
        {"timestamp": "2026-04-02 03:05:00", "source_ip": "1.1.1.1"},
        {
            "timestamp": "bad-time",
            "username": TARGET_USER,
            "source_ip": "10.0.0.3",
            "action": "LOGIN",
            "status": "SUCCESS",
        },
    ]


@pytest.fixture
def baseline(
    history_logs: List[Dict[str, Any]],
    monkeypatch: pytest.MonkeyPatch,
) -> Dict[str, Any]:
    """提供目标用户行为基线。"""
    monkeypatch.setattr(settings, "min_samples_for_profile", 3)
    return BehaviorBaseline(TARGET_USER).build_baseline(history_logs)
