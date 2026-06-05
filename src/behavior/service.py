"""UEBA Service 编排模块。

本模块负责串联 Repository、AggregateMerger、BaselineBuilder 和 BaselineStore，
对外提供一次性离线 Baseline 构建入口。Service 只做流程编排和结果统计。
"""

from datetime import datetime
import time

from .aggregate_merger import AggregateMerger
from .baseline_builder import BaselineBuilder
from .baseline_store import BaselineStore
from .config import UebaBaselineConfig
from .repository import UebaRepository
from .schemas import BaselineBuildResult


class UebaService:
    """UEBA 第一版离线 Baseline 构建统一入口。"""

    def __init__(
        self,
        repository: UebaRepository,
        aggregate_merger: AggregateMerger,
        baseline_builder: BaselineBuilder,
        baseline_store: BaselineStore,
        config: UebaBaselineConfig | None = None,
    ) -> None:
        """初始化 UebaService。

        Args:
            repository: 数据库侧聚合读取层。
            aggregate_merger: 聚合结果合并器。
            baseline_builder: Baseline 构建器。
            baseline_store: Baseline 存储层。
            config: UEBA Baseline 构建配置。
        """
        self.repository = repository
        self.aggregate_merger = aggregate_merger
        self.baseline_builder = baseline_builder
        self.baseline_store = baseline_store
        self.config = config or UebaBaselineConfig()

    def build_baseline_once(
        self,
        start_time: datetime,
        end_time: datetime,
        log_type: str = "vpn",
    ) -> BaselineBuildResult:
        """执行一次离线用户行为 Baseline 构建。

        Args:
            start_time: 基线窗口开始时间。
            end_time: 基线窗口结束时间。
            log_type: Repository 查询使用的日志类型，默认 vpn。

        Returns:
            BaselineBuildResult: 本次构建的结构化结果。
        """
        begin = time.time()

        if start_time >= end_time:
            return self._failed_result(
                start_time,
                end_time,
                begin,
                "时间窗口非法: start_time 必须早于 end_time",
            )

        try:
            self.baseline_store.ensure_table()

            user_summary_rows = self.repository.fetch_user_summary(
                start_time,
                end_time,
                log_type=log_type,
            )
            hour_rows = self.repository.fetch_hour_distribution(
                start_time,
                end_time,
                log_type=log_type,
            )
            source_ip_rows = self.repository.fetch_top_source_ips(
                start_time,
                end_time,
                self.config.top_source_ip_limit,
                log_type=log_type,
            )
            destination_ip_rows = self.repository.fetch_top_destination_ips(
                start_time,
                end_time,
                self.config.top_destination_ip_limit,
                log_type=log_type,
            )
            source_country_rows = self.repository.fetch_top_source_countries(
                start_time,
                end_time,
                self.config.top_source_country_limit,
                log_type=log_type,
            )
            source_city_rows = self.repository.fetch_top_source_cities(
                start_time,
                end_time,
                self.config.top_source_city_limit,
                log_type=log_type,
            )
            vpn_gateway_rows = self.repository.fetch_top_vpn_gateways(
                start_time,
                end_time,
                self.config.top_vpn_gateway_limit,
                log_type=log_type,
            )
            action_rows = self.repository.fetch_action_distribution(
                start_time,
                end_time,
                log_type=log_type,
            )
            event_type_rows = self.repository.fetch_event_type_distribution(
                start_time,
                end_time,
                log_type=log_type,
            )
            result_rows = self.repository.fetch_result_distribution(
                start_time,
                end_time,
                log_type=log_type,
            )
            fail_reason_rows = self.repository.fetch_fail_reason_distribution(
                start_time,
                end_time,
                self.config.top_fail_reason_limit,
                log_type=log_type,
            )
            auth_method_rows = self.repository.fetch_auth_method_distribution(
                start_time,
                end_time,
                log_type=log_type,
            )
            client_software_rows = self.repository.fetch_client_software_distribution(
                start_time,
                end_time,
                self.config.top_client_software_limit,
                log_type=log_type,
            )
            protocol_rows = self.repository.fetch_protocol_distribution(
                start_time,
                end_time,
                log_type=log_type,
            )
            daily_rows = self.repository.fetch_daily_event_counts(
                start_time,
                end_time,
                log_type=log_type,
            )
            session_metric_rows = self.repository.fetch_session_metric_summary(
                start_time,
                end_time,
                log_type=log_type,
            )

            features_by_user = self.aggregate_merger.merge(
                user_summary_rows=user_summary_rows,
                hour_rows=hour_rows,
                source_ip_rows=source_ip_rows,
                destination_ip_rows=destination_ip_rows,
                source_country_rows=source_country_rows,
                source_city_rows=source_city_rows,
                vpn_gateway_rows=vpn_gateway_rows,
                action_rows=action_rows,
                event_type_rows=event_type_rows,
                result_rows=result_rows,
                fail_reason_rows=fail_reason_rows,
                auth_method_rows=auth_method_rows,
                client_software_rows=client_software_rows,
                protocol_rows=protocol_rows,
                daily_rows=daily_rows,
                session_metric_rows=session_metric_rows,
            )

            baselines = self.baseline_builder.build_baselines(
                features_by_user,
                start_time,
                end_time,
            )
            saved_count = self.baseline_store.save_baselines(baselines)

            total_user_count = len(baselines)
            reliable_user_count = sum(1 for baseline in baselines if baseline.is_reliable)
            total_log_count = sum(baseline.sample_count for baseline in baselines)

            return BaselineBuildResult(
                success=True,
                baseline_start_time=start_time,
                baseline_end_time=end_time,
                total_user_count=total_user_count,
                reliable_user_count=reliable_user_count,
                unreliable_user_count=total_user_count - reliable_user_count,
                total_log_count=total_log_count,
                model_version=self.config.model_version,
                duration_seconds=round(time.time() - begin, 3),
                message=f"saved {saved_count} user baselines",
            )
        except Exception as exc:
            return self._failed_result(
                start_time,
                end_time,
                begin,
                f"构建失败: {type(exc).__name__}: {exc}",
            )

    def _failed_result(
        self,
        start_time: datetime,
        end_time: datetime,
        begin: float,
        message: str,
    ) -> BaselineBuildResult:
        """生成失败场景的 BaselineBuildResult。"""
        return BaselineBuildResult(
            success=False,
            baseline_start_time=start_time,
            baseline_end_time=end_time,
            total_user_count=0,
            reliable_user_count=0,
            unreliable_user_count=0,
            total_log_count=0,
            model_version=self.config.model_version,
            duration_seconds=round(time.time() - begin, 3),
            message=message,
        )


__all__ = ["UebaService"]
