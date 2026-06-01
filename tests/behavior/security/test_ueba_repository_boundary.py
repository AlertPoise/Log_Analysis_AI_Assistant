"""Repository 提交级安全边界测试。

仅保护表名白名单、database 标识符安全、参数化查询、禁止 SELECT *、
数据库侧聚合和旧字段禁止等关键约束。

完整 FakeClient SQL 形状测试保留在 tests/behavior/test_ueba_repository.py，
保留在 local_only 中。
"""

from __future__ import annotations

from src.behavior.repository import UebaRepository


class _FakeClient:
    """极简 fake，只记录最后一条 SQL 和参数。"""

    def __init__(self, query_result=None):
        self._query_result = query_result or _FakeQueryResult([])
        self.last_sql = ""
        self.last_parameters = {}

    def query(self, sql, parameters=None):
        self.last_sql = sql
        self.last_parameters = parameters or {}
        return self._query_result


class _FakeQueryResult:
    def __init__(self, rows):
        self.result_rows = rows
        self.column_names = ["username", "sample_count"]

    def named_results(self):
        return [dict(zip(self.column_names, row)) for row in self.result_rows]


# ---------------------------------------------------------------------------
# 表名白名单
# ---------------------------------------------------------------------------


def test_allowed_source_tables_only_permits_controlled_tables():
    """ALLOWED_SOURCE_TABLES 只允许 logs_structured 和 ueba_baseline_training_logs。"""
    assert UebaRepository.ALLOWED_SOURCE_TABLES == {
        "logs_structured",
        "ueba_baseline_training_logs",
    }


def test_illegal_source_table_is_rejected():
    """非法 source_table 在构造时即被拒绝，不允许进入 SQL。"""
    try:
        UebaRepository(client=_FakeClient(), source_table="logs_structured; DROP TABLE x")
        assert False, "应该抛出 ValueError"
    except ValueError as exc:
        assert "unsupported source_table" in str(exc)


# ---------------------------------------------------------------------------
# database 标识符安全
# ---------------------------------------------------------------------------


def test_illegal_database_identifier_is_rejected():
    """非法 database 标识符在构造时即被拒绝。"""
    try:
        UebaRepository(client=_FakeClient(), database="log_analysis; DROP TABLE x")
        assert False, "应该抛出 ValueError"
    except ValueError as exc:
        assert "invalid ClickHouse identifier" in str(exc)


# ---------------------------------------------------------------------------
# 训练表 source 约束
# ---------------------------------------------------------------------------


def test_training_source_table_requires_dataset_id():
    """source_table 为训练表时，缺少 dataset_id 必须被拒绝。"""
    try:
        UebaRepository(
            client=_FakeClient(),
            source_table="ueba_baseline_training_logs",
            dataset_id=None,
        )
        assert False, "应该抛出 ValueError"
    except ValueError as exc:
        assert "dataset_id is required" in str(exc)


# ---------------------------------------------------------------------------
# 聚合字段白名单
# ---------------------------------------------------------------------------


def test_internal_aggregation_column_whitelist_rejects_unknown_field():
    """内部聚合字段白名单拒绝未知字段（如已废弃的 endpoint）。"""
    repo = UebaRepository(client=_FakeClient())
    try:
        repo._fetch_distribution(
            start_time="2024-01-01",
            end_time="2024-02-01",
            log_type="vpn",
            column_name="endpoint",
            output_name="endpoint",
        )
        assert False, "应该抛出 ValueError"
    except ValueError as exc:
        assert "unsupported aggregation column" in str(exc)


# ---------------------------------------------------------------------------
# Top-N limit 必须为正整数
# ---------------------------------------------------------------------------


def test_top_n_limit_must_be_positive_integer():
    """Top-N limit <= 0 必须在查询前被拒绝。"""
    repo = UebaRepository(client=_FakeClient())
    try:
        repo.fetch_top_source_ips(
            start_time="2024-01-01",
            end_time="2024-02-01",
            limit=0,
        )
        assert False, "应该抛出 ValueError"
    except ValueError:
        pass


# ---------------------------------------------------------------------------
# 参数化查询（sentinel 值不直接出现在 SQL 中）
# ---------------------------------------------------------------------------


def test_fetch_user_summary_uses_parameterized_time_and_log_type():
    """start_time / end_time / log_type 通过参数化传入，不拼接进 SQL 字符串。"""
    client = _FakeClient()
    repo = UebaRepository(client=client)
    repo.fetch_user_summary(
        start_time="2024-01-01 00:00:00",
        end_time="2024-02-01 00:00:00",
        log_type="vpn",
    )

    sql = client.last_sql
    params = client.last_parameters
    assert "2024-01-01" not in sql
    assert "2024-02-01" not in sql
    assert "vpn" not in sql.lower().split("from")[0] if "from" in sql.lower() else True
    assert params.get("start_time") is not None
    assert params.get("end_time") is not None
    assert params.get("log_type") is not None


# ---------------------------------------------------------------------------
# 禁止 SELECT *
# ---------------------------------------------------------------------------


def test_fetch_user_summary_does_not_use_select_star():
    """聚合查询不得使用 SELECT *（必须显式列出列或聚合函数）。"""
    client = _FakeClient()
    repo = UebaRepository(client=client)
    repo.fetch_user_summary("2024-01-01", "2024-02-01")

    sql_upper = client.last_sql.upper()
    assert "SELECT *" not in sql_upper


# ---------------------------------------------------------------------------
# 数据库侧聚合（必须有 GROUP BY）
# ---------------------------------------------------------------------------


def test_fetch_user_summary_uses_group_by():
    """聚合查询必须包含 GROUP BY，确保数据库侧聚合，防止拉取原始日志。"""
    client = _FakeClient()
    repo = UebaRepository(client=client)
    repo.fetch_user_summary("2024-01-01", "2024-02-01")

    sql_upper = client.last_sql.upper()
    assert "GROUP BY" in sql_upper, (
        "缺少 GROUP BY——这会导致查询退化为拉取大量原始日志后在 Python 中聚合，"
        "违反数据库侧聚合原则"
    )


# ---------------------------------------------------------------------------
# 旧字段禁止
# ---------------------------------------------------------------------------


def test_fetch_user_summary_does_not_use_legacy_fields():
    """聚合 SQL 不得引用已废弃的 behavior 字段。"""
    client = _FakeClient()
    repo = UebaRepository(client=client)
    repo.fetch_user_summary("2024-01-01", "2024-02-01")

    sql_upper = client.last_sql.upper()
    for legacy in ("ENDPOINT", "STATUS", "LOCATION", "RAW_MESSAGE"):
        assert legacy not in sql_upper, f"SQL 包含已废弃字段: {legacy}"


# ---------------------------------------------------------------------------
# 多个 fetch 方法共用同样的安全约束（参数化抽样）
# ---------------------------------------------------------------------------

import pytest


_FETCH_METHODS_NEEDING_PARAMS = [
    "fetch_hour_distribution",
    "fetch_top_source_ips",
    "fetch_daily_event_counts",
]


@pytest.mark.parametrize("method_name", _FETCH_METHODS_NEEDING_PARAMS)
def test_key_fetch_methods_use_parameterized_sentinels(method_name):
    """关键 fetch 方法的 sentinel 时间/类型参数不直接出现在 SQL 中。"""
    client = _FakeClient()
    repo = UebaRepository(client=client)
    method = getattr(repo, method_name)

    kwargs: dict = {"start_time": "2024-01-01", "end_time": "2024-02-01", "log_type": "vpn"}
    # 只对需要 limit 的 Top-N 方法添加 limit
    if "top_" in method_name:
        kwargs["limit"] = 5

    method(**kwargs)

    sql = client.last_sql
    params = client.last_parameters
    for param_key in ("start_time", "end_time", "log_type"):
        assert params.get(param_key) is not None, f"{method_name} 缺少参数: {param_key}"
    assert "2024-01-01" not in sql, f"{method_name} 将 start_time 拼入 SQL"
    assert "2024-02-01" not in sql, f"{method_name} 将 end_time 拼入 SQL"
