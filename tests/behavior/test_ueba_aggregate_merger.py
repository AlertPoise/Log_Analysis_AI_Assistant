"""UEBA AggregateMerger 模块测试。"""

from datetime import datetime

from src.behavior.aggregate_merger import AggregateMerger


def _merge_all(**overrides):
    """用完整参数调用 merge，便于单项覆盖。"""
    payload = {
        "user_summary_rows": [],
        "hour_rows": [],
        "source_ip_rows": [],
        "destination_ip_rows": [],
        "source_country_rows": [],
        "source_city_rows": [],
        "vpn_gateway_rows": [],
        "action_rows": [],
        "event_type_rows": [],
        "result_rows": [],
        "fail_reason_rows": [],
        "auth_method_rows": [],
        "client_software_rows": [],
        "protocol_rows": [],
        "daily_rows": [],
        "session_metric_rows": [],
    }
    payload.update(overrides)
    return AggregateMerger().merge(**payload)


def test_merge_rows_into_user_aggregate_feature():
    """验证各维度聚合行能按 username 合并。"""
    start_time = datetime(2026, 5, 1)
    features = _merge_all(
        user_summary_rows=[
            {
                "username": "zhangsan",
                "sample_count": 20,
                "failed_count": 2,
                "off_hours_count": 3,
                "unusual_ip_count": 1,
                "active_days": 5,
                "first_seen": start_time,
                "last_seen": datetime(2026, 5, 6),
            }
        ],
        hour_rows=[{"username": "zhangsan", "active_hour": 9, "cnt": 10}],
        source_ip_rows=[{"username": "zhangsan", "source_ip": "10.0.0.1", "cnt": 12}],
        destination_ip_rows=[{"username": "zhangsan", "destination_ip": "10.0.1.1", "cnt": 20}],
        source_country_rows=[{"username": "zhangsan", "source_country": "CN", "cnt": 20}],
        source_city_rows=[{"username": "zhangsan", "source_city": "Beijing", "cnt": 20}],
        vpn_gateway_rows=[{"username": "zhangsan", "vpn_gateway": "gw-1", "cnt": 20}],
        action_rows=[{"username": "zhangsan", "action": "LOGIN", "cnt": 20}],
        event_type_rows=[{"username": "zhangsan", "event_type": "LOGIN_SUCCESS", "cnt": 18}],
        result_rows=[{"username": "zhangsan", "result": "SUCCESS", "cnt": 18}],
        fail_reason_rows=[{"username": "zhangsan", "fail_reason": "PASSWORD_ERROR", "cnt": 2}],
        auth_method_rows=[{"username": "zhangsan", "auth_method": "password", "cnt": 20}],
        client_software_rows=[{"username": "zhangsan", "client_software": "OpenVPN", "cnt": 20}],
        protocol_rows=[{"username": "zhangsan", "protocol": "tcp", "cnt": 20}],
        daily_rows=[{"username": "zhangsan", "event_date": start_time.date(), "cnt": 4}],
        session_metric_rows=[
            {
                "username": "zhangsan",
                "session_duration_avg": 300.0,
                "session_duration_max": 600.0,
                "session_duration_p50": 280.0,
                "session_duration_p95": 550.0,
                "bytes_sent_avg": 1024.0,
                "bytes_sent_max": 2048.0,
                "bytes_recv_avg": 4096.0,
                "bytes_recv_max": 8192.0,
            }
        ],
    )

    feature = features["zhangsan"]
    assert feature.sample_count == 20
    assert feature.failed_count == 2
    assert feature.hour_counts == {9: 10}
    assert feature.source_ip_counts == {"10.0.0.1": 12}
    assert feature.destination_ip_counts == {"10.0.1.1": 20}
    assert feature.source_country_counts == {"CN": 20}
    assert feature.source_city_counts == {"Beijing": 20}
    assert feature.vpn_gateway_counts == {"gw-1": 20}
    assert feature.action_counts == {"LOGIN": 20}
    assert feature.event_type_counts == {"LOGIN_SUCCESS": 18}
    assert feature.result_counts == {"SUCCESS": 18}
    assert feature.fail_reason_counts == {"PASSWORD_ERROR": 2}
    assert feature.auth_method_counts == {"password": 20}
    assert feature.client_software_counts == {"OpenVPN": 20}
    assert feature.protocol_counts == {"tcp": 20}
    assert feature.daily_counts == {str(start_time.date()): 4}
    assert feature.session_metric_summary["session_duration_p95"] == 550.0
    assert feature.traffic_metric_summary["bytes_recv_avg"] == 4096.0


def test_bad_summary_rows_are_skipped():
    """验证缺失关键字段的用户总览行被跳过。"""
    features = _merge_all(
        user_summary_rows=[
            {"username": "", "sample_count": 10},
            {"sample_count": 10},
            {"username": "lisi"},
            {"username": "zhangsan", "sample_count": 20},
        ]
    )

    assert set(features) == {"zhangsan"}


def test_bad_dimension_rows_do_not_break_merge():
    """验证坏维度行被跳过且不影响其他用户。"""
    features = _merge_all(
        user_summary_rows=[{"username": "zhangsan", "sample_count": 20}],
        source_ip_rows=[
            {"username": "zhangsan", "source_ip": "10.0.0.1"},
            {"username": "zhangsan", "source_ip": "10.0.0.2", "cnt": "bad"},
            {"username": "zhangsan", "source_ip": "10.0.0.3", "cnt": -1},
            {"username": "unknown", "source_ip": "10.0.0.4", "cnt": 5},
            {"username": "zhangsan", "source_ip": "10.0.0.5", "cnt": 6},
        ],
    )

    assert features["zhangsan"].source_ip_counts == {"10.0.0.5": 6}
