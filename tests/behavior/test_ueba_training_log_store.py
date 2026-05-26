"""Tests for UEBA baseline training log store."""

import re

import pytest

from src.behavior.training_log_store import TrainingLogStore


class FakeQueryResult:
    """Minimal clickhouse-connect style query result."""

    def __init__(self, count):
        self.result_rows = [(count,)]
        self.column_names = ["cnt"]


class FakeClient:
    """Capture TrainingLogStore SQL calls."""

    def __init__(self, counts=None):
        self.commands = []
        self.queries = []
        self.counts = list(counts or [0])

    def command(self, sql, parameters=None):
        self.commands.append({"sql": sql, "parameters": parameters or {}})

    def query(self, sql, parameters=None):
        self.queries.append({"sql": sql, "parameters": parameters or {}})
        count = self.counts.pop(0) if self.counts else 0
        return FakeQueryResult(count)


def normalize_sql(sql):
    """Collapse whitespace for assertions."""
    return " ".join(sql.split())


def test_ensure_table_creates_training_table_with_required_fields_and_no_ttl():
    """ensure_table should define the reproducible manual training table."""
    client = FakeClient()
    store = TrainingLogStore(client)

    store.ensure_table()

    sql = client.commands[0]["sql"]
    normalized = normalize_sql(sql).lower()
    assert "create table if not exists log_analysis.ueba_baseline_training_logs" in normalized
    assert "dataset_id string" in normalized
    assert "baseline_purpose string" in normalized
    assert "is_active uint8" in normalized
    assert "import_batch_id string" in normalized
    for field in (
        "timestamp datetime",
        "log_type string",
        "username string",
        "source_ip nullable(string)",
        "destination_ip nullable(string)",
        "vpn_gateway nullable(string)",
        "src_country nullable(string)",
        "src_city nullable(string)",
        "auth_method nullable(string)",
        "client_software nullable(string)",
        "protocol nullable(string)",
        "is_off_hours nullable(bool)",
        "is_unusual_ip nullable(bool)",
        "session_duration_sec nullable(uint32)",
        "bytes_sent nullable(uint64)",
        "bytes_recv nullable(uint64)",
    ):
        assert field in normalized
    assert "engine = mergetree()" in normalized
    assert "partition by toyyyymm(timestamp)" in normalized
    assert "order by (dataset_id, log_type, timestamp, username)" in normalized
    assert " ttl " not in f" {normalized} "


def test_replace_mode_deletes_dataset_before_insert():
    """replace_from_logs_structured should delete the old dataset first."""
    client = FakeClient(counts=[12, 12])
    store = TrainingLogStore(client)

    result = store.replace_from_logs_structured(**_common_args())

    assert result == {"selected_rows": 12, "inserted_rows": 12, "target_rows": 12}
    assert "alter table log_analysis.ueba_baseline_training_logs" in normalize_sql(client.commands[0]["sql"]).lower()
    assert "delete where dataset_id = %(dataset_id)s" in normalize_sql(client.commands[0]["sql"]).lower()
    assert client.commands[0]["parameters"] == {"dataset_id": "baseline_init_test"}
    assert "insert into log_analysis.ueba_baseline_training_logs" in normalize_sql(client.commands[1]["sql"]).lower()


def test_append_mode_does_not_delete_dataset():
    """append_from_logs_structured should only insert selected rows."""
    client = FakeClient(counts=[5, 9])
    store = TrainingLogStore(client)

    result = store.append_from_logs_structured(**_common_args())

    assert result == {"selected_rows": 5, "inserted_rows": 5, "target_rows": 9}
    assert len(client.commands) == 1
    assert "delete where" not in normalize_sql(client.commands[0]["sql"]).lower()
    assert "insert into log_analysis.ueba_baseline_training_logs" in normalize_sql(client.commands[0]["sql"]).lower()


def test_insert_sql_uses_expected_field_order_and_parameters():
    """INSERT SELECT should copy fields in the target table order and parameterize filters."""
    client = FakeClient(counts=[3, 3])
    store = TrainingLogStore(client)

    store.append_from_logs_structured(**_common_args())

    insert_call = client.commands[0]
    sql = insert_call["sql"]
    normalized = normalize_sql(sql).lower()
    assert "insert into log_analysis.ueba_baseline_training_logs" in normalized
    assert re.search(r"dataset_id,\s+baseline_purpose,\s+is_active,\s+import_batch_id,\s+id,\s+timestamp", sql)
    assert "from log_analysis.logs_structured" in normalized
    assert "prewhere log_type = %(log_type)s" in normalized
    assert "timestamp >= %(start_time)s" in normalized
    assert "timestamp < %(end_time)s" in normalized
    assert "where username != ''" in normalized
    assert "toString(id) AS source_record_id" in sql
    assert insert_call["parameters"]["dataset_id"] == "baseline_init_test"
    assert insert_call["parameters"]["baseline_purpose"] == "initial_build"
    assert insert_call["parameters"]["import_batch_id"] == "import_test_001"
    assert insert_call["parameters"]["log_type"] == "vpn"
    assert insert_call["parameters"]["source_table"] == "logs_structured"


def test_count_queries_use_parameters():
    """count_source_rows and count_dataset_rows should use parameterized filters."""
    client = FakeClient(counts=[7, 11])
    store = TrainingLogStore(client)

    assert store.count_source_rows(start_time="s", end_time="e", log_type="vpn") == 7
    assert store.count_dataset_rows("dataset_1") == 11

    source_call = client.queries[0]
    dataset_call = client.queries[1]
    assert source_call["parameters"] == {"start_time": "s", "end_time": "e", "log_type": "vpn"}
    assert "log_type = %(log_type)s" in source_call["sql"]
    assert "timestamp >= %(start_time)s" in source_call["sql"]
    assert dataset_call["parameters"] == {"dataset_id": "dataset_1"}
    assert "dataset_id = %(dataset_id)s" in dataset_call["sql"]


def test_illegal_database_identifier_is_rejected():
    """database must not allow SQL fragments."""
    with pytest.raises(ValueError, match="invalid ClickHouse identifier"):
        TrainingLogStore(FakeClient(), database="log_analysis; DROP TABLE x")


def _common_args():
    return {
        "dataset_id": "baseline_init_test",
        "baseline_purpose": "initial_build",
        "import_batch_id": "import_test_001",
        "start_time": "2026-05-01 00:00:00",
        "end_time": "2026-06-01 00:00:00",
        "log_type": "vpn",
        "created_by": "manual",
    }
