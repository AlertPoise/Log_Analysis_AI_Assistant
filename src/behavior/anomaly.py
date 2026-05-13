"""异常检测模块。"""

from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

from src.behavior.normalizer import (
    build_sort_key,
    get_action,
    get_endpoint,
    get_first_value,
    get_ip_address,
    get_location,
    get_username,
    is_failed_status,
    is_login_event,
    parse_timestamp_value,
    require_timestamp_value,
)
from src.behavior.schemas import AnomalyResult
from src.utils.config import settings
from src.utils.helpers import format_datetime, generate_id, get_risk_level

try:
    from src.utils.logger import get_logger
except Exception:  # pragma: no cover - 兼容最小测试环境
    import logging

    def get_logger(name: str):
        return logging.getLogger(name)


logger = get_logger(__name__)


class AnomalyDetector:
    """基于规则和统计的异常检测器。"""

    RULE_WEIGHTS = {
        "UNUSUAL_TIME": 0.20,
        "UNUSUAL_IP": 0.25,
        "UNUSUAL_LOCATION": 0.15,
        "HIGH_FREQUENCY": 0.20,
        "FAILED_LOGIN_SPIKE": 0.25,
        "MULTI_IP_LOGIN": 0.30,
        "SENSITIVE_ACTION": 0.15,
    }

    def __init__(
        self,
        threshold: Optional[float] = None,
        cold_start_discount: float = 0.6,
    ) -> None:
        """初始化异常检测器。"""
        self.threshold = threshold if threshold is not None else settings.anomaly_threshold
        self.cold_start_discount = cold_start_discount

    def detect_unusual_time(self, timestamp: datetime, baseline: Dict[str, Any]) -> bool:
        """检测非常用时间。"""
        parsed_timestamp = self._require_timestamp(timestamp)
        common_hours = baseline.get("common_hours")
        if common_hours:
            return parsed_timestamp.hour not in {int(hour) for hour in common_hours}

        activity_hours = baseline.get("activity_hours", {})
        if activity_hours:
            return parsed_timestamp.hour not in {int(hour) for hour in activity_hours.keys()}

        return False

    def detect_unusual_ip(self, ip: str, baseline_ips: List[str]) -> bool:
        """检测非常用 IP。"""
        if not ip or not baseline_ips:
            return False
        return str(ip) not in {str(value) for value in baseline_ips}

    def detect_high_frequency(self, current_count: int, baseline_avg: float) -> bool:
        """检测高频行为。"""
        if current_count <= 0:
            return False
        if baseline_avg <= 0:
            return current_count >= 2
        return current_count >= max(2, baseline_avg * 2)

    def detect_unusual_location(self, location: str, baseline_locations: List[str]) -> bool:
        """检测非常用地理位置。"""
        if not location or not baseline_locations:
            return False
        return str(location) not in {str(value) for value in baseline_locations}

    def detect_failed_login_spike(self, current_failed: int, baseline_failed_avg: float) -> bool:
        """检测失败登录突增。"""
        if current_failed <= 0:
            return False
        if baseline_failed_avg <= 0:
            return current_failed >= 2
        return current_failed >= max(2, baseline_failed_avg * 2)

    def detect_multi_ip_login(
        self,
        logs: List[Dict[str, Any]],
        window_minutes: int = 30,
    ) -> List[Dict[str, Any]]:
        """检测短时间窗口内的多 IP 登录。"""
        login_logs = [
            log
            for log in sorted(logs, key=self._sort_key)
            if self._is_login_event(log) and self._get_timestamp(log) is not None
        ]
        events: List[Dict[str, Any]] = []

        for index, start_log in enumerate(login_logs):
            start_timestamp = self._require_timestamp(start_log.get("timestamp"))
            username = get_username(start_log)
            if not username:
                logger.debug("多 IP 登录检测跳过缺少用户名的日志")
                continue
            related_logs = [start_log]
            ip_set = {str(get_ip_address(start_log) or "")}

            for next_log in login_logs[index + 1 :]:
                next_timestamp = self._require_timestamp(next_log.get("timestamp"))
                if next_timestamp - start_timestamp > timedelta(minutes=window_minutes):
                    break
                if get_username(next_log) != username:
                    continue
                related_logs.append(next_log)
                ip_address = get_ip_address(next_log)
                if ip_address:
                    ip_set.add(ip_address)

            ip_set.discard("")
            if len(ip_set) <= 1 or len(related_logs) <= 1:
                continue

            related_ids = [
                int(log["id"])
                for log in related_logs
                if isinstance(log.get("id"), int)
            ]
            candidate = {
                "username": username,
                "timestamp": format_datetime(start_timestamp),
                "related_logs": related_ids,
                "source_ips": sorted(ip_set),
                "window_minutes": window_minutes,
            }
            if not self._is_redundant_multi_ip_event(candidate, events):
                events.append(candidate)

        return events

    def calculate_anomaly_score(
        self,
        anomalies: List[Dict[str, Any]],
        baseline_reliable: bool = True,
    ) -> float:
        """计算多异常加权评分。"""
        score = 0.0
        for anomaly in anomalies:
            anomaly_type = anomaly.get("anomaly_type")
            if anomaly_type in self.RULE_WEIGHTS:
                score += self.RULE_WEIGHTS[anomaly_type]
            elif "weight" in anomaly:
                score += float(anomaly["weight"])

        if not baseline_reliable:
            score *= self.cold_start_discount

        return round(min(1.0, score), 6)

    def detect_log(self, log: Dict[str, Any], baseline: Dict[str, Any]) -> Optional[AnomalyResult]:
        """检测单条日志是否存在异常。"""
        if not isinstance(log, dict):
            logger.debug("异常检测跳过非字典日志")
            return None

        timestamp = self._get_timestamp(log)
        username = get_username(log)
        if timestamp is None or not username:
            logger.debug("异常检测跳过缺少用户名或时间戳无效的日志")
            return None

        matched_rules: List[str] = []
        rule_details: List[Dict[str, Any]] = []
        ip_address = get_ip_address(log)
        location = get_location(log)
        endpoint = get_endpoint(log)

        if self.detect_unusual_time(timestamp, baseline):
            rule_details.append({"anomaly_type": "UNUSUAL_TIME"})
            matched_rules.append("UNUSUAL_TIME")
        if ip_address and self.detect_unusual_ip(str(ip_address), baseline.get("common_ips", [])):
            rule_details.append({"anomaly_type": "UNUSUAL_IP"})
            matched_rules.append("UNUSUAL_IP")
        if location and self.detect_unusual_location(str(location), baseline.get("common_locations", [])):
            rule_details.append({"anomaly_type": "UNUSUAL_LOCATION"})
            matched_rules.append("UNUSUAL_LOCATION")
        if endpoint and self._is_sensitive_action(str(endpoint)) and {
            "UNUSUAL_TIME",
            "UNUSUAL_IP",
        }.intersection(matched_rules):
            rule_details.append({"anomaly_type": "SENSITIVE_ACTION"})
            matched_rules.append("SENSITIVE_ACTION")

        if not matched_rules:
            return None

        score = self.calculate_anomaly_score(
            rule_details,
            baseline_reliable=bool(baseline.get("is_reliable", True)),
        )
        meets_threshold = score >= self.threshold
        return {
            "anomaly_id": self._build_anomaly_id(log, matched_rules),
            "username": username,
            "timestamp": format_datetime(timestamp),
            "anomaly_type": matched_rules[0],
            "anomaly_score": score,
            "risk_level": get_risk_level(score),
            "is_alert": meets_threshold,
            "description": self._build_description(
                timestamp=timestamp,
                matched_rules=matched_rules,
                ip_address=ip_address,
                location=location,
                endpoint=endpoint,
            ),
            "source_ip": ip_address or "",
            "location": location or "",
            "context": {
                "matched_rules": list(matched_rules),
                "baseline_common_hours": list(baseline.get("common_hours", [])),
                "baseline_common_ips": list(baseline.get("common_ips", [])),
                "baseline_common_locations": list(baseline.get("common_locations", [])),
                "meets_threshold": meets_threshold,
            },
            "related_logs": [log["id"]] if isinstance(log.get("id"), int) else [],
        }

    def detect_batch(self, logs: List[Dict[str, Any]], baseline: Dict[str, Any]) -> List[AnomalyResult]:
        """检测一批日志并返回异常列表。"""
        sorted_logs = sorted((log for log in logs if isinstance(log, dict)), key=self._sort_key)
        anomalies: List[Dict[str, Any]] = []

        for log in sorted_logs:
            anomaly = self.detect_log(log, baseline)
            if anomaly is not None:
                anomalies.append(anomaly)

        self._merge_multi_ip_events(anomalies, sorted_logs, baseline)
        self._append_high_frequency_event(anomalies, sorted_logs, baseline)
        self._append_failed_login_spike_event(anomalies, sorted_logs, baseline)

        return anomalies

    def _merge_multi_ip_events(
        self,
        anomalies: List[Dict[str, Any]],
        logs: List[Dict[str, Any]],
        baseline: Dict[str, Any],
    ) -> None:
        """将多 IP 登录事件合并到现有异常中。"""
        multi_ip_events = self.detect_multi_ip_login(logs)
        for event in multi_ip_events:
            related_ids = set(event["related_logs"])
            target = next(
                (
                    anomaly
                    for anomaly in anomalies
                    if set(anomaly.get("related_logs", [])) & related_ids
                ),
                None,
            )
            if target is None:
                target = {
                    "anomaly_id": generate_id(
                        f"{event['username']}-{event['timestamp']}-MULTI_IP_LOGIN"
                    ),
                    "username": event["username"],
                    "timestamp": event["timestamp"],
                    "anomaly_type": "MULTI_IP_LOGIN",
                    "anomaly_score": 0.0,
                    "risk_level": "INFO",
                    "is_alert": False,
                    "description": "短时间窗口内出现多个来源 IP 登录。",
                    "source_ip": event["source_ips"][0] if event["source_ips"] else "",
                    "location": "",
                    "context": {
                        "matched_rules": [],
                        "baseline_common_hours": list(baseline.get("common_hours", [])),
                        "baseline_common_ips": list(baseline.get("common_ips", [])),
                        "baseline_common_locations": list(baseline.get("common_locations", [])),
                        "meets_threshold": False,
                    },
                    "related_logs": list(event["related_logs"]),
                }
                anomalies.append(target)

            self._append_rule(
                target,
                "MULTI_IP_LOGIN",
                baseline_reliable=bool(baseline.get("is_reliable", True)),
                related_logs=event["related_logs"],
                context_updates={
                    "window_minutes": event["window_minutes"],
                    "source_ips": sorted(
                        set(target.get("context", {}).get("source_ips", []))
                        | set(event["source_ips"])
                    ),
                },
            )
            if target.get("description"):
                target["description"] = self._build_description(
                    timestamp=self._require_timestamp(target["timestamp"]),
                    matched_rules=target["context"]["matched_rules"],
                    ip_address=target.get("source_ip") or None,
                    location=target.get("location") or None,
                    endpoint=None,
                )

    def _append_high_frequency_event(
        self,
        anomalies: List[Dict[str, Any]],
        logs: List[Dict[str, Any]],
        baseline: Dict[str, Any],
    ) -> None:
        """为高频 API 调用补充聚合异常。"""
        api_logs = [
            log for log in logs if self._get_action(log) == "API_CALL"
        ]
        api_count = len(api_logs)
        baseline_avg = float(baseline.get("api_call_avg_per_hour", 0.0))

        if not self.detect_high_frequency(api_count, baseline_avg):
            return

        target_log = api_logs[-1] if api_logs else None
        if target_log is None:
            return

        anomaly = self.detect_log(target_log, baseline) or {
            "anomaly_id": self._build_anomaly_id(target_log, ["HIGH_FREQUENCY"]),
            "username": str(target_log.get("username", "")),
            "timestamp": format_datetime(self._require_timestamp(target_log.get("timestamp"))),
            "anomaly_type": "HIGH_FREQUENCY",
            "anomaly_score": 0.0,
            "risk_level": "INFO",
            "is_alert": False,
            "description": "",
            "source_ip": str(get_ip_address(target_log) or ""),
            "location": str(get_location(target_log) or ""),
            "context": {
                "matched_rules": [],
                "baseline_common_hours": list(baseline.get("common_hours", [])),
                "baseline_common_ips": list(baseline.get("common_ips", [])),
                "baseline_common_locations": list(baseline.get("common_locations", [])),
                "meets_threshold": False,
            },
            "related_logs": [int(log["id"]) for log in api_logs if isinstance(log.get("id"), int)],
        }

        if anomaly not in anomalies:
            anomalies.append(anomaly)

        self._append_rule(
            anomaly,
            "HIGH_FREQUENCY",
            baseline_reliable=bool(baseline.get("is_reliable", True)),
            related_logs=[int(log["id"]) for log in api_logs if isinstance(log.get("id"), int)],
            context_updates={
                "current_count": api_count,
                "baseline_avg": baseline_avg,
            },
        )
        anomaly["description"] = self._build_description(
            timestamp=self._require_timestamp(anomaly["timestamp"]),
            matched_rules=anomaly["context"]["matched_rules"],
            ip_address=anomaly.get("source_ip") or None,
            location=anomaly.get("location") or None,
            endpoint=str(get_endpoint(target_log) or ""),
        )

    def _append_failed_login_spike_event(
        self,
        anomalies: List[Dict[str, Any]],
        logs: List[Dict[str, Any]],
        baseline: Dict[str, Any],
    ) -> None:
        """为失败登录突增补充聚合异常。"""
        failed_login_logs = [
            log
            for log in logs
            if self._is_login_event(log) and self._is_failed_status(log)
        ]
        current_failed = len(failed_login_logs)
        baseline_failed = float(baseline.get("failed_login_count", 0))

        if not self.detect_failed_login_spike(current_failed, baseline_failed):
            return

        target_log = failed_login_logs[-1]
        anomaly = self.detect_log(target_log, baseline) or {
            "anomaly_id": self._build_anomaly_id(target_log, ["FAILED_LOGIN_SPIKE"]),
            "username": str(target_log.get("username", "")),
            "timestamp": format_datetime(self._require_timestamp(target_log.get("timestamp"))),
            "anomaly_type": "FAILED_LOGIN_SPIKE",
            "anomaly_score": 0.0,
            "risk_level": "INFO",
            "is_alert": False,
            "description": "",
            "source_ip": str(get_ip_address(target_log) or ""),
            "location": str(get_location(target_log) or ""),
            "context": {
                "matched_rules": [],
                "baseline_common_hours": list(baseline.get("common_hours", [])),
                "baseline_common_ips": list(baseline.get("common_ips", [])),
                "baseline_common_locations": list(baseline.get("common_locations", [])),
                "meets_threshold": False,
            },
            "related_logs": [
                int(log["id"])
                for log in failed_login_logs
                if isinstance(log.get("id"), int)
            ],
        }

        if anomaly not in anomalies:
            anomalies.append(anomaly)

        self._append_rule(
            anomaly,
            "FAILED_LOGIN_SPIKE",
            baseline_reliable=bool(baseline.get("is_reliable", True)),
            related_logs=[int(log["id"]) for log in failed_login_logs if isinstance(log.get("id"), int)],
            context_updates={
                "current_failed": current_failed,
                "baseline_failed": baseline_failed,
            },
        )
        anomaly["description"] = self._build_description(
            timestamp=self._require_timestamp(anomaly["timestamp"]),
            matched_rules=anomaly["context"]["matched_rules"],
            ip_address=anomaly.get("source_ip") or None,
            location=anomaly.get("location") or None,
            endpoint=None,
        )

    def _append_rule(
        self,
        anomaly: Dict[str, Any],
        anomaly_type: str,
        baseline_reliable: bool,
        related_logs: Optional[List[int]] = None,
        context_updates: Optional[Dict[str, Any]] = None,
    ) -> None:
        """向异常结果追加规则并重算评分。"""
        context = anomaly.setdefault("context", {})
        matched_rules = context.setdefault("matched_rules", [])
        if anomaly_type not in matched_rules:
            matched_rules.append(anomaly_type)

        if related_logs:
            anomaly["related_logs"] = sorted(
                set(anomaly.get("related_logs", [])) | set(related_logs)
            )
        if context_updates:
            context.update(context_updates)

        rule_details = [{"anomaly_type": rule_name} for rule_name in matched_rules]
        score = self.calculate_anomaly_score(
            rule_details,
            baseline_reliable=baseline_reliable,
        )
        anomaly["anomaly_score"] = score
        anomaly["risk_level"] = get_risk_level(score)
        anomaly["is_alert"] = score >= self.threshold
        context["meets_threshold"] = anomaly["is_alert"]

    def _is_redundant_multi_ip_event(
        self,
        candidate: Dict[str, Any],
        existing_events: List[Dict[str, Any]],
    ) -> bool:
        """判断候选多 IP 事件是否被已有事件覆盖。"""
        candidate_timestamp = self._require_timestamp(candidate["timestamp"])
        candidate_source_ips = set(candidate["source_ips"])
        candidate_window = int(candidate.get("window_minutes", 30))

        for existing in existing_events:
            if existing.get("username") != candidate.get("username"):
                continue

            existing_timestamp = self._require_timestamp(existing["timestamp"])
            existing_window = int(existing.get("window_minutes", 30))
            existing_source_ips = set(existing.get("source_ips", []))

            if candidate_source_ips == existing_source_ips and candidate_timestamp == existing_timestamp:
                return True
            if candidate_source_ips and candidate_source_ips.issubset(existing_source_ips):
                if candidate_timestamp <= existing_timestamp + timedelta(minutes=existing_window):
                    return True
            if existing_source_ips and existing_source_ips.issubset(candidate_source_ips):
                if existing_timestamp <= candidate_timestamp + timedelta(minutes=candidate_window):
                    return True

        return False

    def _build_anomaly_id(self, log: Dict[str, Any], matched_rules: List[str]) -> str:
        """构造异常 ID。"""
        username = str(log.get("username", ""))
        log_id = str(log.get("id", ""))
        timestamp = format_datetime(self._require_timestamp(log.get("timestamp")))
        return generate_id(f"{username}-{timestamp}-{log_id}-{'-'.join(matched_rules)}")

    def _build_description(
        self,
        timestamp: datetime,
        matched_rules: List[str],
        ip_address: Optional[str],
        location: Optional[str],
        endpoint: Optional[str],
    ) -> str:
        """构造简要异常描述。"""
        parts: List[str] = []
        if "UNUSUAL_TIME" in matched_rules:
            parts.append(f"{timestamp.hour} 点发生非常用时间访问")
        if "UNUSUAL_IP" in matched_rules and ip_address:
            parts.append(f"来源 IP {ip_address} 不在常用范围内")
        if "UNUSUAL_LOCATION" in matched_rules and location:
            parts.append(f"位置 {location} 不在常用范围内")
        if "MULTI_IP_LOGIN" in matched_rules:
            parts.append("短时间内出现多 IP 登录")
        if "HIGH_FREQUENCY" in matched_rules:
            parts.append("API 调用频率明显高于基线")
        if "FAILED_LOGIN_SPIKE" in matched_rules:
            parts.append("失败登录数量明显高于历史基线")
        if "SENSITIVE_ACTION" in matched_rules and endpoint:
            parts.append(f"访问了敏感接口 {endpoint}")

        return "；".join(parts) if parts else "检测到异常访问行为。"

    def _is_sensitive_action(self, endpoint: str) -> bool:
        """判断 endpoint 是否属于敏感操作。"""
        sensitive_markers = ("/api/admin", "/admin", "/export", "/delete", "/audit")
        endpoint_lower = endpoint.lower()
        return any(marker in endpoint_lower for marker in sensitive_markers)

    def _require_timestamp(self, timestamp: Any) -> datetime:
        """解析时间字段，失败时抛出异常。"""
        return require_timestamp_value(timestamp, "Invalid timestamp for anomaly detection")

    def _get_timestamp(self, log: Dict[str, Any]) -> Optional[datetime]:
        """安全读取日志时间。"""
        return parse_timestamp_value(log.get("timestamp"))

    def _sort_key(self, log: Dict[str, Any]) -> tuple:
        """为日志提供稳定排序键。"""
        return build_sort_key(log)

    def _get_first_value(
        self,
        log: Dict[str, Any],
        field_names: tuple[str, ...],
    ) -> Optional[Any]:
        """按优先级获取第一个非空字段。"""
        return get_first_value(log, field_names)

    def _get_action(self, log: Dict[str, Any]) -> Optional[str]:
        """读取标准化 action。"""
        return get_action(log)

    def _is_login_event(self, log: Dict[str, Any]) -> bool:
        """判断是否为登录事件。"""
        return is_login_event(log)

    def _is_failed_status(self, status_or_log: Any) -> bool:
        """判断状态是否表示失败。"""
        return is_failed_status(status_or_log)

    def _get_location(self, log: Dict[str, Any]) -> Optional[str]:
        """读取位置字段，兼容 VPN 输出。"""
        return get_location(log)
