"""行为模块统一编排入口。"""

from typing import Any, Dict, List, Optional

from src.behavior.anomaly import AnomalyDetector
from src.behavior.baseline import BehaviorBaseline
from src.behavior.normalizer import build_sort_key, get_username
from src.behavior.schemas import (
    AnomalyResult,
    BehaviorAnalysisResult,
    BehaviorBaselineResult,
    UserProfileResult,
)
from src.behavior.user_profile import UserProfile

try:
    from src.utils.logger import get_logger
except Exception:  # pragma: no cover - 兼容最小测试环境
    import logging

    def get_logger(name: str):
        return logging.getLogger(name)


logger = get_logger(__name__)


class BehaviorAnalysisService:
    """统一串联基线、画像和异常检测。"""

    def __init__(
        self,
        detector: Optional[AnomalyDetector] = None,
    ) -> None:
        """初始化行为分析服务。"""
        self.detector = detector or AnomalyDetector()

    def build_baseline(
        self,
        username: str,
        logs: List[Dict[str, Any]],
    ) -> BehaviorBaselineResult:
        """构建单个用户的行为基线。"""
        return BehaviorBaseline(username).build_baseline(logs)

    def build_profile(
        self,
        username: str,
        logs: List[Dict[str, Any]],
    ) -> UserProfileResult:
        """构建单个用户的行为画像。"""
        return UserProfile(username).build_from_logs(logs).get_profile()

    def detect_anomalies(
        self,
        username: str,
        logs: List[Dict[str, Any]],
        baseline: Optional[BehaviorBaselineResult] = None,
    ) -> List[AnomalyResult]:
        """检测单个用户的异常行为。"""
        user_logs = self._filter_user_logs(username, logs)
        active_baseline = baseline or self.build_baseline(username, user_logs)
        return self.detector.detect_batch(user_logs, active_baseline)

    def analyze_user(
        self,
        username: str,
        logs: List[Dict[str, Any]],
        detection_logs: Optional[List[Dict[str, Any]]] = None,
    ) -> BehaviorAnalysisResult:
        """输出单个用户的完整行为分析结果。"""
        user_logs = self._filter_user_logs(username, logs)
        baseline = self.build_baseline(username, user_logs)
        profile = self.build_profile(username, user_logs)
        active_detection_logs = (
            self._filter_user_logs(username, detection_logs)
            if detection_logs is not None
            else user_logs
        )
        anomalies = self.detector.detect_batch(active_detection_logs, baseline)

        return {
            "username": username,
            "baseline": baseline,
            "profile": profile,
            "anomalies": anomalies,
            "summary": {
                "anomaly_count": len(anomalies),
                "alert_count": sum(1 for anomaly in anomalies if anomaly.get("is_alert")),
                "highest_risk_level": self._highest_risk_level(anomalies),
            },
        }

    def _filter_user_logs(
        self,
        username: str,
        logs: Optional[List[Dict[str, Any]]],
    ) -> List[Dict[str, Any]]:
        """筛选并稳定排序指定用户日志。"""
        if not logs:
            logger.debug("行为分析服务收到空日志列表")
            return []

        return sorted(
            [
                log
                for log in logs
                if isinstance(log, dict) and get_username(log) == username
            ],
            key=build_sort_key,
        )

    def _highest_risk_level(self, anomalies: List[AnomalyResult]) -> str:
        """返回当前异常列表中的最高风险等级。"""
        order = {
            "INFO": 0,
            "LOW": 1,
            "MEDIUM": 2,
            "HIGH": 3,
            "CRITICAL": 4,
        }
        highest_level = "INFO"
        highest_score = -1

        for anomaly in anomalies:
            level = str(anomaly.get("risk_level", "INFO")).upper()
            score = order.get(level, 0)
            if score > highest_score:
                highest_level = level
                highest_score = score

        return highest_level
