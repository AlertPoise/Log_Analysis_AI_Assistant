"""UEBA BaselineBuilder 模块测试。"""

from datetime import datetime


from src.behavior.baseline_builder import BaselineBuilder
from src.behavior.config import UebaBaselineConfig
from src.behavior.schemas import UserAggregateFeature


def _rich_feature(username="zhangsan", sample_count=20):
    """构造覆盖各维度的聚合特征。"""
    return UserAggregateFeature(
        username=username,
        sample_count=sample_count,
        failed_count=2,
        off_hours_count=3,
        unusual_ip_count=1,
        active_days=5,
        hour_counts={9: 10, 3: 1},
        source_ip_counts={"10.0.0.1": 12, "10.0.0.2": 5, "10.0.0.3": 1},
        destination_ip_counts={"10.0.1.1": 11, "10.0.1.2": 9},
        source_country_counts={"CN": 15, "US": 5},
        source_city_counts={"Beijing": 15, "Shanghai": 5},
        vpn_gateway_counts={"gw-1": 15, "gw-2": 5},
        action_counts={"LOGIN": 20},
        event_type_counts={"LOGIN_SUCCESS": 18, "LOGIN_FAIL": 2},
        result_counts={"SUCCESS": 18, "FAIL": 2},
        fail_reason_counts={"PASSWORD_ERROR": 2},
        auth_method_counts={"password": 20},
        client_software_counts={"OpenVPN": 20},
        protocol_counts={"tcp": 20},
        daily_counts={"2026-05-01": 4, "2026-05-02": 6},
        session_metric_summary={
            "session_duration_avg": 300.0,
            "session_duration_p50": 280.0,
            "session_duration_p95": 550.0,
        },
        traffic_metric_summary={"bytes_sent_avg": 1024.0, "bytes_recv_avg": 4096.0},
    )


def test_build_reliable_baseline_with_distributions_and_metrics():
    """验证聚合特征能转换为可靠 Baseline。"""
    config = UebaBaselineConfig(
        top_source_ip_limit=2,
        top_destination_ip_limit=1,
        top_source_country_limit=2,
        top_source_city_limit=2,
        top_vpn_gateway_limit=2,
        common_hour_min_ratio=0.1,
        common_source_ip_min_ratio=0.1,
        common_source_city_min_ratio=0.1,
        common_vpn_gateway_min_ratio=0.1,
    )
    builder = BaselineBuilder(config)
    start_time = datetime(2026, 5, 1)
    end_time = datetime(2026, 5, 6)

    baseline = builder.build_baselines({"zhangsan": _rich_feature()}, start_time, end_time)[0]

    assert baseline.is_reliable is True
    assert [item.value for item in baseline.common_active_hours] == [9]
    assert [item.value for item in baseline.common_source_ips] == ["10.0.0.1", "10.0.0.2"]
    assert [item.value for item in baseline.common_destination_ips] == ["10.0.1.1"]
    assert [item.value for item in baseline.common_source_countries] == ["CN", "US"]
    assert [item.value for item in baseline.common_source_cities] == ["Beijing", "Shanghai"]
    assert [item.value for item in baseline.common_vpn_gateways] == ["gw-1", "gw-2"]
    assert baseline.action_distribution == {"LOGIN": 1.0}
    assert baseline.event_type_distribution == {"LOGIN_SUCCESS": 0.9, "LOGIN_FAIL": 0.1}
    assert baseline.result_distribution == {"SUCCESS": 0.9, "FAIL": 0.1}
    assert baseline.fail_reason_distribution == {"PASSWORD_ERROR": 0.1}
    assert baseline.auth_method_distribution == {"password": 1.0}
    assert baseline.client_software_distribution == {"OpenVPN": 1.0}
    assert baseline.protocol_distribution == {"tcp": 1.0}
    assert abs(baseline.failed_rate - 0.1) < 1e-9
    assert abs(baseline.off_hours_rate - 0.15) < 1e-9
    assert abs(baseline.unusual_ip_rate - 0.05) < 1e-9
    assert abs(baseline.avg_daily_events - 4.0) < 1e-9
    assert abs(baseline.active_day_avg_events - 4.0) < 1e-9
    assert baseline.max_daily_events == 6
    assert baseline.session_duration_avg == 300.0
    assert baseline.session_duration_p50 == 280.0
    assert baseline.session_duration_p95 == 550.0
    assert baseline.bytes_sent_avg == 1024.0
    assert baseline.bytes_recv_avg == 4096.0


def test_unreliable_baseline_is_still_generated():
    """验证样本不足时仍生成不可靠 Baseline。"""
    baseline = BaselineBuilder(UebaBaselineConfig(min_sample_count=20)).build_baselines(
        {"lisi": _rich_feature("lisi", sample_count=1)},
        datetime(2026, 5, 1),
        datetime(2026, 5, 2),
    )[0]

    assert baseline.username == "lisi"
    assert baseline.is_reliable is False


def test_zero_sample_and_empty_dimensions_do_not_crash():
    """验证 sample_count 为 0 时不除零、不崩溃。"""
    feature = UserAggregateFeature(username="empty")
    baseline = BaselineBuilder().build_baselines(
        {"empty": feature},
        datetime(2026, 5, 1),
        datetime(2026, 5, 1),
    )[0]

    assert baseline.is_reliable is False
    assert baseline.common_active_hours == []
    assert baseline.action_distribution == {}
    assert baseline.failed_rate == 0.0
    assert baseline.avg_daily_events == 0.0
    assert baseline.active_day_avg_events == 0.0
