"""用户画像模块。"""

from collections import Counter
from datetime import datetime
from typing import Any, Dict, List, Optional

from src.behavior.baseline import BehaviorBaseline
from src.behavior.normalizer import (
    build_sort_key,
    get_action,
    get_first_value,
    get_location,
    is_login_event,
    normalize_behavior_log,
    parse_timestamp_value,
    require_timestamp_value,
)
from src.behavior.schemas import BehaviorBaselineResult, UserProfileResult
from src.utils.config import settings
from src.utils.helpers import format_datetime

try:
    from src.utils.logger import get_logger
except Exception:  # pragma: no cover - 兼容最小测试环境
    import logging

    def get_logger(name: str):
        return logging.getLogger(name)


logger = get_logger(__name__)


class UserProfile:
    """单个用户的行为画像。"""

    def __init__(
        self,
        username: str,
        time_window_hours: Optional[int] = None,
        common_top_n: int = 5,
    ) -> None:
        """初始化用户画像。"""
        self.username = username
        self.created_at = datetime.now()
        self.updated_at = self.created_at
        self.login_times: List[str] = []
        self.common_ips: List[str] = []
        self.common_locations: List[str] = []
        self.user_agents: List[str] = []
        self.api_call_frequency = 0.0
        self.failed_login_count = 0
        self.total_actions = 0
        self.baseline: Dict[str, Any] = {}

        self._logs: List[Dict[str, Any]] = []
        self._user_agent_counter: Counter[str] = Counter()
        self._has_behavior_data = False
        self._baseline_builder = BehaviorBaseline(
            username=username,
            time_window_hours=time_window_hours or settings.behavior_time_window_hours,
            common_top_n=common_top_n,
        )

    def add_login_record(
        self,
        timestamp: datetime,
        ip: str,
        location: Optional[str] = None,
        status: str = "SUCCESS",
        user_agent: Optional[str] = None,
    ) -> None:
        """添加登录行为。"""
        parsed_timestamp = self._require_timestamp(timestamp)
        log_record: Dict[str, Any] = {
            "timestamp": parsed_timestamp,
            "username": self.username,
            "source_ip": str(ip),
            "action": "LOGIN",
            "status": str(status).strip().upper(),
        }
        if location:
            log_record["location"] = str(location)
        if user_agent:
            log_record["user_agent"] = str(user_agent)

        self._append_log(log_record)

    def add_api_call(
        self,
        timestamp: datetime,
        endpoint: str,
        ip: Optional[str] = None,
        location: Optional[str] = None,
        method: Optional[str] = None,
        response_time: Optional[float] = None,
        user_agent: Optional[str] = None,
        status: str = "SUCCESS",
    ) -> None:
        """添加 API 调用行为。"""
        parsed_timestamp = self._require_timestamp(timestamp)
        log_record: Dict[str, Any] = {
            "timestamp": parsed_timestamp,
            "username": self.username,
            "action": "API_CALL",
            "endpoint": str(endpoint),
            "status": str(status).strip().upper(),
        }
        if ip:
            log_record["source_ip"] = str(ip)
        if location:
            log_record["location"] = str(location)
        if method:
            log_record["method"] = str(method).strip().upper()
        if response_time is not None:
            log_record["response_time"] = float(response_time)
        if user_agent:
            log_record["user_agent"] = str(user_agent)

        self._append_log(log_record)

    def add_log(self, log: Dict[str, Any]) -> None:
        """按结构化日志自动写入画像。"""
        normalized_log = normalize_behavior_log(
            log,
            require_timestamp=True,
        )
        if normalized_log is None:
            logger.debug("用户画像跳过无法标准化的日志")
            return
        if normalized_log.get("username") != self.username:
            logger.debug("用户画像忽略非目标用户日志: {}", normalized_log.get("username"))
            return
        self._append_log(dict(normalized_log))

    def build_from_logs(self, logs: List[Dict[str, Any]]) -> "UserProfile":
        """从日志列表批量构建画像。"""
        sorted_logs = sorted(
            (log for log in logs if isinstance(log, dict)),
            key=self._sort_key,
        )
        for log in sorted_logs:
            try:
                self.add_log(log)
            except ValueError:
                logger.warning("用户画像跳过时间戳无效的日志")
                continue

        self.calculate_baseline()
        return self

    def calculate_baseline(self) -> BehaviorBaselineResult:
        """计算行为基线，并同步画像摘要字段。"""
        self.baseline = self._baseline_builder.build_baseline(self._logs)
        self.common_ips = list(self.baseline.get("common_ips", []))
        self.common_locations = list(self.baseline.get("common_locations", []))
        self.api_call_frequency = float(self.baseline.get("api_call_avg_per_hour", 0.0))
        self.failed_login_count = int(self.baseline.get("failed_login_count", 0))
        self.user_agents = self._top_keys(self._user_agent_counter)
        return dict(self.baseline)

    def get_profile(self) -> UserProfileResult:
        """返回可序列化画像。"""
        if not self.baseline:
            self.calculate_baseline()

        return {
            "username": self.username,
            "created_at": format_datetime(self.created_at),
            "updated_at": format_datetime(self.updated_at),
            "login_times": list(self.login_times),
            "common_ips": list(self.common_ips),
            "common_locations": list(self.common_locations),
            "user_agents": list(self.user_agents),
            "api_call_frequency": self.api_call_frequency,
            "activity_hours": dict(self.baseline.get("activity_hours", {})),
            "failed_login_count": self.failed_login_count,
            "total_actions": self.total_actions,
            "baseline": dict(self.baseline),
        }

    def to_dict(self) -> UserProfileResult:
        """`get_profile` 的别名。"""
        return self.get_profile()

    def _append_log(self, log_record: Dict[str, Any]) -> None:
        """写入标准化日志并刷新状态。"""
        timestamp = self._require_timestamp(log_record.get("timestamp"))
        self._logs.append(log_record)
        self.total_actions = len(self._logs)
        self._register_timestamp(timestamp)
        self._track_user_agent(log_record.get("user_agent"))

        if self._is_login_event(log_record):
            self.login_times.append(format_datetime(timestamp, "%H:%M"))

        self.baseline = {}

    def _register_timestamp(self, timestamp: datetime) -> None:
        """按最早/最晚时间维护画像时间边界。"""
        if not self._has_behavior_data:
            self.created_at = timestamp
            self.updated_at = timestamp
            self._has_behavior_data = True
            return

        if timestamp < self.created_at:
            self.created_at = timestamp
        if timestamp > self.updated_at:
            self.updated_at = timestamp

    def _track_user_agent(self, user_agent: Optional[Any]) -> None:
        """记录 user agent 出现次数。"""
        if user_agent in (None, ""):
            return
        self._user_agent_counter[str(user_agent)] += 1

    def _require_timestamp(self, timestamp: Any) -> datetime:
        """解析时间字段，失败时抛出异常。"""
        return require_timestamp_value(timestamp, "Invalid timestamp for user profile log")

    def _safe_parse_timestamp(self, timestamp: Any) -> Optional[datetime]:
        """安全解析时间字段。"""
        return parse_timestamp_value(timestamp)

    def _sort_key(self, log: Dict[str, Any]) -> tuple:
        """构建稳定排序键，保证批量导入顺序可预期。"""
        return build_sort_key(log)

    def _get_action(self, log: Dict[str, Any]) -> Optional[str]:
        """读取标准化 action。"""
        return get_action(log)

    def _is_login_event(self, log: Dict[str, Any]) -> bool:
        """判断日志是否为登录行为。"""
        return is_login_event(log)

    def _get_first_value(
        self,
        log: Dict[str, Any],
        field_names: tuple[str, ...],
    ) -> Optional[Any]:
        """按字段优先级读取第一个非空值。"""
        return get_first_value(log, field_names)

    def _get_location(self, log: Dict[str, Any]) -> Optional[str]:
        """读取位置字段，兼容 VPN 输出。"""
        return get_location(log)

    def _top_keys(self, counter: Counter[str]) -> List[str]:
        """按频率返回高频字段。"""
        return [
            key
            for key, _ in sorted(
                counter.items(),
                key=lambda item: (-item[1], item[0]),
            )[: self._baseline_builder.common_top_n]
        ]
