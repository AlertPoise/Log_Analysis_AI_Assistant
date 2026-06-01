"""BaselineStore 提交级安全边界测试。

仅保护 database 标识符安全、DDL 表隔离、查询表隔离和参数化查询。
完整序列化/批量写入测试保留在 tests/behavior/test_ueba_baseline_store.py，
保留在 local_only 中。
"""

from __future__ import annotations

from src.behavior.baseline_store import BaselineStore
from src.behavior.config import UebaBaselineConfig


class _FakeClient:
    """极简 fake，记录 command 和 query 调用。"""

    def __init__(self, empty_result=False):
        self.commands = []
        self.query_calls = []
        self._empty = empty_result

    def command(self, sql):
        self.commands.append(sql)

    def query(self, sql, parameters=None):
        self.query_calls.append({"sql": sql, "parameters": parameters or {}})
        if self._empty:
            return _EmptyQueryResult()
        return _FakeQueryResult()

    def insert(self, table, rows, column_names=None, database=None):
        pass


class _FakeQueryResult:
    column_names = ["username", "model_version", "baseline_json", "created_at"]
    result_rows = [("zhangsan", "ueba_baseline_v1", '{"username":"zhangsan"}', None)]


class _EmptyQueryResult:
    column_names = ["username"]
    result_rows = []


# ---------------------------------------------------------------------------
# database 标识符安全
# ---------------------------------------------------------------------------


def test_illegal_database_identifier_is_rejected():
    """非法 database 标识符在构造时即被拒绝。"""
    try:
        BaselineStore(client=_FakeClient(), database="log_analysis; DROP TABLE x")
        assert False, "应该抛出 ValueError"
    except ValueError as exc:
        assert "invalid ClickHouse identifier" in str(exc)


# ---------------------------------------------------------------------------
# DDL 表隔离
# ---------------------------------------------------------------------------


def test_ensure_table_uses_user_behavior_baselines():
    """建表 SQL 必须使用 user_behavior_baselines 表名。"""
    client = _FakeClient()
    BaselineStore(client=client).ensure_table()

    assert len(client.commands) == 1
    assert "user_behavior_baselines" in client.commands[0]


def test_ensure_table_does_not_touch_logs_structured():
    """建表 SQL 不得引用 logs_structured。"""
    client = _FakeClient()
    BaselineStore(client=client).ensure_table()

    assert "logs_structured" not in client.commands[0]


def test_ensure_table_uses_replacing_merge_tree():
    """建表 SQL 必须使用 ReplacingMergeTree(created_at)。"""
    client = _FakeClient()
    BaselineStore(client=client).ensure_table()

    assert "ReplacingMergeTree(created_at)" in client.commands[0]


# ---------------------------------------------------------------------------
# 查询表隔离 + 参数化
# ---------------------------------------------------------------------------


def test_get_user_baseline_queries_baseline_table_not_logs_structured():
    """get_user_baseline 只查询 user_behavior_baselines，不触碰 logs_structured。"""
    client = _FakeClient()
    BaselineStore(client=client).get_user_baseline("zhangsan")

    call = client.query_calls[0]
    assert "user_behavior_baselines" in call["sql"]
    assert "logs_structured" not in call["sql"]


def test_get_user_baseline_uses_parameterized_username():
    """username 通过参数化传入，不拼接进 SQL。"""
    client = _FakeClient()
    BaselineStore(client=client).get_user_baseline("zhangsan", model_version="ueba_baseline_v1")

    call = client.query_calls[0]
    params = call["parameters"]
    assert params == {"username": "zhangsan", "model_version": "ueba_baseline_v1"}
    assert "zhangsan" not in call["sql"]
    assert "ueba_baseline_v1" not in call["sql"]
