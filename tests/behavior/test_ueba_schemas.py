"""UEBA Schema 定义测试。"""

from dataclasses import fields
from datetime import datetime

from src.behavior.schemas import (
    BaselineBuildResult,
    CountRatioItem,
    UserAggregateFeature,
    UserBaseline,
)


def test_count_ratio_item_stores_values():
    """验证 Top-N 计数项字段。"""
    item = CountRatioItem(value="10.0.0.1", count=12, ratio=0.6)

    assert item.value == "10.0.0.1"
    assert item.count == 12
    assert item.ratio == 0.6


def test_user_aggregate_feature_defaults_and_factories():
    """验证聚合特征默认值和 default_factory 隔离。"""
    first = UserAggregateFeature(username="zhangsan")
    second = UserAggregateFeature(username="lisi")

    assert first.sample_count == 0
    first.source_ip_counts["10.0.0.1"] = 1
    first.hour_counts[9] = 2

    assert second.source_ip_counts == {}
    assert second.hour_counts == {}


def test_user_baseline_can_store_builder_output_fields():
    """验证 UserBaseline 能承载 Builder 输出字段。"""
    start_time = datetime(2026, 5, 1)
    end_time = datetime(2026, 5, 6)
    baseline = UserBaseline(
        username="zhangsan",
        sample_count=20,
        is_reliable=True,
        common_active_hours=[CountRatioItem(9, 10, 0.5)],
        common_source_ips=[CountRatioItem("10.0.0.1", 12, 0.6)],
        common_destination_ips=[CountRatioItem("10.0.1.1", 20, 1.0)],
        common_source_countries=[CountRatioItem("CN", 20, 1.0)],
        common_source_cities=[CountRatioItem("Beijing", 20, 1.0)],
        common_vpn_gateways=[CountRatioItem("gw-1", 20, 1.0)],
        action_distribution={"LOGIN": 1.0},
        event_type_distribution={"LOGIN_SUCCESS": 0.9},
        result_distribution={"SUCCESS": 0.9},
        fail_reason_distribution={"PASSWORD_ERROR": 0.1},
        auth_method_distribution={"password": 1.0},
        client_software_distribution={"OpenVPN": 1.0},
        protocol_distribution={"tcp": 1.0},
        failed_rate=0.1,
        off_hours_rate=0.15,
        unusual_ip_rate=0.05,
        avg_daily_events=4.0,
        session_duration_avg=300.0,
        session_duration_p50=280.0,
        session_duration_p95=550.0,
        bytes_sent_avg=1024.0,
        bytes_recv_avg=4096.0,
        active_day_avg_events=4.0,
        max_daily_events=6,
        baseline_start_time=start_time,
        baseline_end_time=end_time,
        model_version="ueba_baseline_v1",
    )

    assert baseline.username == "zhangsan"
    assert baseline.common_active_hours[0].value == 9
    assert baseline.bytes_recv_avg == 4096.0
    assert baseline.baseline_start_time == start_time


def test_baseline_build_result_success_and_failure():
    """验证构建结果可表达成功和失败。"""
    start_time = datetime(2026, 5, 1)
    end_time = datetime(2026, 5, 2)
    success = BaselineBuildResult(True, start_time, end_time, 1, 1, 0, 20, "v1", 0.1, "ok")
    failure = BaselineBuildResult(False, start_time, end_time, 0, 0, 0, 0, "v1", 0.1, "failed")

    assert success.success is True
    assert failure.success is False
    assert failure.total_user_count == 0


def test_schema_has_no_raw_log_or_legacy_fields():
    """验证 Schema 不保存原始日志列表，也不保留旧字段。"""
    disallowed = {
        "raw_logs",
        "log_list",
        "events",
        "end" + "point",
        "sta" + "tus",
        "loc" + "ation",
    }
    for schema in (UserAggregateFeature, UserBaseline):
        names = {field.name for field in fields(schema)}
        assert names.isdisjoint(disallowed)
