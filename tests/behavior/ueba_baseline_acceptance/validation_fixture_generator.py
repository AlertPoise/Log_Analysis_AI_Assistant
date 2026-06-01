"""Validation 验收专用 fixture 生成器。

为 validation e2e 生成少量确定性场景日志，与 baseline May/June 窗口分离。

场景：
- fixture_user_validation_normal : 匹配 baseline 的正常行为 → LOW / VALIDATED
- fixture_user_validation_combo  : 多信号组合高风险行为 → HIGH / VALIDATED
- fixture_user_validation_nobase : 无 baseline → NO_BASELINE
"""

from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from .id_generator import deterministic_log_id


def _ts(base: datetime, offset_minutes: int = 0) -> str:
    """生成固定时间戳字符串。"""
    return (base + timedelta(minutes=offset_minutes)).strftime("%Y-%m-%d %H:%M:%S")


def generate_validation_fixture_logs(
    fixture_id: str,
    seed: int = 42,
    *,
    validation_start: str = "2026-07-01 00:00:00",
    validation_end: str = "2026-07-02 00:00:00",
) -> list[dict[str, Any]]:
    """生成 validation 验收专用 fixture 日志。

    返回 list[dict]，每条包含 logs_structured 所需字段。
    """
    base = datetime.strptime(validation_start, "%Y-%m-%d %H:%M:%S")
    rows: list[dict[str, Any]] = []
    user_indexes: dict[str, int] = {}

    def _row(username, **overrides):
        if username not in user_indexes:
            user_indexes[username] = len(user_indexes) + 1
        r = {
            "id": deterministic_log_id(
                namespace="validation",
                seed=seed,
                user_index=user_indexes[username],
                row_index=len(rows),
            ),
            "timestamp": _ts(base, 0),
            "log_type": "vpn",
            "username": username,
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
            "session_duration_sec": 300,
            "bytes_sent": 1024,
            "bytes_recv": 4096,
            "request_id": f"{fixture_id}-{username}",
            "raw_log": "",
            "parser": "",
            "parse_status": "",
            "collected_at": _ts(base, 0),
            "source": "",
            "dept": "",
            "role": "",
            "fail_reason": "",
            "session_id": "",
        }
        r.update(overrides)
        rows.append(r)

    # ---- 场景 1: 正常用户（匹配 baseline）----
    # baseline common: source_ip=10.10.90.1, country=CN, city=Shanghai,
    # gateway=gw-1, auth=password, client=OpenVPN, protocol=tcp, dest=10.0.1.1
    for i in range(5):
        _row(
            "fixture_user_validation_normal",
            timestamp=_ts(base.replace(hour=9), i * 10),
            request_id=f"{fixture_id}-normal-{i}",
            source_ip="10.10.90.1",
            destination_ip="10.0.1.1",
            vpn_gateway="gw-1",
            src_country="CN",
            src_city="Shanghai",
            auth_method="password",
            client_software="OpenVPN",
            protocol="tcp",
            action="LOGIN",
            result="SUCCESS",
            is_off_hours=False,
            is_unusual_ip=False,
        )

    # ---- 场景 2: 组合高风险用户 ----
    # new country + new city + new IP + failed login + off_hours + unusual_ip
    # NEW_SOURCE_COUNTRY(30) + NEW_SOURCE_CITY(8) + NEW_SOURCE_IP(12) + LOGIN_FAILED(20) + OFF_HOURS + UNUSUAL_IP(20) ≥ 50 (HIGH)
    for i in range(5):
        _row(
            "fixture_user_validation_combo",
            timestamp=_ts(base, i * 10 + 1),
            request_id=f"{fixture_id}-combo-{i}",
            source_ip=f"198.51.100.{10 + i}",
            src_country="DE",
            src_city="Berlin",
            vpn_gateway="gw-9",
            auth_method="mfa-push",
            client_software="UnknownVPN",
            protocol="udp",
            result="FAILED",
            event_type="LOGIN_FAIL",
            is_off_hours=True,
            is_unusual_ip=True,
            fail_reason="PASSWORD_ERROR",
        )

    # ---- 场景 3: 无 baseline 用户 ----
    for i in range(3):
        _row(
            "fixture_user_validation_nobase",
            timestamp=_ts(base, i * 10 + 2),
            source_ip=f"203.0.113.{10 + i}",
            request_id=f"{fixture_id}-nobase-{i}",
        )

    return rows


def validation_fixture_expected_rows() -> int:
    """返回默认场景的 fixture 行数。"""
    return 5 + 5 + 3  # normal + combo + nobase


__all__ = ["generate_validation_fixture_logs", "validation_fixture_expected_rows"]
