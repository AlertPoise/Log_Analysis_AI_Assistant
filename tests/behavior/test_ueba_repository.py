"""UEBA Repository SQL 层约束测试。"""

import re
from collections.abc import Callable

import pytest

from src.behavior.repository import UebaRepository


START_TIME = "START_TIME_SENTINEL"
END_TIME = "END_TIME_SENTINEL"
LOG_TYPE = "LOG_TYPE_SENTINEL"


class FakeQueryResult:
    """模拟 clickhouse_connect 的 query result。"""

    def __init__(self, rows=None, column_names=None):
        self.result_rows = rows or []
        self.column_names = column_names or []

    def named_results(self):
        """按列名返回 dict 行，覆盖 repository 的 named_results 分支。"""
        return [dict(zip(self.column_names, row)) for row in self.result_rows]


class FakeRowsResult:
    """只提供 result_rows / column_names，覆盖备用转换分支。"""

    def __init__(self, rows=None, column_names=None):
        self.result_rows = rows or []
        self.column_names = column_names or []


class FakeClient:
    """捕获 Repository 发出的 SQL 和 parameters。"""

    def __init__(self, result=None, exc: Exception | None = None):
        self.calls = []
        self.result = result or FakeQueryResult(rows=[("zhangsan", 1)], column_names=["username", "cnt"])
        self.exc = exc

    def query(self, sql, parameters=None):
        self.calls.append({"sql": sql, "parameters": parameters or {}})
        if self.exc is not None:
            raise self.exc
        return self.result


FetchCall = Callable[[UebaRepository], list[dict]]


def normalize_sql(sql: str) -> str:
    """压缩空白，方便做大小写无关的 SQL 断言。"""
    return " ".join(sql.split())


def assert_no_legacy_fields(sql: str) -> None:
    """确认没有使用旧 behavior 字段；允许 status_code 这类新字段名。"""
    normalized = normalize_sql(sql).lower()
    forbidden_patterns = {
        "endpoint": r"\bendpoint\b",
        "status": r"\bstatus\b",
        "location": r"\blocation\b",
        "raw_message": r"\braw_message\b",
    }
    for field, pattern in forbidden_patterns.items():
        assert not re.search(pattern, normalized), f"SQL 不应使用旧字段 {field}: {sql}"


def assert_common_repository_sql_constraints(sql: str, parameters: dict) -> None:
    """检查所有 Repository 聚合 SQL 的公共约束。"""
    normalized = normalize_sql(sql).lower()

    assert not re.search(r"\bselect\s+\*\b", normalized)
    assert "logs_structured" in normalized
    assert "group by" in normalized
    assert "timestamp >= %(start_time)s" in normalized
    assert "timestamp < %(end_time)s" in normalized
    assert "log_type = %(log_type)s" in normalized
    assert "username != ''" in normalized
    assert parameters["start_time"] == START_TIME
    assert parameters["end_time"] == END_TIME
    assert parameters["log_type"] == LOG_TYPE
    assert START_TIME not in sql
    assert END_TIME not in sql
    assert LOG_TYPE not in sql
    assert_no_legacy_fields(sql)


def build_repository(result=None, exc: Exception | None = None):
    """创建带 FakeClient 的 Repository。"""
    client = FakeClient(result=result, exc=exc)
    return UebaRepository(client), client


def capture_call(fetch_call: FetchCall):
    """执行一次 fetch_* 并返回捕获的 SQL、parameters 和结果。"""
    repository, client = build_repository()
    rows = fetch_call(repository)
    assert len(client.calls) == 1
    call = client.calls[0]
    return call["sql"], call["parameters"], rows


def fetch_calls() -> list[tuple[str, FetchCall]]:
    """列出阶段 11 要覆盖的全部 fetch_* 方法。"""
    return [
        ("fetch_user_summary", lambda repo: repo.fetch_user_summary(START_TIME, END_TIME, LOG_TYPE)),
        ("fetch_hour_distribution", lambda repo: repo.fetch_hour_distribution(START_TIME, END_TIME, LOG_TYPE)),
        ("fetch_top_source_ips", lambda repo: repo.fetch_top_source_ips(START_TIME, END_TIME, 3, LOG_TYPE)),
        ("fetch_top_destination_ips", lambda repo: repo.fetch_top_destination_ips(START_TIME, END_TIME, 3, LOG_TYPE)),
        ("fetch_top_source_countries", lambda repo: repo.fetch_top_source_countries(START_TIME, END_TIME, 3, LOG_TYPE)),
        ("fetch_top_source_cities", lambda repo: repo.fetch_top_source_cities(START_TIME, END_TIME, 3, LOG_TYPE)),
        ("fetch_top_vpn_gateways", lambda repo: repo.fetch_top_vpn_gateways(START_TIME, END_TIME, 3, LOG_TYPE)),
        ("fetch_action_distribution", lambda repo: repo.fetch_action_distribution(START_TIME, END_TIME, LOG_TYPE)),
        ("fetch_event_type_distribution", lambda repo: repo.fetch_event_type_distribution(START_TIME, END_TIME, LOG_TYPE)),
        ("fetch_result_distribution", lambda repo: repo.fetch_result_distribution(START_TIME, END_TIME, LOG_TYPE)),
        ("fetch_fail_reason_distribution", lambda repo: repo.fetch_fail_reason_distribution(START_TIME, END_TIME, 3, LOG_TYPE)),
        ("fetch_auth_method_distribution", lambda repo: repo.fetch_auth_method_distribution(START_TIME, END_TIME, LOG_TYPE)),
        ("fetch_client_software_distribution", lambda repo: repo.fetch_client_software_distribution(START_TIME, END_TIME, 3, LOG_TYPE)),
        ("fetch_protocol_distribution", lambda repo: repo.fetch_protocol_distribution(START_TIME, END_TIME, LOG_TYPE)),
        ("fetch_daily_event_counts", lambda repo: repo.fetch_daily_event_counts(START_TIME, END_TIME, LOG_TYPE)),
        ("fetch_session_metric_summary", lambda repo: repo.fetch_session_metric_summary(START_TIME, END_TIME, LOG_TYPE)),
    ]


@pytest.mark.parametrize("method_name,fetch_call", fetch_calls())
def test_fetch_methods_use_common_sql_constraints(method_name, fetch_call):
    """所有 fetch_* 方法都应走参数化数据库侧 GROUP BY 聚合。"""
    sql, parameters, rows = capture_call(fetch_call)

    assert rows == [{"username": "zhangsan", "cnt": 1}]
    assert_common_repository_sql_constraints(sql, parameters)


def test_fetch_user_summary_uses_current_summary_fields():
    """用户总览统计应使用当前登录 / VPN 字段。"""
    sql, _parameters, _rows = capture_call(lambda repo: repo.fetch_user_summary(START_TIME, END_TIME, LOG_TYPE))
    normalized = normalize_sql(sql).lower()

    assert "result in ('failed', 'fail')" in normalized
    assert "'failed'" in normalized
    assert "'fail'" in normalized
    assert "event_type = 'login_fail'" in normalized
    assert not re.search(r"\bstatus\b", normalized)
    assert "status_code" not in normalized
    assert "is_off_hours" in normalized
    assert "is_unusual_ip" in normalized
    assert "uniqexact(todate(timestamp))" in normalized


def test_fetch_hour_distribution_uses_timestamp_hour():
    """小时分布应从 timestamp 计算活跃小时。"""
    sql, _parameters, _rows = capture_call(lambda repo: repo.fetch_hour_distribution(START_TIME, END_TIME, LOG_TYPE))
    normalized = normalize_sql(sql).lower()

    assert "tohour(timestamp) as active_hour" in normalized
    assert "group by username, active_hour" in normalized


@pytest.mark.parametrize(
    "method_name,fetch_call,source_field,output_field",
    [
        ("fetch_top_source_ips", lambda repo: repo.fetch_top_source_ips(START_TIME, END_TIME, 5, LOG_TYPE), "source_ip", "source_ip"),
        ("fetch_top_destination_ips", lambda repo: repo.fetch_top_destination_ips(START_TIME, END_TIME, 5, LOG_TYPE), "destination_ip", "destination_ip"),
        ("fetch_top_source_countries", lambda repo: repo.fetch_top_source_countries(START_TIME, END_TIME, 5, LOG_TYPE), "src_country", "source_country"),
        ("fetch_top_source_cities", lambda repo: repo.fetch_top_source_cities(START_TIME, END_TIME, 5, LOG_TYPE), "src_city", "source_city"),
        ("fetch_top_vpn_gateways", lambda repo: repo.fetch_top_vpn_gateways(START_TIME, END_TIME, 5, LOG_TYPE), "vpn_gateway", "vpn_gateway"),
        ("fetch_fail_reason_distribution", lambda repo: repo.fetch_fail_reason_distribution(START_TIME, END_TIME, 5, LOG_TYPE), "fail_reason", "fail_reason"),
        ("fetch_client_software_distribution", lambda repo: repo.fetch_client_software_distribution(START_TIME, END_TIME, 5, LOG_TYPE), "client_software", "client_software"),
    ],
)
def test_top_n_queries_have_limits_and_current_fields(method_name, fetch_call, source_field, output_field):
    """Top-N 查询必须有每用户限制，并通过 parameters 传入 limit。"""
    sql, parameters, _rows = capture_call(fetch_call)
    normalized = normalize_sql(sql).lower()

    assert source_field in normalized
    assert f"{source_field} as {output_field}" in normalized
    assert "limit %(limit)s by username" in normalized
    assert parameters["limit"] == 5
    assert "5" not in sql


@pytest.mark.parametrize(
    "method_name,fetch_call,field_name",
    [
        ("fetch_action_distribution", lambda repo: repo.fetch_action_distribution(START_TIME, END_TIME, LOG_TYPE), "action"),
        ("fetch_event_type_distribution", lambda repo: repo.fetch_event_type_distribution(START_TIME, END_TIME, LOG_TYPE), "event_type"),
        ("fetch_result_distribution", lambda repo: repo.fetch_result_distribution(START_TIME, END_TIME, LOG_TYPE), "result"),
        ("fetch_auth_method_distribution", lambda repo: repo.fetch_auth_method_distribution(START_TIME, END_TIME, LOG_TYPE), "auth_method"),
        ("fetch_protocol_distribution", lambda repo: repo.fetch_protocol_distribution(START_TIME, END_TIME, LOG_TYPE), "protocol"),
    ],
)
def test_distribution_queries_use_current_fields(method_name, fetch_call, field_name):
    """分布查询应使用当前字段并过滤空维度值。"""
    sql, _parameters, _rows = capture_call(fetch_call)
    normalized = normalize_sql(sql).lower()

    assert f"{field_name} as {field_name}" in normalized
    assert f"and {field_name} != ''" in normalized
    assert f"group by username, {field_name}" in normalized


def test_daily_event_counts_uses_timestamp_date():
    """每日事件数应按 timestamp 转日期聚合。"""
    sql, _parameters, _rows = capture_call(lambda repo: repo.fetch_daily_event_counts(START_TIME, END_TIME, LOG_TYPE))
    normalized = normalize_sql(sql).lower()

    assert "todate(timestamp) as event_date" in normalized
    assert "group by username, event_date" in normalized


def test_session_metric_summary_uses_session_and_traffic_fields():
    """会话指标摘要应使用 session_duration_sec 和流量字段。"""
    sql, _parameters, _rows = capture_call(lambda repo: repo.fetch_session_metric_summary(START_TIME, END_TIME, LOG_TYPE))
    normalized = normalize_sql(sql).lower()

    assert "avg(session_duration_sec)" in normalized
    assert "max(session_duration_sec)" in normalized
    assert "quantileexact(0.5)(session_duration_sec)" in normalized
    assert "quantileexact(0.95)(session_duration_sec)" in normalized
    assert "avg(bytes_sent)" in normalized
    assert "avg(bytes_recv)" in normalized
    assert "max(bytes_sent)" in normalized
    assert "max(bytes_recv)" in normalized


def test_repository_converts_named_results_to_dicts():
    """Repository 不应把 ClickHouse 原始 result 对象直接返回上层。"""
    result = FakeQueryResult(rows=[("zhangsan", 12)], column_names=["username", "sample_count"])
    repository, _client = build_repository(result=result)

    rows = repository.fetch_user_summary(START_TIME, END_TIME, LOG_TYPE)

    assert rows == [{"username": "zhangsan", "sample_count": 12}]
    assert isinstance(rows, list)
    assert isinstance(rows[0], dict)


def test_repository_converts_result_rows_to_dicts():
    """Repository 应兼容 result_rows / column_names 返回结构。"""
    result = FakeRowsResult(rows=[("zhangsan", 7)], column_names=["username", "cnt"])
    repository, _client = build_repository(result=result)

    rows = repository.fetch_hour_distribution(START_TIME, END_TIME, LOG_TYPE)

    assert rows == [{"username": "zhangsan", "cnt": 7}]


def test_repository_returns_empty_list_for_empty_result():
    """空聚合结果应返回空 list。"""
    result = FakeQueryResult(rows=[], column_names=["username", "cnt"])
    repository, _client = build_repository(result=result)

    assert repository.fetch_user_summary(START_TIME, END_TIME, LOG_TYPE) == []


def test_repository_does_not_swallow_query_exceptions():
    """query 异常应透传，不能伪造成功。"""
    repository, _client = build_repository(exc=RuntimeError("boom"))

    with pytest.raises(RuntimeError, match="boom"):
        repository.fetch_user_summary(START_TIME, END_TIME, LOG_TYPE)


def test_repository_default_database_is_log_analysis():
    """默认 database 保留为 log_analysis。"""
    repository, _client = build_repository()

    assert repository.database == "log_analysis"


def test_repository_rejects_unknown_internal_column():
    """内部聚合字段白名单应拒绝未知字段。"""
    repository, _client = build_repository()

    with pytest.raises(ValueError, match="unsupported aggregation column"):
        repository._fetch_distribution(START_TIME, END_TIME, LOG_TYPE, "endpoint", "endpoint")


def test_top_n_limit_must_be_positive_integer():
    """Top-N limit 必须是正整数，避免无效参数进入 SQL。"""
    repository, _client = build_repository()

    with pytest.raises(ValueError, match="limit must be a positive integer"):
        repository.fetch_top_source_ips(START_TIME, END_TIME, 0, LOG_TYPE)
