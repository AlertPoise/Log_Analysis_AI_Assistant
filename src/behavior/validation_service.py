"""UEBA 准线验证 dry-run 编排服务。

本模块只串联目标日志读取、baseline 读取和纯 Python 评分，返回受控摘要。
它不写结果表，不更新源日志，也不创建数据库连接。
"""

from typing import Any

from .baseline_store import BaselineStore
from .schemas import CountRatioItem, UserBaseline
from .score_calculator import UebaScoreCalculator
from .validation_repository import UebaValidationRepository
from .validation_schemas import UebaValidationResult


class UebaValidationService:
    """UEBA 准线验证 dry-run 统一编排入口。"""

    DEFAULT_SAMPLE_RESULT_LIMIT = 10

    def __init__(
        self,
        validation_repository: UebaValidationRepository,
        baseline_store: BaselineStore,
        score_calculator: UebaScoreCalculator | None = None,
    ) -> None:
        """初始化 validation dry-run service。

        Args:
            validation_repository: 待读取目标日志的 repository。
            baseline_store: 只读 baseline 查询组件。
            score_calculator: 纯 Python 评分器。
        """
        self.validation_repository = validation_repository
        self.baseline_store = baseline_store
        self.score_calculator = score_calculator or UebaScoreCalculator()

    def dry_run(
        self,
        start_time: str,
        end_time: str,
        log_type: str = "vpn",
        limit: int = 1000,
        baseline_version: str | None = None,
        sample_result_limit: int = DEFAULT_SAMPLE_RESULT_LIMIT,
        validated_at: str | None = None,
    ) -> dict[str, Any]:
        """执行一次不落库的 UEBA 准线验证 dry-run。"""
        sample_limit = self._validate_sample_result_limit(sample_result_limit)
        target_logs = self.validation_repository.fetch_target_logs(
            start_time=start_time,
            end_time=end_time,
            log_type=log_type,
            limit=limit,
        )

        results: list[UebaValidationResult] = []
        no_baseline_count = 0
        unreliable_baseline_count = 0

        for target_log in target_logs:
            baseline = self._load_user_baseline(target_log.username, baseline_version)
            if baseline is None:
                no_baseline_count += 1
            elif not baseline.is_reliable:
                unreliable_baseline_count += 1

            result = self.score_calculator.calculate(
                target_log,
                baseline,
                model_version=baseline_version or (baseline.model_version if baseline else None),
                validated_at=validated_at,
            )
            results.append(result)

        return {
            "success": True,
            "dry_run": True,
            "start_time": start_time,
            "end_time": end_time,
            "log_type": log_type,
            "limit": limit,
            "baseline_version": baseline_version,
            "processed_count": len(target_logs),
            "selected_count": len(target_logs),
            "scored_count": len(results),
            "written_count": 0,
            "skipped_count": no_baseline_count,
            "no_baseline_count": no_baseline_count,
            "unreliable_baseline_count": unreliable_baseline_count,
            "failed_count": 0,
            "risk_level_counts": self._count_by(results, "ueba_risk_level"),
            "validation_status_counts": self._count_by(results, "validation_status"),
            "sample_results": [
                self._sample_result(result)
                for result in results[:sample_limit]
            ],
            "message": self._message(len(target_logs), len(results)),
        }

    def _load_user_baseline(
        self,
        username: str,
        baseline_version: str | None,
    ) -> UserBaseline | None:
        """读取并转换某个用户的最新 baseline。"""
        baseline_row = self.baseline_store.get_user_baseline(
            username,
            model_version=baseline_version,
        )
        return self._coerce_user_baseline(baseline_row)

    def _coerce_user_baseline(self, value: Any) -> UserBaseline | None:
        """兼容 BaselineStore 行字典、baseline_json 字典和测试对象。"""
        if value is None:
            return None
        if isinstance(value, UserBaseline):
            return value
        if not isinstance(value, dict):
            return None

        payload = value.get("baseline") if isinstance(value.get("baseline"), dict) else value
        if not isinstance(payload, dict):
            return None
        if not self._has_required_baseline_fields(payload):
            return None

        return UserBaseline(
            username=str(payload["username"]),
            sample_count=int(payload.get("sample_count") or 0),
            is_reliable=self._to_bool(payload.get("is_reliable")),
            common_active_hours=self._count_ratio_items(payload.get("common_active_hours")),
            common_source_ips=self._count_ratio_items(payload.get("common_source_ips")),
            common_destination_ips=self._count_ratio_items(payload.get("common_destination_ips")),
            common_source_countries=self._count_ratio_items(payload.get("common_source_countries")),
            common_source_cities=self._count_ratio_items(payload.get("common_source_cities")),
            common_vpn_gateways=self._count_ratio_items(payload.get("common_vpn_gateways")),
            action_distribution=self._to_distribution(payload.get("action_distribution")),
            event_type_distribution=self._to_distribution(payload.get("event_type_distribution")),
            result_distribution=self._to_distribution(payload.get("result_distribution")),
            fail_reason_distribution=self._to_distribution(payload.get("fail_reason_distribution")),
            auth_method_distribution=self._to_distribution(payload.get("auth_method_distribution")),
            client_software_distribution=self._to_distribution(payload.get("client_software_distribution")),
            protocol_distribution=self._to_distribution(payload.get("protocol_distribution")),
            failed_rate=self._to_float(payload.get("failed_rate")),
            off_hours_rate=self._to_float(payload.get("off_hours_rate")),
            unusual_ip_rate=self._to_float(payload.get("unusual_ip_rate")),
            avg_daily_events=self._to_float(payload.get("avg_daily_events")),
            session_duration_avg=self._to_float(payload.get("session_duration_avg")),
            session_duration_p50=self._to_float(payload.get("session_duration_p50")),
            session_duration_p95=self._to_float(payload.get("session_duration_p95")),
            bytes_sent_avg=self._to_float(payload.get("bytes_sent_avg")),
            bytes_recv_avg=self._to_float(payload.get("bytes_recv_avg")),
            active_day_avg_events=self._to_float(payload.get("active_day_avg_events")),
            max_daily_events=int(payload.get("max_daily_events") or 0),
            baseline_start_time=payload.get("baseline_start_time"),
            baseline_end_time=payload.get("baseline_end_time"),
            model_version=str(payload.get("model_version") or ""),
        )

    def _has_required_baseline_fields(self, payload: dict[str, Any]) -> bool:
        """确认 baseline 至少具备评分所需的核心字段。"""
        required = {
            "username",
            "sample_count",
            "is_reliable",
            "baseline_start_time",
            "baseline_end_time",
            "model_version",
        }
        return required.issubset(payload)

    def _count_ratio_items(self, value: Any) -> list[CountRatioItem]:
        """将 baseline JSON 中的 Top-N 项转换为 CountRatioItem。"""
        if not isinstance(value, list):
            return []

        items: list[CountRatioItem] = []
        for item in value:
            if isinstance(item, CountRatioItem):
                items.append(item)
                continue
            if not isinstance(item, dict):
                continue
            if "value" not in item:
                continue
            items.append(
                CountRatioItem(
                    value=item.get("value"),
                    count=int(item.get("count") or 0),
                    ratio=self._to_float(item.get("ratio")),
                )
            )
        return items

    def _to_distribution(self, value: Any) -> dict[str, float]:
        """规范化 baseline 分布字典。"""
        if not isinstance(value, dict):
            return {}
        return {str(key): self._to_float(raw) for key, raw in value.items()}

    def _to_bool(self, value: Any) -> bool:
        """兼容 ClickHouse UInt8、Bool 和 JSON 字符串。"""
        if isinstance(value, bool):
            return value
        if isinstance(value, int):
            return value != 0
        if isinstance(value, str):
            return value.strip().lower() in {"1", "true", "yes"}
        return bool(value)

    def _to_float(self, value: Any) -> float:
        """将缺失或空值安全转换为 float。"""
        if value in (None, ""):
            return 0.0
        return float(value)

    def _count_by(self, results: list[UebaValidationResult], field_name: str) -> dict[str, int]:
        """按结果字段统计分布。"""
        counts: dict[str, int] = {}
        for result in results:
            value = str(getattr(result, field_name))
            counts[value] = counts.get(value, 0) + 1
        return counts

    def _sample_result(self, result: UebaValidationResult) -> dict[str, Any]:
        """返回受控的样例结果，避免 dry-run 摘要过大。"""
        return {
            "validation_id": result.validation_id,
            "source_log_id": result.source_log_id,
            "timestamp": result.timestamp,
            "username": result.username,
            "log_type": result.log_type,
            "request_id": result.request_id,
            "baseline_model_version": result.baseline_model_version,
            "ueba_score": result.ueba_score,
            "ueba_risk_level": result.ueba_risk_level,
            "validation_status": result.validation_status,
            "reason_codes": [reason.code for reason in result.ueba_anomaly_reasons],
        }

    def _validate_sample_result_limit(self, limit: int) -> int:
        """限制 dry-run 样例结果数量。"""
        if not isinstance(limit, int) or limit < 0:
            raise ValueError("sample_result_limit must be a non-negative integer")
        return limit

    def _message(self, selected_count: int, scored_count: int) -> str:
        """生成简短 dry-run 说明。"""
        if selected_count == 0:
            return "dry-run selected 0 target logs"
        return f"dry-run scored {scored_count} of {selected_count} target logs"


__all__ = ["UebaValidationService"]
