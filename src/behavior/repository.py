"""行为模块与存储层的协议定义。"""

from datetime import datetime, timedelta
from typing import Dict, List, Optional, Protocol

from src.behavior.normalizer import normalize_behavior_log, parse_timestamp_value

from src.behavior.schemas import (
    AnomalyResult,
    BehaviorBaselineResult,
    NormalizedBehaviorLog,
    UserProfileResult,
)

try:
    from src.utils.logger import get_logger
except Exception:  # pragma: no cover - 兼容最小测试环境
    import logging

    def get_logger(name: str):
        return logging.getLogger(name)


logger = get_logger(__name__)


class BehaviorLogRepository(Protocol):
    """定义 behavior 对历史/实时日志的读取需求。

    真实实现应由 storage 模块或其适配层提供。
    """

    def fetch_user_history(
        self,
        username: str,
        start_time: Optional[datetime] = None,
        end_time: Optional[datetime] = None,
        limit: Optional[int] = None,
    ) -> List[NormalizedBehaviorLog]:
        """读取用户历史日志。"""

    def fetch_recent_user_events(
        self,
        username: str,
        window_minutes: int = 60,
        limit: Optional[int] = None,
    ) -> List[NormalizedBehaviorLog]:
        """读取用户近期窗口日志。"""


class BehaviorResultRepository(Protocol):
    """定义 behavior 对分析结果的写入需求。

    真实实现应由 storage 模块或其适配层提供。
    """

    def save_baseline(self, result: BehaviorBaselineResult) -> None:
        """保存行为基线。"""

    def save_profile(self, result: UserProfileResult) -> None:
        """保存用户画像。"""

    def save_anomalies(self, results: List[AnomalyResult]) -> None:
        """保存异常检测结果。"""


class InMemoryBehaviorRepository:
    """轻量内存仓储，便于本地演示和测试。"""

    def __init__(self, logs: Optional[List[NormalizedBehaviorLog]] = None) -> None:
        """初始化内存仓储。"""
        self._logs: List[NormalizedBehaviorLog] = []
        self.baselines: Dict[str, BehaviorBaselineResult] = {}
        self.profiles: Dict[str, UserProfileResult] = {}
        self.anomalies: List[AnomalyResult] = []
        if logs:
            self.add_logs(logs)

    def add_logs(self, logs: List[NormalizedBehaviorLog]) -> None:
        """批量写入内存日志。"""
        for log in logs:
            normalized = normalize_behavior_log(log)
            if normalized is not None:
                self._logs.append(normalized)
            else:
                logger.debug("内存仓储跳过无法标准化的日志")

    def fetch_user_history(
        self,
        username: str,
        start_time: Optional[datetime] = None,
        end_time: Optional[datetime] = None,
        limit: Optional[int] = None,
    ) -> List[NormalizedBehaviorLog]:
        """读取用户历史日志。"""
        results: List[NormalizedBehaviorLog] = []
        for log in self._logs:
            if log.get("username") != username:
                continue

            timestamp = parse_timestamp_value(log.get("timestamp"))
            if timestamp is None:
                continue
            if start_time is not None and timestamp < start_time:
                continue
            if end_time is not None and timestamp > end_time:
                continue
            results.append(dict(log))

        results.sort(key=lambda item: parse_timestamp_value(item.get("timestamp")) or datetime.max)
        if limit is not None:
            return results[:limit]
        return results

    def fetch_recent_user_events(
        self,
        username: str,
        window_minutes: int = 60,
        limit: Optional[int] = None,
    ) -> List[NormalizedBehaviorLog]:
        """读取用户近期窗口日志。"""
        history = self.fetch_user_history(username)
        if not history:
            return []

        latest_timestamp = max(
            parse_timestamp_value(log.get("timestamp")) or datetime.min
            for log in history
        )
        start_time = latest_timestamp - timedelta(minutes=window_minutes)
        recent = [
            log
            for log in history
            if (parse_timestamp_value(log.get("timestamp")) or datetime.min) >= start_time
        ]
        if limit is not None:
            return recent[-limit:]
        return recent

    def save_baseline(self, result: BehaviorBaselineResult) -> None:
        """保存行为基线。"""
        self.baselines[result["username"]] = dict(result)

    def save_profile(self, result: UserProfileResult) -> None:
        """保存用户画像。"""
        self.profiles[result["username"]] = dict(result)

    def save_anomalies(self, results: List[AnomalyResult]) -> None:
        """保存异常检测结果。"""
        self.anomalies.extend(dict(result) for result in results)
