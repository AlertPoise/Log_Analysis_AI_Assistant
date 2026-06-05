"""UEBA 离线 Baseline 构建的默认 Config 对象。

本模块是第一版一次性离线用户行为 Baseline 构建的默认配置来源。
它不是长期最终配置中心；后续阶段可以通过 CLI 参数、API 参数、
环境变量或数据库配置覆盖这些默认值。
"""

from dataclasses import dataclass


@dataclass
class UebaBaselineConfig:
    """第一版 UEBA Baseline 构建配置。"""

    baseline_window_days: int = 30
    min_sample_count: int = 20

    top_source_ip_limit: int = 10
    top_destination_ip_limit: int = 10
    top_source_country_limit: int = 10
    top_source_city_limit: int = 10
    top_vpn_gateway_limit: int = 10
    top_fail_reason_limit: int = 10
    top_client_software_limit: int = 10
    top_action_limit: int = 20
    top_result_limit: int = 10

    common_hour_min_ratio: float = 0.05
    common_source_ip_min_ratio: float = 0.03
    common_source_city_min_ratio: float = 0.03
    common_vpn_gateway_min_ratio: float = 0.03
    model_version: str = "ueba_baseline_v1"

    write_batch_size: int = 1000


__all__ = ["UebaBaselineConfig"]

