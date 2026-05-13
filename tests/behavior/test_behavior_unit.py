#!/usr/bin/env python3
"""Behavior 模块核心单元测试。"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List

import pytest

from src.behavior.anomaly import AnomalyDetector
from src.behavior.normalizer import get_username, normalize_behavior_log
from src.behavior.repository import InMemoryBehaviorRepository
from src.behavior.service import BehaviorAnalysisService
from src.behavior.user_profile import UserProfile
from src.utils.config import settings

from tests.behavior.conftest import TARGET_USER


class TestBehaviorNormalizer:
    """测试行为日志标准化。"""

    def test_normalize_behavior_log_maps_required_fields_and_rejects_invalid_logs(self):
        """应统一核心字段，并跳过缺用户名或坏时间戳日志。"""
        normalized = normalize_behavior_log(
            {
                "id": 9,
                "timestamp": "2026-04-01T09:00:00",
                "user": TARGET_USER,
                "src_ip": "10.0.0.1",
                "src_city": "北京",
                "event_type": "LOGIN_FAIL",
                "result": "FAIL",
                "uri": "/api/orders",
                "client_software": "OpenVPN",
            }
        )

        assert normalized is not None
        assert normalized["username"] == TARGET_USER
        assert normalized["source_ip"] == "10.0.0.1"
        assert normalized["location"] == "北京"
        assert normalized["endpoint"] == "/api/orders"
        assert normalized["status"] == "FAIL"
        assert normalize_behavior_log({"timestamp": "2026-04-01 09:00:00"}) is None
        assert normalize_behavior_log({"username": TARGET_USER, "timestamp": "bad-time"}) is None


class TestBehaviorBaseline:
    """测试用户行为基线。"""

    def test_build_baseline_uses_only_target_user_logs(self, baseline: Dict[str, Any]):
        """基线应只统计目标用户的正常历史。"""
        assert baseline["username"] == TARGET_USER
        assert baseline["sample_count"] == 5
        assert "10.0.0.1" in baseline["common_ips"]
        assert "北京" in baseline["common_locations"]
        assert baseline["action_frequency"] == {"LOGIN": 4, "API_CALL": 1}
        assert baseline["api_frequency"] == {"/api/orders": 1}
        assert baseline["failed_login_count"] == 1
        assert baseline["failed_login_rate"] == 0.25


class TestUserProfile:
    """测试用户画像。"""

    def test_build_profile_uses_only_valid_target_user_logs(
        self,
        history_logs: List[Dict[str, Any]],
    ):
        """画像应只汇总目标用户的有效日志。"""
        profile = UserProfile(TARGET_USER).build_from_logs(history_logs).get_profile()

        assert profile["username"] == TARGET_USER
        assert profile["total_actions"] == 5
        assert "10.0.0.1" in profile["common_ips"]
        assert "北京" in profile["common_locations"]
        assert {"09:00", "09:30", "10:30", "11:00"}.issubset(set(profile["login_times"]))


class TestAnomalyDetector:
    """测试异常检测。"""

    def test_detect_log_flags_unusual_time_ip_location_and_sensitive_action(
        self,
        baseline: Dict[str, Any],
        suspicious_detection_logs: List[Dict[str, Any]],
    ):
        """单条高风险日志应触发核心异常规则。"""
        sensitive_log = next(
            log
            for log in suspicious_detection_logs
            if log.get("endpoint") == "/api/admin/export"
        )

        anomaly = AnomalyDetector(threshold=0.7).detect_log(sensitive_log, baseline)

        assert anomaly is not None
        assert anomaly["username"] == TARGET_USER
        assert all(
            rule in anomaly["context"]["matched_rules"]
            for rule in ("UNUSUAL_TIME", "UNUSUAL_IP", "UNUSUAL_LOCATION", "SENSITIVE_ACTION")
        )
        assert 0.0 <= anomaly["anomaly_score"] <= 1.0
        assert anomaly["risk_level"] in {"HIGH", "CRITICAL"}
        assert anomaly["description"]
        assert anomaly["is_alert"] is True

    def test_detect_batch_flags_multi_ip_high_frequency_and_failed_spike(
        self,
        baseline: Dict[str, Any],
        suspicious_detection_logs: List[Dict[str, Any]],
    ):
        """批量检测应识别多 IP、高频访问和失败登录突增。"""
        user_detection_logs = [
            log for log in suspicious_detection_logs if get_username(log) == TARGET_USER
        ]
        anomalies = AnomalyDetector().detect_batch(user_detection_logs, baseline)
        matched_rules = {
            rule
            for anomaly in anomalies
            for rule in anomaly["context"].get("matched_rules", [])
        }

        assert "MULTI_IP_LOGIN" in matched_rules
        assert "HIGH_FREQUENCY" in matched_rules
        assert "FAILED_LOGIN_SPIKE" in matched_rules
        assert all(0.0 <= anomaly["anomaly_score"] <= 1.0 for anomaly in anomalies)
        assert all(anomaly["description"] for anomaly in anomalies)


class TestBehaviorAnalysisService:
    """测试统一服务入口。"""

    def test_analyze_user_returns_complete_result(
        self,
        history_logs: List[Dict[str, Any]],
        suspicious_detection_logs: List[Dict[str, Any]],
        monkeypatch: pytest.MonkeyPatch,
    ):
        """服务应输出 baseline、profile、anomalies 和 summary。"""
        monkeypatch.setattr(settings, "min_samples_for_profile", 3)
        result = BehaviorAnalysisService().analyze_user(
            TARGET_USER,
            history_logs,
            detection_logs=suspicious_detection_logs,
        )

        assert result["username"] == TARGET_USER
        assert result["baseline"]["username"] == TARGET_USER
        assert result["profile"]["username"] == TARGET_USER
        assert result["summary"]["anomaly_count"] == len(result["anomalies"])
        assert result["summary"]["alert_count"] >= 1
        assert all(anomaly["username"] == TARGET_USER for anomaly in result["anomalies"])

    def test_detect_anomalies_builds_baseline_from_filtered_user_logs(
        self,
        history_logs: List[Dict[str, Any]],
        monkeypatch: pytest.MonkeyPatch,
    ):
        """detect_anomalies 构建 baseline 时应只使用目标用户日志。"""
        service = BehaviorAnalysisService()
        captured: Dict[str, Any] = {}
        original_build_baseline = BehaviorAnalysisService.build_baseline

        def spy_build_baseline(self, username: str, logs: List[Dict[str, Any]]):
            captured["username"] = username
            captured["logs"] = list(logs)
            return original_build_baseline(self, username, logs)

        monkeypatch.setattr(BehaviorAnalysisService, "build_baseline", spy_build_baseline)
        result = service.detect_anomalies(TARGET_USER, history_logs)

        assert result == []
        assert captured["username"] == TARGET_USER
        assert len(captured["logs"]) == 5
        assert all(
            normalize_behavior_log(log).get("username") == TARGET_USER
            for log in captured["logs"]
            if normalize_behavior_log(log) is not None
        )

    def test_analyze_user_handles_unknown_user(self, history_logs: List[Dict[str, Any]]):
        """未知用户应返回安全默认结果。"""
        result = BehaviorAnalysisService().analyze_user("nobody", history_logs)

        assert result["username"] == "nobody"
        assert result["baseline"]["sample_count"] == 0
        assert result["profile"]["total_actions"] == 0
        assert result["anomalies"] == []
        assert result["summary"]["anomaly_count"] == 0


class TestBehaviorRepository:
    """测试内存仓储。"""

    def test_in_memory_repository_supports_basic_fetch_and_save(
        self,
        history_logs: List[Dict[str, Any]],
        baseline: Dict[str, Any],
    ):
        """内存仓储应支持基础读写。"""
        repository = InMemoryBehaviorRepository(history_logs)
        profile = UserProfile(TARGET_USER).build_from_logs(history_logs).get_profile()
        anomalies = BehaviorAnalysisService().detect_anomalies(
            TARGET_USER,
            history_logs,
            baseline=baseline,
        )

        history = repository.fetch_user_history(TARGET_USER)
        recent = repository.fetch_recent_user_events(TARGET_USER, window_minutes=45)
        filtered = repository.fetch_user_history(
            TARGET_USER,
            start_time=datetime(2026, 4, 1, 9, 30, 0),
            end_time=datetime(2026, 4, 1, 10, 30, 0),
            limit=2,
        )
        repository.save_baseline(baseline)
        repository.save_profile(profile)
        repository.save_anomalies(anomalies)

        assert len(history) == 5
        assert len(recent) == 2
        assert [log["id"] for log in filtered] == [2, 3]
        assert repository.baselines[TARGET_USER]["sample_count"] == 5
        assert repository.profiles[TARGET_USER]["total_actions"] == 5
        assert repository.anomalies == anomalies
