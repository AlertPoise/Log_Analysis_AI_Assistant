"""UEBA 内部稳定 Schema 定义。

本模块定义第一版离线 Baseline 构建在各内部模块之间传递的数据结构。
Repository 负责屏蔽数据库字段变化，后续 AggregateMerger、Builder、Store、
Service 应依赖这里的稳定结构。此模块不访问数据库、不写 SQL、
不保存完整原始日志列表。
"""

from dataclasses import dataclass, field
from datetime import datetime


@dataclass
class CountRatioItem:
    """Top-N 统计项及其占比。"""

    value: str | int
    count: int
    ratio: float


@dataclass
class UserAggregateFeature:
    """Repository 与 AggregateMerger 产出的用户级聚合特征。"""

    username: str
    sample_count: int = 0
    failed_count: int = 0
    off_hours_count: int = 0
    unusual_ip_count: int = 0
    active_days: int = 0
    first_seen: datetime | None = None
    last_seen: datetime | None = None

    hour_counts: dict[int, int] = field(default_factory=dict)
    source_ip_counts: dict[str, int] = field(default_factory=dict)
    destination_ip_counts: dict[str, int] = field(default_factory=dict)
    source_country_counts: dict[str, int] = field(default_factory=dict)
    source_city_counts: dict[str, int] = field(default_factory=dict)
    vpn_gateway_counts: dict[str, int] = field(default_factory=dict)
    action_counts: dict[str, int] = field(default_factory=dict)
    event_type_counts: dict[str, int] = field(default_factory=dict)
    result_counts: dict[str, int] = field(default_factory=dict)
    fail_reason_counts: dict[str, int] = field(default_factory=dict)
    auth_method_counts: dict[str, int] = field(default_factory=dict)
    client_software_counts: dict[str, int] = field(default_factory=dict)
    protocol_counts: dict[str, int] = field(default_factory=dict)
    daily_counts: dict[str, int] = field(default_factory=dict)
    session_metric_summary: dict[str, float] = field(default_factory=dict)
    traffic_metric_summary: dict[str, float] = field(default_factory=dict)


@dataclass(kw_only=True)
class UserBaseline:
    """由用户聚合特征构建出的用户行为 Baseline。"""

    username: str
    sample_count: int
    is_reliable: bool

    common_active_hours: list[CountRatioItem] = field(default_factory=list)
    common_source_ips: list[CountRatioItem] = field(default_factory=list)
    common_destination_ips: list[CountRatioItem] = field(default_factory=list)
    common_source_countries: list[CountRatioItem] = field(default_factory=list)
    common_source_cities: list[CountRatioItem] = field(default_factory=list)
    common_vpn_gateways: list[CountRatioItem] = field(default_factory=list)

    action_distribution: dict[str, float] = field(default_factory=dict)
    event_type_distribution: dict[str, float] = field(default_factory=dict)
    result_distribution: dict[str, float] = field(default_factory=dict)
    fail_reason_distribution: dict[str, float] = field(default_factory=dict)
    auth_method_distribution: dict[str, float] = field(default_factory=dict)
    client_software_distribution: dict[str, float] = field(default_factory=dict)
    protocol_distribution: dict[str, float] = field(default_factory=dict)

    failed_rate: float = 0.0
    off_hours_rate: float = 0.0
    unusual_ip_rate: float = 0.0
    avg_daily_events: float = 0.0
    session_duration_avg: float = 0.0
    session_duration_p50: float = 0.0
    session_duration_p95: float = 0.0
    bytes_sent_avg: float = 0.0
    bytes_recv_avg: float = 0.0
    active_day_avg_events: float = 0.0
    max_daily_events: int = 0

    baseline_start_time: datetime
    baseline_end_time: datetime
    model_version: str


@dataclass
class BaselineBuildResult:
    """一次离线 Baseline 构建任务的汇总结果。"""

    success: bool
    baseline_start_time: datetime
    baseline_end_time: datetime
    total_user_count: int
    reliable_user_count: int
    unreliable_user_count: int
    total_log_count: int
    model_version: str
    duration_seconds: float
    message: str = ""


__all__ = [
    "CountRatioItem",
    "UserAggregateFeature",
    "UserBaseline",
    "BaselineBuildResult",
]

