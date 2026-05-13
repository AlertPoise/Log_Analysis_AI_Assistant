"""行为基线计算模块。

本模块只负责从内存中的结构化日志计算用户行为统计结果，不直接访问
Kafka、ClickHouse 或其他外部服务。
"""
from collections import Counter
from datetime import datetime
from typing import Any, Dict, Iterable, List, Optional, Tuple

from src.behavior.normalizer import (
    get_action,
    get_first_value,
    get_ip_address,
    get_location,
    get_username,
    is_failed_status,
    is_login_event,
    parse_timestamp_value,
)
from src.behavior.schemas import BehaviorBaselineResult
from src.utils.config import settings
from src.utils.helpers import format_datetime


class BehaviorBaseline:
    """用户行为基线计算器。

    Args:
        username: 需要计算行为基线的用户名。
        time_window_hours: 无法从日志时间推断跨度时使用的默认时间窗口。
        common_top_n: `common_*` 字段保留的高频值数量。
    """
    
    def __init__(
        self,
        username: str,
        time_window_hours: int = 24,
        common_top_n: int = 5,
    ) -> None:
        """初始化行为基线计算器。"""
        self.username = username
        self.time_window_hours = time_window_hours
        self.common_top_n = common_top_n
        
    def calculate_activity_hours(self, logs: List[Dict[str, Any]]) -> Dict[int, int]:
        """计算 0-23 小时活跃分布。

        Args:
            logs: 结构化日志列表。

        Returns:
            小时到日志数量的映射。
        """
        counter: Counter[int] = Counter()
        for log in self._iter_user_logs(logs):
            timestamp = self._get_timestamp(log)
            if timestamp is not None:
                counter[timestamp.hour] += 1

        return dict(sorted(counter.items()))
    
    def calculate_ip_frequency(self, logs: List[Dict[str, Any]]) -> Dict[str, int]:
        """计算 IP 出现次数。

        IP 字段按 `source_ip` -> `remote_addr` -> `ip` -> `src_ip` 的顺序读取。
        """
        counter: Counter[str] = Counter()
        for log in self._iter_user_logs(logs):
            ip_address = get_ip_address(log)
            if ip_address:
                counter[ip_address] += 1

        return self._ordered_counts(counter)
    
    def calculate_action_frequency(self, logs: List[Dict[str, Any]]) -> Dict[str, int]:
        """计算操作类型分布。

        `action` 会统一转为大写；缺失时回退到 `log_type`。
        """
        counter: Counter[str] = Counter()
        for log in self._iter_user_logs(logs):
            action = self._get_action(log)
            if action:
                counter[action] += 1

        return self._ordered_counts(counter)

    def calculate_location_frequency(self, logs: List[Dict[str, Any]]) -> Dict[str, int]:
        """计算地理位置出现次数。"""
        counter: Counter[str] = Counter()
        for log in self._iter_user_logs(logs):
            location = self._get_location(log)
            if location:
                counter[str(location)] += 1

        return self._ordered_counts(counter)

    def calculate_api_frequency(self, logs: List[Dict[str, Any]]) -> Dict[str, int]:
        """计算 API endpoint 调用次数。

        endpoint 字段按 `endpoint` -> `uri` 的顺序读取。
        """
        counter: Counter[str] = Counter()
        for log in self._iter_user_logs(logs):
            endpoint = self._get_first_value(log, ("endpoint", "uri"))
            if endpoint:
                counter[str(endpoint)] += 1

        return self._ordered_counts(counter)

    def calculate_failed_login_rate(self, logs: List[Dict[str, Any]]) -> Dict[str, float]:
        """计算失败登录数量和比例。

        Returns:
            包含 `failed_login_count`、`login_count` 和
            `failed_login_rate` 的字典。失败比例以登录事件数量为分母。
        """
        login_count = 0
        failed_login_count = 0

        for log in self._iter_user_logs(logs):
            if not self._is_login_event(log):
                continue

            login_count += 1
            if self._is_failed_status(log):
                failed_login_count += 1

        failed_login_rate = failed_login_count / login_count if login_count else 0.0
        return {
            "failed_login_count": failed_login_count,
            "login_count": login_count,
            "failed_login_rate": round(failed_login_rate, 6),
        }

    def build_baseline(self, logs: List[Dict[str, Any]]) -> BehaviorBaselineResult:
        """一次性构建完整用户行为基线。

        Args:
            logs: 结构化日志列表，可包含多个用户的数据。

        Returns:
            可序列化的用户行为基线字典。
        """
        user_logs = list(self._iter_user_logs(logs))
        sample_count = len(user_logs)
        activity_hours = self.calculate_activity_hours(user_logs)
        ip_frequency = self.calculate_ip_frequency(user_logs)
        location_frequency = self.calculate_location_frequency(user_logs)
        action_frequency = self.calculate_action_frequency(user_logs)
        api_frequency = self.calculate_api_frequency(user_logs)
        failed_login_stats = self.calculate_failed_login_rate(user_logs)

        timestamps = [
            timestamp
            for timestamp in (self._get_timestamp(log) for log in user_logs)
            if timestamp is not None
        ]
        api_call_avg_per_hour = self._calculate_average_per_hour(
            sum(api_frequency.values()),
            timestamps,
        )

        return {
            "username": self.username,
            "sample_count": sample_count,
            "is_reliable": sample_count >= settings.min_samples_for_profile,
            "activity_hours": activity_hours,
            "common_hours": self._top_keys(activity_hours),
            "ip_frequency": ip_frequency,
            "common_ips": self._top_keys(ip_frequency),
            "location_frequency": location_frequency,
            "common_locations": self._top_keys(location_frequency),
            "action_frequency": action_frequency,
            "api_frequency": api_frequency,
            "api_call_avg_per_hour": api_call_avg_per_hour,
            "failed_login_count": failed_login_stats["failed_login_count"],
            "failed_login_rate": failed_login_stats["failed_login_rate"],
            "calculated_at": format_datetime(datetime.now()),
        }

    def _iter_user_logs(self, logs: List[Dict[str, Any]]) -> Iterable[Dict[str, Any]]:
        """迭代当前用户日志。"""
        for log in logs:
            if not isinstance(log, dict):
                continue

            username = get_username(log)
            if username == self.username:
                yield log

    def _get_timestamp(self, log: Dict[str, Any]) -> Optional[datetime]:
        """从日志中读取并解析时间戳。"""
        value = log.get("timestamp")
        return parse_timestamp_value(value)

    def _get_action(self, log: Dict[str, Any]) -> Optional[str]:
        """从日志中读取标准化操作名。"""
        action = self._get_first_value(log, ("action", "event_type", "log_type"))
        if action is None:
            return None
        return get_action(log)

    def _is_login_event(self, log: Dict[str, Any]) -> bool:
        """判断日志是否表示登录行为。"""
        return is_login_event(log)

    def _is_failed_status(self, log_or_status: Any) -> bool:
        """判断状态字段是否表示失败。"""
        return is_failed_status(log_or_status)

    def _calculate_average_per_hour(
        self,
        count: int,
        timestamps: List[datetime],
    ) -> float:
        """按日志跨度计算每小时平均次数。"""
        if count <= 0:
            return 0.0

        span_hours = self._calculate_span_hours(timestamps)
        return round(count / span_hours, 6)

    def _calculate_span_hours(self, timestamps: List[datetime]) -> float:
        """计算日志时间跨度，最小按 1 小时计。"""
        if len(timestamps) < 2:
            return float(max(1, self.time_window_hours))

        start_time, end_time = min(timestamps), max(timestamps)
        span_seconds = (end_time - start_time).total_seconds()
        return max(span_seconds / 3600, 1.0)

    def _top_keys(self, counts: Dict[Any, int]) -> List[Any]:
        """按频率和键值稳定返回高频字段。"""
        return [
            key
            for key, _ in sorted(
                counts.items(),
                key=lambda item: (-item[1], self._sort_key_value(item[0])),
            )[: self.common_top_n]
        ]

    def _ordered_counts(self, counter: Counter) -> Dict[Any, int]:
        """将 Counter 转换为按数量倒序的普通字典。"""
        return {
            key: count
            for key, count in sorted(
                counter.items(),
                key=lambda item: (-item[1], str(item[0])),
            )
        }

    def _sort_key_value(self, value: Any) -> tuple:
        """为不同类型的 key 提供稳定排序值。"""
        if isinstance(value, int):
            return (0, value)
        return (1, str(value))

    def _get_first_value(
        self,
        log: Dict[str, Any],
        field_names: Tuple[str, ...],
    ) -> Optional[Any]:
        """按字段优先级读取第一个非空值。"""
        return get_first_value(log, field_names)

    def _get_location(self, log: Dict[str, Any]) -> Optional[str]:
        """读取位置字段，兼容 VPN 输出。"""
        return get_location(log)
