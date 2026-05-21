"""UEBA Config 模块测试。"""

from src.behavior.config import UebaBaselineConfig


def test_default_config_values():
    """验证第一版 UEBA Config 默认值。"""
    config = UebaBaselineConfig()

    assert config.baseline_window_days == 30
    assert config.min_sample_count == 20
    assert config.top_source_ip_limit == 10
    assert config.top_destination_ip_limit == 10
    assert config.top_source_country_limit == 10
    assert config.top_source_city_limit == 10
    assert config.top_vpn_gateway_limit == 10
    assert config.top_fail_reason_limit == 10
    assert config.top_client_software_limit == 10
    assert config.top_action_limit == 20
    assert config.top_result_limit == 10
    assert config.common_hour_min_ratio == 0.05
    assert config.common_source_ip_min_ratio == 0.03
    assert config.common_source_city_min_ratio == 0.03
    assert config.common_vpn_gateway_min_ratio == 0.03
    assert config.model_version == "ueba_baseline_v1"
    assert config.write_batch_size == 1000


def test_config_can_override_fields():
    """验证 Config 可由 CLI/API 层覆盖。"""
    config = UebaBaselineConfig(
        baseline_window_days=7,
        min_sample_count=50,
        top_source_ip_limit=3,
        common_hour_min_ratio=0.2,
        model_version="test_model",
        write_batch_size=2,
    )

    assert config.baseline_window_days == 7
    assert config.min_sample_count == 50
    assert config.top_source_ip_limit == 3
    assert config.common_hour_min_ratio == 0.2
    assert config.model_version == "test_model"
    assert config.write_batch_size == 2
