"""TrainingLogStore 提交级安全边界测试。

仅保护 database 标识符安全、DELETE WHERE 参数化、append/replace 边界
和 INSERT SELECT 筛选条件参数化。

注意：当前实现的 PREWHERE 位于 INSERT SELECT 的 SELECT 子查询中；
本边界测试保护的是参数化筛选，不绑定 PREWHERE 这一实现方式。

完整 DDL 字段/INSERT_COLUMNS 测试保留在 tests/behavior/test_ueba_training_log_store.py，
等待 P4 迁入 local_only。
"""

from __future__ import annotations

from src.behavior.training_log_store import TrainingLogStore


class _FakeClient:
    """极简 fake，记录 command 调用和参数。"""

    def __init__(self, query_result=None):
        self.commands = []
        self.query_calls = []
        self._query_result = query_result or _FakeQueryResult([{"cnt": 10}])

    def command(self, sql, parameters=None):
        self.commands.append({"sql": sql, "parameters": parameters or {}})

    def query(self, sql, parameters=None):
        self.query_calls.append({"sql": sql, "parameters": parameters or {}})
        return self._query_result


class _FakeQueryResult:
    """模拟 clickhouse-connect 查询结果，走 named_results 路径。"""

    def __init__(self, rows):
        self.result_rows = rows
        self.column_names = list(rows[0].keys()) if rows else []
        self._named = [dict(row) for row in rows]

    def named_results(self):
        return self._named


def _store(client=None):
    return TrainingLogStore(client=client or _FakeClient())


# ---------------------------------------------------------------------------
# database 标识符安全
# ---------------------------------------------------------------------------


def test_illegal_database_identifier_is_rejected():
    """非法 database 标识符在构造时即被拒绝。"""
    try:
        TrainingLogStore(client=_FakeClient(), database="log_analysis; DROP TABLE x")
        assert False, "应该抛出 ValueError"
    except ValueError as exc:
        assert "invalid ClickHouse identifier" in str(exc)


# ---------------------------------------------------------------------------
# SOURCE / TARGET 表名固定
# ---------------------------------------------------------------------------


def test_source_table_is_logs_structured():
    """SOURCE_TABLE 固定为 logs_structured。"""
    assert TrainingLogStore.SOURCE_TABLE == "logs_structured"


def test_target_table_is_ueba_baseline_training_logs():
    """TARGET_TABLE 固定为 ueba_baseline_training_logs。"""
    assert TrainingLogStore.TARGET_TABLE == "ueba_baseline_training_logs"


# ---------------------------------------------------------------------------
# replace 模式：先 DELETE（参数化）再 INSERT
# ---------------------------------------------------------------------------


def test_replace_mode_deletes_dataset_before_insert():
    """replace 模式必须先执行 DELETE WHERE dataset_id = %(dataset_id)s。"""
    client = _FakeClient()
    _store(client).replace_from_logs_structured(
        dataset_id="ds-001",
        baseline_purpose="initial_build",
        import_batch_id="batch-1",
        start_time="2024-01-01",
        end_time="2024-02-01",
        log_type="vpn",
    )

    delete_cmd = client.commands[0]
    assert "DELETE" in delete_cmd["sql"].upper()
    assert "ALTER TABLE" in delete_cmd["sql"].upper()
    assert "dataset_id" in delete_cmd["sql"]
    assert delete_cmd["parameters"].get("dataset_id") == "ds-001"
    # dataset_id 值不直接出现在 SQL 中
    assert "ds-001" not in delete_cmd["sql"]


# ---------------------------------------------------------------------------
# append 模式：不 DELETE
# ---------------------------------------------------------------------------


def test_append_mode_does_not_delete():
    """append 模式不得产生任何 DELETE 语句。"""
    client = _FakeClient()
    _store(client).append_from_logs_structured(
        dataset_id="ds-002",
        baseline_purpose="manual_update",
        import_batch_id="batch-2",
        start_time="2024-01-01",
        end_time="2024-02-01",
        log_type="vpn",
    )

    for cmd in client.commands:
        assert "DELETE" not in cmd["sql"].upper(), "append 模式不应包含 DELETE"


# ---------------------------------------------------------------------------
# INSERT SELECT 筛选条件参数化
# ---------------------------------------------------------------------------


def test_insert_select_uses_parameterized_filters():
    """INSERT SELECT 的筛选条件（log_type / start_time / end_time）必须参数化。"""
    client = _FakeClient()
    _store(client).append_from_logs_structured(
        dataset_id="ds-003",
        baseline_purpose="test",
        import_batch_id="batch-3",
        start_time="2024-03-01 00:00:00",
        end_time="2024-04-01 00:00:00",
        log_type="vpn",
    )

    # 找到 INSERT 命令（可能是最后一个 command）
    insert_cmd = client.commands[-1]
    params = insert_cmd["parameters"]
    sql_upper = insert_cmd["sql"].upper()

    assert "INSERT" in sql_upper
    assert params.get("log_type") == "vpn"
    assert params.get("start_time") is not None
    assert params.get("end_time") is not None
    # 参数值不直接出现在 SQL 中（排除可能与列名冲突的短值检查）
    assert "2024-03-01" not in insert_cmd["sql"]
    assert "2024-04-01" not in insert_cmd["sql"]
