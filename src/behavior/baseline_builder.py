"""UEBA Baseline Builder 模块。

本模块只负责把 UserAggregateFeature 转换为 UserBaseline。它不访问数据库、
不写 SQL、不处理原始日志，也不实现实时检测或异常评分闭环。
"""

from datetime import datetime
from typing import Any

from .config import UebaBaselineConfig
from .schemas import CountRatioItem, UserAggregateFeature, UserBaseline


class BaselineBuilder:
    """根据用户聚合特征生成 UEBA 第一版 Baseline。"""

    def __init__(self, config: UebaBaselineConfig | None = None) -> None:
        """初始化 BaselineBuilder。"""
        self.config = config or UebaBaselineConfig()

    def build_baselines(
        self,
        features_by_user: dict[str, UserAggregateFeature],
        start_time: datetime,
        end_time: datetime,
    ) -> list[UserBaseline]:
        """将用户聚合特征批量转换为 UserBaseline。"""
        return [
            self._build_one_baseline(feature, start_time, end_time)
            for feature in features_by_user.values()
        ]

    def build_count_ratio_items(
        self,
        counts: dict[str | int, int],
        total: int,
        limit: int,
        min_ratio: float = 0.0,
    ) -> list[CountRatioItem]:
        """按 count 降序生成受限数量的 Top-N ratio 项。"""
        if total <= 0 or limit <= 0:
            return []

        items: list[CountRatioItem] = []
        for value, count in sorted(counts.items(), key=lambda item: item[1], reverse=True):
            if count <= 0:
                continue

            ratio = self._safe_ratio(count, total)
            if ratio < min_ratio:
                continue

            items.append(
                CountRatioItem(
                    value=value,
                    count=count,
                    ratio=round(ratio, 6),
                )
            )
            if len(items) >= limit:
                break

        return items

    def build_distribution(self, counts: dict[str, int], total: int) -> dict[str, float]:
        """将计数字典转换为占比分布。"""
        if total <= 0:
            return {}
        return {
            key: round(self._safe_ratio(value, total), 6)
            for key, value in counts.items()
            if value > 0
        }

    def _build_one_baseline(
        self,
        feature: UserAggregateFeature,
        start_time: datetime,
        end_time: datetime,
    ) -> UserBaseline:
        """构建单个用户的 UserBaseline。"""
        sample_count = feature.sample_count
        window_days = max((end_time - start_time).days, 1)
        active_days = max(feature.active_days, 1)
        vpn_gateway_min_ratio = getattr(self.config, "common_vpn_gateway_min_ratio", 0.0)

        return UserBaseline(
            username=feature.username,
            sample_count=sample_count,
            is_reliable=sample_count >= self.config.min_sample_count,
            common_active_hours=self.build_count_ratio_items(
                feature.hour_counts,
                sample_count,
                limit=24,
                min_ratio=self.config.common_hour_min_ratio,
            ),
            common_source_ips=self.build_count_ratio_items(
                feature.source_ip_counts,
                sample_count,
                limit=self.config.top_source_ip_limit,
                min_ratio=self.config.common_source_ip_min_ratio,
            ),
            common_destination_ips=self.build_count_ratio_items(
                feature.destination_ip_counts,
                sample_count,
                limit=self.config.top_destination_ip_limit,
            ),
            common_source_countries=self.build_count_ratio_items(
                feature.source_country_counts,
                sample_count,
                limit=self.config.top_source_country_limit,
            ),
            common_source_cities=self.build_count_ratio_items(
                feature.source_city_counts,
                sample_count,
                limit=self.config.top_source_city_limit,
                min_ratio=self.config.common_source_city_min_ratio,
            ),
            common_vpn_gateways=self.build_count_ratio_items(
                feature.vpn_gateway_counts,
                sample_count,
                limit=self.config.top_vpn_gateway_limit,
                min_ratio=vpn_gateway_min_ratio,
            ),
            action_distribution=self.build_distribution(feature.action_counts, sample_count),
            event_type_distribution=self.build_distribution(feature.event_type_counts, sample_count),
            result_distribution=self.build_distribution(feature.result_counts, sample_count),
            fail_reason_distribution=self.build_distribution(feature.fail_reason_counts, sample_count),
            auth_method_distribution=self.build_distribution(feature.auth_method_counts, sample_count),
            client_software_distribution=self.build_distribution(feature.client_software_counts, sample_count),
            protocol_distribution=self.build_distribution(feature.protocol_counts, sample_count),
            failed_rate=round(self._safe_ratio(feature.failed_count, sample_count), 6),
            off_hours_rate=round(self._safe_ratio(feature.off_hours_count, sample_count), 6),
            unusual_ip_rate=round(self._safe_ratio(feature.unusual_ip_count, sample_count), 6),
            avg_daily_events=round(self._safe_ratio(sample_count, window_days), 6),
            session_duration_avg=self._get_metric(feature.session_metric_summary, "session_duration_avg"),
            session_duration_p50=self._get_metric(feature.session_metric_summary, "session_duration_p50"),
            session_duration_p95=self._get_metric(feature.session_metric_summary, "session_duration_p95"),
            bytes_sent_avg=self._get_metric(feature.traffic_metric_summary, "bytes_sent_avg"),
            bytes_recv_avg=self._get_metric(feature.traffic_metric_summary, "bytes_recv_avg"),
            active_day_avg_events=round(self._safe_ratio(sample_count, active_days), 6),
            max_daily_events=max(feature.daily_counts.values(), default=0),
            baseline_start_time=start_time,
            baseline_end_time=end_time,
            model_version=self.config.model_version,
        )

    def _safe_ratio(self, numerator: int | float, denominator: int | float) -> float:
        """安全计算 ratio，避免除零。"""
        if denominator <= 0:
            return 0.0
        return numerator / denominator

    def _get_metric(self, summary: dict[str, float], key: str, default: float = 0.0) -> float:
        """从聚合摘要中读取浮点指标。"""
        value: Any = summary.get(key, default)
        try:
            if value is None:
                return default
            return float(value)
        except (TypeError, ValueError):
            return default


__all__ = ["BaselineBuilder"]
