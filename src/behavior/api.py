"""面向前端的 Behavior 接口适配层。"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, TypedDict

from src.behavior.normalizer import get_username, parse_timestamp_value
from src.behavior.service import BehaviorAnalysisService
from src.utils.helpers import format_datetime

try:
    from src.utils.logger import get_logger
except Exception:  # pragma: no cover - 兼容最小测试环境
    import logging

    def get_logger(name: str):
        return logging.getLogger(name)


logger = get_logger(__name__)


class _ValidatedPayload(TypedDict):
    target_user: str
    history_logs: List[Any]
    detection_logs: List[Any]


class _PayloadValidationError(ValueError):
    """输入校验失败。"""

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


def analyze_behavior_for_frontend(payload: dict) -> dict:
    """接收前端 JSON/dict，返回稳定的 Behavior 分析结果。"""
    try:
        validated_payload = _validate_payload(payload)
        target_user = validated_payload["target_user"]
        history_logs = _prepare_logs(validated_payload["history_logs"], target_user)
        detection_logs = _prepare_logs(validated_payload["detection_logs"], target_user)
        total_logs = len(history_logs) + len(detection_logs)

        result = BehaviorAnalysisService().analyze_user(
            target_user,
            history_logs,
            detection_logs=detection_logs,
        )
        anomalies = [
            _format_anomaly(anomaly)
            for anomaly in result.get("anomalies", [])
            if isinstance(anomaly, dict)
        ]
        max_risk_score = max((item["risk_score"] for item in anomalies), default=0.0)

        return {
            "success": True,
            "target_user": target_user,
            "baseline": _clone_mapping(result.get("baseline")),
            "profile": _clone_mapping(result.get("profile")),
            "anomalies": anomalies,
            "summary": {
                "total_logs": total_logs,
                "anomaly_count": len(anomalies),
                "max_risk_score": round(max_risk_score, 6),
                "overall_risk_level": _risk_level_from_score(max_risk_score, has_anomalies=bool(anomalies)),
            },
            "error": None,
        }
    except _PayloadValidationError as exc:
        return _error_response("INVALID_INPUT", exc.message)
    except Exception:
        logger.exception("Behavior 前端接口分析失败")
        return _error_response("ANALYSIS_ERROR", "Behavior 分析失败，请稍后重试。")


def _validate_payload(payload: Any) -> _ValidatedPayload:
    """校验前端请求体。"""
    if not isinstance(payload, dict):
        raise _PayloadValidationError("payload 必须是 dict")

    target_user = payload.get("target_user")
    if target_user is None or str(target_user).strip() == "":
        raise _PayloadValidationError("缺少 target_user")

    history_logs = payload.get("history_logs", [])
    detection_logs = payload.get("detection_logs", [])

    if history_logs is None:
        history_logs = []
    if detection_logs is None:
        detection_logs = []

    if not isinstance(history_logs, list):
        raise _PayloadValidationError("history_logs 必须是 list")
    if not isinstance(detection_logs, list):
        raise _PayloadValidationError("detection_logs 必须是 list")

    return {
        "target_user": str(target_user).strip(),
        "history_logs": history_logs,
        "detection_logs": detection_logs,
    }


def _prepare_logs(logs: List[Any], target_user: str) -> List[Dict[str, Any]]:
    """尽量兼容前端输入，生成 service 可消费的日志列表。"""
    prepared_logs: List[Dict[str, Any]] = []

    for index, item in enumerate(logs):
        if not isinstance(item, dict):
            logger.warning(f"Behavior API 跳过非字典日志: index={index}")
            continue

        log = dict(item)
        if not get_username(log):
            log["username"] = target_user

        parsed_timestamp = parse_timestamp_value(log.get("timestamp"))
        if parsed_timestamp is not None:
            log["timestamp"] = format_datetime(parsed_timestamp)

        prepared_logs.append(log)

    return prepared_logs


def _format_anomaly(anomaly: Dict[str, Any]) -> Dict[str, Any]:
    """将内部异常结构转换为前端稳定字段。"""
    risk_score = _extract_risk_score(anomaly)
    return {
        "timestamp": str(anomaly.get("timestamp") or ""),
        "username": str(anomaly.get("username") or ""),
        "anomaly_type": _format_anomaly_type(anomaly.get("anomaly_type")),
        "risk_score": round(risk_score, 6),
        "risk_level": _risk_level_from_score(risk_score, has_anomalies=True),
        "reason": _build_reason(anomaly),
    }


def _extract_risk_score(anomaly: Dict[str, Any]) -> float:
    """读取并限制异常评分。"""
    value = anomaly.get("anomaly_score", 0.0)
    try:
        numeric_value = float(value)
    except (TypeError, ValueError):
        numeric_value = 0.0
    return max(0.0, min(1.0, numeric_value))


def _format_anomaly_type(value: Any) -> str:
    """将内部大写规则名转换为前端更稳定的 snake_case。"""
    if value in (None, ""):
        return "unknown"
    return str(value).strip().lower()


def _build_reason(anomaly: Dict[str, Any]) -> str:
    """构造前端可直接展示的原因说明。"""
    description = anomaly.get("description")
    if isinstance(description, str) and description.strip():
        return description.strip()

    fallback_reasons = {
        "UNUSUAL_TIME": "用户在非常用时间活动",
        "UNUSUAL_IP": "用户使用了非常用来源 IP",
        "UNUSUAL_LOCATION": "用户在非常用地点活动",
        "MULTI_IP_LOGIN": "用户短时间内出现多个 IP 登录",
        "HIGH_FREQUENCY": "用户的 API 调用频率明显高于基线",
        "FAILED_LOGIN_SPIKE": "用户失败登录次数明显高于基线",
        "SENSITIVE_ACTION": "用户触发了敏感操作访问",
    }
    anomaly_type = str(anomaly.get("anomaly_type") or "").strip().upper()
    return fallback_reasons.get(anomaly_type, "检测到异常访问行为")


def _risk_level_from_score(score: float, has_anomalies: bool) -> str:
    """按前端约定阈值返回风险等级。"""
    if score >= 0.8:
        return "high"
    if score >= 0.5:
        return "medium"
    if has_anomalies:
        return "low"
    return "low"


def _clone_mapping(value: Any) -> Dict[str, Any]:
    """复制返回字典，避免暴露内部可变对象。"""
    if not isinstance(value, dict):
        return {}

    cloned: Dict[str, Any] = {}
    for key, item in value.items():
        if isinstance(item, dict):
            cloned[key] = _clone_mapping(item)
        elif isinstance(item, list):
            cloned[key] = [
                _clone_mapping(child) if isinstance(child, dict) else child
                for child in item
            ]
        else:
            cloned[key] = item
    return cloned


def _error_response(code: str, message: str) -> Dict[str, Any]:
    """构造稳定失败响应。"""
    return {
        "success": False,
        "target_user": None,
        "baseline": {},
        "profile": {},
        "anomalies": [],
        "summary": {
            "total_logs": 0,
            "anomaly_count": 0,
            "max_risk_score": 0.0,
            "overall_risk_level": "unknown",
        },
        "error": {
            "code": code,
            "message": message,
        },
    }
