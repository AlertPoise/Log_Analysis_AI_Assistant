#!/usr/bin/env python3
"""
Behavior 模块总流程测试。


测试目标：
1. 验证行为日志标准化；
2. 验证用户行为基线构建；
3. 验证用户画像生成；
4. 验证异常行为检测；
5. 验证 BehaviorAnalysisService 统一分析流程。
"""

from __future__ import annotations

import json
from typing import Any, Dict, List

import pytest

from src.behavior.normalizer import normalize_behavior_log
from src.behavior.service import BehaviorAnalysisService
from src.utils.config import settings

TARGET_USER = "zhangsan"
OTHER_USER = "lisi"


def _print_section(title: str) -> None:
    print("\n" + "=" * 70)
    print(title)
    print("=" * 70)


def _print_step(message: str) -> None:
    print(f"\n[测试步骤] {message}")


def _print_json(title: str, data: object) -> None:
    print(f"\n{title}:")
    print(json.dumps(data, ensure_ascii=False, indent=2, default=str))


def _sample_logs(logs: List[Dict[str, Any]], limit: int = 2) -> List[Dict[str, Any]]:
    return [
        {
            "timestamp": log.get("timestamp"),
            "username": log.get("username") or log.get("user") or log.get("account"),
            "source_ip": (
                log.get("source_ip") or log.get("src_ip") or log.get("remote_addr") or log.get("ip")
            ),
            "location": log.get("location") or log.get("src_city"),
            "action": log.get("action") or log.get("event_type") or log.get("log_type"),
            "endpoint": log.get("endpoint") or log.get("uri"),
            "status": log.get("status") or log.get("result"),
        }
        for log in logs[:limit]
    ]


def _summarize_baseline(baseline: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "username": baseline.get("username"),
        "sample_count": baseline.get("sample_count"),
        "common_ips": baseline.get("common_ips"),
        "common_locations": baseline.get("common_locations"),
        "failed_login_count": baseline.get("failed_login_count"),
        "failed_login_rate": baseline.get("failed_login_rate"),
    }


def _summarize_profile(profile: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "username": profile.get("username"),
        "total_actions": profile.get("total_actions"),
        "common_ips": profile.get("common_ips"),
        "common_locations": profile.get("common_locations"),
        "login_times": profile.get("login_times"),
    }


def _summarize_anomalies(anomalies: List[Dict[str, Any]]) -> Dict[str, Any]:
    matched_rules = sorted(
        {
            rule
            for anomaly in anomalies
            for rule in anomaly.get("context", {}).get("matched_rules", [])
        }
    )
    return {
        "anomaly_count": len(anomalies),
        "alert_count": sum(1 for item in anomalies if item.get("is_alert")),
        "matched_rules": matched_rules,
        "risk_levels": sorted({item.get("risk_level") for item in anomalies if item.get("risk_level")}),
    }


def test_behavior_service_workflow(
    history_logs: List[Dict[str, Any]],
    suspicious_detection_logs: List[Dict[str, Any]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """BehaviorAnalysisService 应能统一输出画像、基线、异常和摘要。"""
    _print_section("Behavior 模块服务摘要流程测试")
    _print_json(
        "输入摘要",
        {
            "target_user": TARGET_USER,
            "other_user": OTHER_USER,
            "history_log_count": len(history_logs),
            "detection_log_count": len(suspicious_detection_logs),
            "history_log_samples": _sample_logs(history_logs),
            "detection_log_samples": _sample_logs(suspicious_detection_logs),
        },
    )
    _print_step("调用 BehaviorAnalysisService.analyze_user 生成完整分析结果")

    monkeypatch.setattr(settings, "min_samples_for_profile", 3)
    result = BehaviorAnalysisService().analyze_user(
        TARGET_USER,
        history_logs,
        detection_logs=suspicious_detection_logs,
    )

    _print_step("检查 baseline、profile、anomalies 和 summary 的关键结果")
    _print_json("Baseline 摘要", _summarize_baseline(result["baseline"]))
    _print_json("Profile 摘要", _summarize_profile(result["profile"]))
    _print_json("Anomalies 摘要", _summarize_anomalies(result["anomalies"]))
    _print_json("Summary", result["summary"])

    assert result["username"] == TARGET_USER
    assert result["baseline"]["username"] == TARGET_USER
    assert result["profile"]["username"] == TARGET_USER
    assert result["summary"]["anomaly_count"] == len(result["anomalies"])
    assert result["summary"]["alert_count"] >= 1
    assert result["summary"]["highest_risk_level"] in {"LOW", "MEDIUM", "HIGH", "CRITICAL"}
    assert result["baseline"]["sample_count"] == 5
    assert all(anomaly["username"] == TARGET_USER for anomaly in result["anomalies"])

    _print_step("服务摘要流程测试通过")


def test_behavior_end_to_end_workflow(
    history_logs: List[Dict[str, Any]],
    suspicious_detection_logs: List[Dict[str, Any]],
) -> None:
    """原始/结构化日志应能完成 behavior 主流程。"""
    _print_section("Behavior 模块端到端流程测试")
    _print_json(
        "原始输入摘要",
        {
            "target_user": TARGET_USER,
            "other_user": OTHER_USER,
            "history_log_count": len(history_logs),
            "detection_log_count": len(suspicious_detection_logs),
            "history_log_samples": _sample_logs(history_logs),
            "detection_log_samples": _sample_logs(suspicious_detection_logs),
        },
    )
    _print_step("步骤 1：标准化历史日志和检测日志")

    normalized_history = [
        normalized
        for normalized in (normalize_behavior_log(log) for log in history_logs)
        if normalized is not None
    ]
    normalized_detection = [
        normalized
        for normalized in (normalize_behavior_log(log) for log in suspicious_detection_logs)
        if normalized is not None
    ]

    _print_json(
        "标准化结果摘要",
        {
            "normalized_history_count": len(normalized_history),
            "normalized_detection_count": len(normalized_detection),
            "normalized_history_samples": _sample_logs(normalized_history),
            "normalized_detection_samples": _sample_logs(normalized_detection),
        },
    )

    assert any(log["username"] == TARGET_USER for log in normalized_history)
    assert any(log["username"] == OTHER_USER for log in normalized_history)

    _print_step("步骤 2：执行 analyze_user，完成基线、画像、异常和汇总分析")
    result = BehaviorAnalysisService().analyze_user(
        TARGET_USER,
        normalized_history,
        detection_logs=normalized_detection,
    )

    _print_json("Baseline 摘要", _summarize_baseline(result["baseline"]))
    _print_json("Profile 摘要", _summarize_profile(result["profile"]))
    _print_json("Anomalies 摘要", _summarize_anomalies(result["anomalies"]))
    _print_json("Summary", result["summary"])

    assert result["baseline"]["sample_count"] == 5
    assert result["profile"]["total_actions"] == 5
    assert result["summary"]["anomaly_count"] == len(result["anomalies"])
    assert all(anomaly["username"] == TARGET_USER for anomaly in result["anomalies"])

    _print_step("端到端流程测试通过")
