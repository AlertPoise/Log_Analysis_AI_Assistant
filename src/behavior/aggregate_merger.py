"""UEBA 聚合结果合并模块。

本模块接收 Repository 返回的多组数据库聚合行，并按 username 合并成
UserAggregateFeature。它不访问数据库、不写 SQL、不生成 UserBaseline，
也不保存完整原始日志列表。
"""

from typing import Any, Callable

from .schemas import UserAggregateFeature


class AggregateMerger:
    """将多组 Repository 聚合结果合并为用户级聚合特征。"""

    def merge(
        self,
        user_summary_rows: list[dict],
        hour_rows: list[dict],
        source_ip_rows: list[dict],
        destination_ip_rows: list[dict],
        source_country_rows: list[dict],
        source_city_rows: list[dict],
        vpn_gateway_rows: list[dict],
        action_rows: list[dict],
        event_type_rows: list[dict],
        result_rows: list[dict],
        fail_reason_rows: list[dict],
        auth_method_rows: list[dict],
        client_software_rows: list[dict],
        protocol_rows: list[dict],
        daily_rows: list[dict],
        session_metric_rows: list[dict],
    ) -> dict[str, UserAggregateFeature]:
        """按 username 合并多组聚合结果。"""
        features = self._build_feature_skeletons(user_summary_rows)

        self._merge_count_dimension(features, hour_rows, "active_hour", "hour_counts", int)
        self._merge_count_dimension(features, source_ip_rows, "source_ip", "source_ip_counts")
        self._merge_count_dimension(features, destination_ip_rows, "destination_ip", "destination_ip_counts")
        self._merge_count_dimension(features, source_country_rows, "source_country", "source_country_counts")
        self._merge_count_dimension(features, source_city_rows, "source_city", "source_city_counts")
        self._merge_count_dimension(features, vpn_gateway_rows, "vpn_gateway", "vpn_gateway_counts")
        self._merge_count_dimension(features, action_rows, "action", "action_counts")
        self._merge_count_dimension(features, event_type_rows, "event_type", "event_type_counts")
        self._merge_count_dimension(features, result_rows, "result", "result_counts")
        self._merge_count_dimension(features, fail_reason_rows, "fail_reason", "fail_reason_counts")
        self._merge_count_dimension(features, auth_method_rows, "auth_method", "auth_method_counts")
        self._merge_count_dimension(features, client_software_rows, "client_software", "client_software_counts")
        self._merge_count_dimension(features, protocol_rows, "protocol", "protocol_counts")
        self._merge_daily_counts(features, daily_rows)
        self._merge_session_metrics(features, session_metric_rows)

        return features

    def _build_feature_skeletons(self, rows: list[dict]) -> dict[str, UserAggregateFeature]:
        """用用户总览行创建 UserAggregateFeature 骨架。"""
        features: dict[str, UserAggregateFeature] = {}

        for row in rows or []:
            username = self._get_username(row)
            if username is None or "sample_count" not in row:
                continue

            features[username] = UserAggregateFeature(
                username=username,
                sample_count=self._to_int(row.get("sample_count")),
                failed_count=self._to_int(row.get("failed_count")),
                off_hours_count=self._to_int(row.get("off_hours_count")),
                unusual_ip_count=self._to_int(row.get("unusual_ip_count")),
                active_days=self._to_int(row.get("active_days")),
                first_seen=row.get("first_seen"),
                last_seen=row.get("last_seen"),
            )

        return features

    def _merge_count_dimension(
        self,
        features: dict[str, UserAggregateFeature],
        rows: list[dict],
        value_field: str,
        target_attr: str,
        key_converter: Callable[[Any], Any] = str,
    ) -> None:
        """合并形如 username + value + cnt 的计数维度。"""
        for row in rows or []:
            username = self._get_username(row)
            if username is None or username not in features:
                continue
            if value_field not in row or "cnt" not in row:
                continue

            count = self._to_count(row.get("cnt"))
            if count is None:
                continue

            key = self._convert_key(row.get(value_field), key_converter)
            if key is None:
                continue

            getattr(features[username], target_attr)[key] = count

    def _merge_daily_counts(self, features: dict[str, UserAggregateFeature], rows: list[dict]) -> None:
        """合并每日事件数，日期统一转成字符串 key。"""
        self._merge_count_dimension(features, rows, "event_date", "daily_counts", str)

    def _merge_session_metrics(self, features: dict[str, UserAggregateFeature], rows: list[dict]) -> None:
        """合并会话时长和流量摘要。"""
        session_fields = (
            "session_duration_avg",
            "session_duration_max",
            "session_duration_p50",
            "session_duration_p95",
        )
        traffic_fields = (
            "bytes_sent_avg",
            "bytes_sent_max",
            "bytes_recv_avg",
            "bytes_recv_max",
        )

        for row in rows or []:
            username = self._get_username(row)
            if username is None or username not in features:
                continue

            feature = features[username]
            for field_name in session_fields:
                value = self._to_float_or_none(row.get(field_name))
                if value is not None:
                    feature.session_metric_summary[field_name] = value

            for field_name in traffic_fields:
                value = self._to_float_or_none(row.get(field_name))
                if value is not None:
                    feature.traffic_metric_summary[field_name] = value

    def _get_username(self, row: dict) -> str | None:
        """提取并清洗 username，空值直接跳过。"""
        if not isinstance(row, dict):
            return None
        value = row.get("username")
        if value is None:
            return None
        username = str(value).strip()
        return username or None

    def _to_int(self, value: Any, default: int = 0) -> int:
        """尽量转换为 int，失败时返回默认值。"""
        try:
            if value is None:
                return default
            return int(value)
        except (TypeError, ValueError):
            return default

    def _to_count(self, value: Any) -> int | None:
        """转换 cnt；缺失、非法或负数表示该行不可合并。"""
        try:
            if value is None:
                return None
            count = int(value)
        except (TypeError, ValueError):
            return None
        if count < 0:
            return None
        return count

    def _to_float(self, value: Any, default: float = 0.0) -> float:
        """尽量转换为 float，失败时返回默认值。"""
        converted = self._to_float_or_none(value)
        return default if converted is None else converted

    def _to_float_or_none(self, value: Any) -> float | None:
        """转换可选浮点数，非法值返回 None。"""
        try:
            if value is None:
                return None
            return float(value)
        except (TypeError, ValueError):
            return None

    def _convert_key(self, value: Any, converter: Callable[[Any], Any]) -> Any | None:
        """转换计数字典 key，空字符串或转换失败时跳过。"""
        if value is None:
            return None
        try:
            key = converter(value)
        except (TypeError, ValueError):
            return None
        if isinstance(key, str):
            key = key.strip()
            return key or None
        return key


__all__ = ["AggregateMerger"]
