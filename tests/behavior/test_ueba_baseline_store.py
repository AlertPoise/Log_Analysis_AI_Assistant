"""UEBA BaselineStore 模块测试。"""

from datetime import datetime
import json

from src.behavior.baseline_store import BaselineStore
from src.behavior.config import UebaBaselineConfig
from src.behavior.schemas import CountRatioItem, UserBaseline


class FakeQueryResult:
    """模拟 clickhouse-connect 查询结果。"""

    column_names = ["username", "model_version", "baseline_json", "created_at"]
    result_rows = [("zhangsan", "ueba_baseline_v1", '{"username": "zhangsan"}', datetime(2026, 5, 7))]


class EmptyQueryResult:
    """模拟空查询结果。"""

    column_names = ["username"]
    result_rows = []


class FakeClient:
    """不连接真实 ClickHouse 的 fake client。"""

    def __init__(self, result=None):
        self.commands = []
        self.inserts = []
        self.query_calls = []
        self.result = result or FakeQueryResult()

    def command(self, sql):
        self.commands.append(sql)

    def insert(self, table, rows, column_names=None, database=None):
        self.inserts.append({"table": table, "rows": rows, "column_names": column_names, "database": database})

    def query(self, sql, parameters=None):
        self.query_calls.append({"sql": sql, "parameters": parameters})
        return self.result


def _baseline(username="zhangsan", reliable=True):
    """构造 UserBaseline 测试样本。"""
    return UserBaseline(
        username=username,
        sample_count=20,
        is_reliable=reliable,
        common_active_hours=[CountRatioItem(9, 10, 0.5)],
        common_source_ips=[CountRatioItem("10.0.0.1", 12, 0.6)],
        common_destination_ips=[CountRatioItem("10.0.1.1", 20, 1.0)],
        common_source_countries=[CountRatioItem("CN", 20, 1.0)],
        common_source_cities=[CountRatioItem("Beijing", 20, 1.0)],
        common_vpn_gateways=[CountRatioItem("gw-1", 20, 1.0)],
        action_distribution={"LOGIN": 1.0},
        event_type_distribution={"LOGIN_SUCCESS": 0.9, "LOGIN_FAIL": 0.1},
        result_distribution={"SUCCESS": 0.9, "FAIL": 0.1},
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
        baseline_start_time=datetime(2026, 5, 1),
        baseline_end_time=datetime(2026, 5, 6),
        model_version="ueba_baseline_v1",
    )


def test_baseline_to_row_serializes_json_fields():
    """验证 Baseline 行序列化。"""
    row = BaselineStore(client=FakeClient()).baseline_to_row(_baseline())

    assert row["username"] == "zhangsan"
    assert row["is_reliable"] == 1
    assert json.loads(row["common_active_hours"])[0]["value"] == 9
    assert json.loads(row["common_source_ips"])[0]["value"] == "10.0.0.1"
    assert json.loads(row["action_distribution"]) == {"LOGIN": 1.0}
    assert json.loads(row["result_distribution"])["FAIL"] == 0.1
    assert json.loads(row["baseline_json"])["username"] == "zhangsan"
    assert json.loads(row["session_metric_summary"])["session_duration_avg"] == 300.0
    assert json.loads(row["traffic_metric_summary"])["bytes_recv_avg"] == 4096.0


def test_baseline_to_row_converts_unreliable_to_zero():
    """验证 is_reliable 写入 UInt8 语义。"""
    row = BaselineStore(client=FakeClient()).baseline_to_row(_baseline(reliable=False))

    assert row["is_reliable"] == 0


def test_ensure_table_uses_expected_table_engine():
    """验证建表 SQL 的表名和 Engine。"""
    client = FakeClient()
    BaselineStore(client=client).ensure_table()

    assert len(client.commands) == 1
    sql = client.commands[0]
    assert "user_behavior_baselines" in sql
    assert "ReplacingMergeTree(created_at)" in sql
    assert "logs_structured" not in sql


def test_save_baselines_batches_by_write_batch_size():
    """验证批量写入按 write_batch_size 分批，不逐条 insert。"""
    client = FakeClient()
    store = BaselineStore(client=client, config=UebaBaselineConfig(write_batch_size=2))

    assert store.save_baselines([]) == 0
    saved = store.save_baselines([_baseline("u1"), _baseline("u2"), _baseline("u3")])

    assert saved == 3
    assert len(client.inserts) == 2
    assert len(client.inserts[0]["rows"]) == 2
    assert len(client.inserts[1]["rows"]) == 1
    assert client.inserts[0]["table"] == "user_behavior_baselines"


def test_get_user_baseline_queries_baseline_table_and_parses_json():
    """验证查询用户最新 Baseline 时解析 baseline_json。"""
    client = FakeClient()
    row = BaselineStore(client=client).get_user_baseline("zhangsan", model_version="ueba_baseline_v1")

    assert row["username"] == "zhangsan"
    assert row["baseline"] == {"username": "zhangsan"}
    call = client.query_calls[0]
    assert "user_behavior_baselines" in call["sql"]
    assert "logs_structured" not in call["sql"]
    assert call["parameters"] == {"username": "zhangsan", "model_version": "ueba_baseline_v1"}


def test_get_user_baseline_returns_none_for_empty_result():
    """验证无记录时返回 None。"""
    row = BaselineStore(client=FakeClient(result=EmptyQueryResult())).get_user_baseline("missing")

    assert row is None
