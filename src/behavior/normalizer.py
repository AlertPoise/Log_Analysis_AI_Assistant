"""行为模块共享的日志归一化工具。"""

from datetime import datetime
from typing import Any, Dict, Optional

from src.behavior.schemas import NormalizedBehaviorLog
from src.utils.helpers import parse_timestamp
from src.utils.helpers import format_datetime

try:
    from src.utils.logger import get_logger
except Exception:  # pragma: no cover - 兼容最小测试环境
    import logging

    def get_logger(name: str):
        return logging.getLogger(name)


logger = get_logger(__name__)


def parse_timestamp_value(value: Any) -> Optional[datetime]:
    """安全解析时间字段。"""
    if isinstance(value, datetime):
        return value
    if isinstance(value, str) and value.strip():
        return parse_timestamp(value)
    return None


def require_timestamp_value(value: Any, error_message: str) -> datetime:
    """解析时间字段，失败时抛出异常。"""
    parsed = parse_timestamp_value(value)
    if parsed is None:
        raise ValueError(error_message)
    return parsed


def get_first_value(
    log: Dict[str, Any],
    field_names: tuple[str, ...],
) -> Optional[Any]:
    """按字段优先级获取第一个非空值。"""
    for field_name in field_names:
        value = log.get(field_name)
        if value not in (None, ""):
            return value
    return None


def get_username(log: Dict[str, Any]) -> Optional[str]:
    """读取标准化用户名字段。"""
    username = get_first_value(log, ("username", "user", "account"))
    if username is None:
        return None

    normalized = str(username).strip()
    return normalized or None


def get_ip_address(log: Dict[str, Any]) -> Optional[str]:
    """读取标准化 IP 字段。"""
    ip_address = get_first_value(log, ("source_ip", "remote_addr", "ip", "src_ip"))
    if ip_address:
        return str(ip_address)
    return None


def get_location(log: Dict[str, Any]) -> Optional[str]:
    """读取标准化位置字段。"""
    location = get_first_value(log, ("location", "src_city"))
    if location:
        return str(location)
    return None


def get_endpoint(log: Dict[str, Any]) -> Optional[str]:
    """读取标准化 endpoint 字段。"""
    endpoint = get_first_value(log, ("endpoint", "uri"))
    if endpoint:
        return str(endpoint)
    return None


def get_action(log: Dict[str, Any]) -> Optional[str]:
    """读取标准化 action。"""
    action = get_first_value(log, ("action", "event_type", "log_type"))
    if not action:
        return None

    normalized = str(action).strip().upper()
    if normalized.startswith("LOGIN_"):
        return "LOGIN"
    return normalized


def get_status(log: Dict[str, Any]) -> Optional[str]:
    """读取标准化状态字段。"""
    status = get_first_value(log, ("status", "result"))
    if status not in (None, ""):
        return str(status).strip().upper()

    event_type = str(log.get("event_type", "")).strip().upper()
    if event_type.endswith("SUCCESS"):
        return "SUCCESS"
    if event_type.endswith("FAIL"):
        return "FAIL"
    return None


def is_login_event(log: Dict[str, Any]) -> bool:
    """判断日志是否为登录事件。"""
    action = get_action(log)
    log_type = str(log.get("log_type", "")).upper()
    event_type = str(log.get("event_type", "")).upper()
    return (
        action in {"LOGIN", "AUTH", "VPN_LOGIN"}
        or "LOGIN" in log_type
        or event_type.startswith("LOGIN_")
    )


def is_failed_status(status_or_log: Any) -> bool:
    """判断状态是否表示失败。"""
    if isinstance(status_or_log, dict):
        status = get_first_value(status_or_log, ("status", "result", "event_type"))
    else:
        status = status_or_log

    if status is None:
        return False

    normalized = str(status).strip().upper()
    return normalized in {"FAIL", "FAILED", "FAILURE", "ERROR", "LOGIN_FAIL"}


def build_sort_key(log: Dict[str, Any]) -> tuple:
    """构建稳定排序键。"""
    timestamp = parse_timestamp_value(log.get("timestamp")) or datetime.max
    log_id = log.get("id")
    return (timestamp, int(log_id) if isinstance(log_id, int) else 0)


def normalize_behavior_log(
    log: Dict[str, Any],
    fallback_username: Optional[str] = None,
    require_timestamp: bool = False,
) -> Optional[NormalizedBehaviorLog]:
    """将原始日志标准化为 behavior 模块统一结构。

    Args:
        log: 原始结构化日志。
        fallback_username: 日志未带用户名时可选的回退用户名。
        require_timestamp: 是否要求时间戳必须可解析。

    Returns:
        标准化后的日志；若关键字段缺失或非法则返回 ``None``。
    """
    if not isinstance(log, dict):
        logger.debug("跳过非字典日志，无法标准化")
        return None

    username = get_username(log)
    if username is None and fallback_username not in (None, ""):
        username = str(fallback_username).strip() or None
    if not username:
        logger.debug("跳过缺少用户名的日志")
        return None

    timestamp = parse_timestamp_value(log.get("timestamp"))
    if timestamp is None:
        if require_timestamp:
            logger.warning("日志时间戳无效，无法标准化")
            raise ValueError("Invalid timestamp for behavior log")
        logger.debug("跳过时间戳无效的日志")
        return None

    normalized: NormalizedBehaviorLog = {
        "timestamp": format_datetime(timestamp),
        "username": username,
    }

    if isinstance(log.get("id"), int):
        normalized["id"] = int(log["id"])

    log_type = get_first_value(log, ("log_type",))
    if log_type not in (None, ""):
        normalized["log_type"] = str(log_type).strip()

    action = get_action(log)
    if action:
        normalized["action"] = action

    status = get_status(log)
    if status:
        normalized["status"] = status

    ip_address = get_ip_address(log)
    if ip_address:
        normalized["source_ip"] = ip_address

    location = get_location(log)
    if location:
        normalized["location"] = location

    endpoint = get_endpoint(log)
    if endpoint:
        normalized["endpoint"] = endpoint

    user_agent = get_first_value(log, ("user_agent", "client_software"))
    if user_agent not in (None, ""):
        normalized["user_agent"] = str(user_agent)

    for field_name in (
        "method",
        "response_time",
        "dept",
        "role",
        "protocol",
        "auth_method",
        "vpn_gateway",
        "session_id",
        "fail_reason",
        "raw_log",
        "parser",
        "parse_status",
    ):
        value = log.get(field_name)
        if value not in (None, ""):
            normalized[field_name] = value

    return normalized
