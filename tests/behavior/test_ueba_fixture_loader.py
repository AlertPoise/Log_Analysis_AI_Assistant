"""Tests for UEBA acceptance ClickHouse fixture loader."""

from types import SimpleNamespace
import json
import sys

import pytest

from tests.behavior.ueba_baseline_acceptance.clickhouse_writer import (
    INSERT_COLUMNS,
    FixtureClickHouseWriter,
    create_clickhouse_client,
    load_fixture_to_clickhouse,
    validate_identifier,
)
from tests.behavior.ueba_baseline_acceptance.config import AcceptanceConfig


class FakeQueryResult:
    """Minimal clickhouse-connect style query result."""

    def __init__(self, count):
        self.result_rows = [(count,)]
        self.column_names = ["cnt"]


class FakeClickHouseClient:
    """Fake ClickHouse client for loader unit tests."""

    def __init__(self, query_count=0, fail_insert=False, fail_close=False):
        self.commands = []
        self.inserts = []
        self.queries = []
        self.query_count = query_count
        self.fail_insert = fail_insert
        self.fail_close = fail_close
        self.closed = False

    def command(self, sql, parameters=None):
        self.commands.append((sql, parameters))

    def insert(self, table, rows, column_names=None, database=None):
        if self.fail_insert:
            raise RuntimeError("insert failed")
        self.inserts.append(
            {
                "table": table,
                "rows": rows,
                "column_names": column_names,
                "database": database,
            }
        )
        self.query_count += len(rows)

    def query(self, sql, parameters=None):
        self.queries.append((sql, parameters))
        return FakeQueryResult(self.query_count)

    def close(self):
        self.closed = True
        if self.fail_close:
            raise RuntimeError("close failed")


def _small_config(tmp_path):
    return AcceptanceConfig(
        output_dir=tmp_path,
        stable_user_count=0,
        multi_location_user_count=0,
        high_failure_user_count=0,
        offhour_user_count=0,
        ip_long_tail_user_count=0,
        validation_baseline_user_count=0,
        logs_per_main_user=1,
        clickhouse_batch_size=10,
    )


def test_validate_identifier_accepts_safe_names_and_rejects_sql():
    """Identifier validation should reject injected SQL fragments."""
    assert validate_identifier("log_analysis") == "log_analysis"

    with pytest.raises(ValueError):
        validate_identifier("log_analysis; DROP TABLE x")


def test_create_clickhouse_client_gets_client_and_probes_select_one(monkeypatch):
    """Client creation should call get_client and then SELECT 1."""
    fake_client = FakeClickHouseClient()
    calls = []

    def fake_get_client(**kwargs):
        calls.append(kwargs)
        return fake_client

    monkeypatch.setitem(sys.modules, "clickhouse_connect", SimpleNamespace(get_client=fake_get_client))
    config = AcceptanceConfig(clickhouse_host="127.0.0.1", clickhouse_port=8123)

    client = create_clickhouse_client(config)

    assert client is fake_client
    assert calls[0]["host"] == "127.0.0.1"
    assert fake_client.commands[0][0] == "SELECT 1"


def test_insert_logs_uses_explicit_columns_and_batches():
    """insert_logs should batch rows and always pass explicit column names."""
    client = FakeClickHouseClient()
    writer = FixtureClickHouseWriter(client, database="log_analysis", batch_size=2)
    rows = [_row("u1"), _row("u2"), _row("u3")]

    inserted = writer.insert_logs(rows)

    assert inserted == 3
    assert len(client.inserts) == 2
    assert client.inserts[0]["table"] == "logs_structured"
    assert client.inserts[0]["database"] == "log_analysis"
    assert client.inserts[0]["column_names"] == INSERT_COLUMNS
    assert len(client.inserts[0]["rows"]) == 2
    assert len(client.inserts[1]["rows"]) == 1


def test_clean_fixture_logs_limits_delete_scope(tmp_path):
    """Cleanup must target only fixture users inside the configured window and log_type."""
    config = AcceptanceConfig(output_dir=tmp_path)
    client = FakeClickHouseClient()
    writer = FixtureClickHouseWriter(client)

    writer.clean_fixture_logs(config)

    sql, parameters = client.commands[0]
    assert "ALTER TABLE log_analysis.logs_structured" in sql
    assert "username LIKE 'fixture_user_%%'" in sql
    assert "log_type = %(log_type)s" in sql
    assert "timestamp >= %(start_time)s" in sql
    assert "timestamp < %(end_time)s" in sql
    assert "SETTINGS mutations_sync = 1" in sql
    assert parameters == {
        "start_time": config.start_time,
        "end_time": config.end_time,
        "log_type": config.log_type,
    }


def test_count_fixture_logs_limits_query_scope(tmp_path):
    """Count query should use the same fixture/time/log_type filters."""
    config = AcceptanceConfig(output_dir=tmp_path)
    client = FakeClickHouseClient(query_count=123)
    writer = FixtureClickHouseWriter(client)

    count = writer.count_fixture_logs(config)

    sql, parameters = client.queries[0]
    assert count == 123
    assert "SELECT count() AS cnt" in sql
    assert "username LIKE 'fixture_user_%%'" in sql
    assert "log_type = %(log_type)s" in sql
    assert "timestamp >= %(start_time)s" in sql
    assert "timestamp < %(end_time)s" in sql
    assert parameters["log_type"] == config.log_type


def test_load_fixture_to_clickhouse_success_writes_load_result_and_no_raw_jsonl(tmp_path):
    """Successful load should write load_result and keep raw logs out of .tox."""
    config = _small_config(tmp_path)
    client = FakeClickHouseClient()

    result = load_fixture_to_clickhouse(config, client_factory=lambda _config: client)

    assert result["success"] is True
    assert result["expected_rows"] == 130
    assert result["inserted_rows"] == 130
    assert result["database_rows"] == 130
    assert (tmp_path / "expected_baselines.json").exists()
    assert (tmp_path / "fixture_summary.json").exists()
    assert (tmp_path / "load_result.json").exists()
    assert (tmp_path / "run_state.json").exists()
    assert not (tmp_path / "fixture_logs.jsonl").exists()
    assert not (tmp_path / "debug_fixture_logs.jsonl").exists()

    load_result = json.loads((tmp_path / "load_result.json").read_text(encoding="utf-8"))
    run_state = json.loads((tmp_path / "run_state.json").read_text(encoding="utf-8"))
    assert load_result["success"] is True
    assert run_state["clickhouse_loaded"] is True


def test_load_fixture_to_clickhouse_failure_writes_success_false(tmp_path):
    """ClickHouse failures should be captured in load_result.json."""
    config = _small_config(tmp_path)
    client = FakeClickHouseClient(fail_insert=True)

    result = load_fixture_to_clickhouse(config, client_factory=lambda _config: client)

    assert result["success"] is False
    assert result["inserted_rows"] == 0
    assert "insert failed" in result["error"]
    load_result = json.loads((tmp_path / "load_result.json").read_text(encoding="utf-8"))
    run_state = json.loads((tmp_path / "run_state.json").read_text(encoding="utf-8"))
    assert load_result["success"] is False
    assert run_state["clickhouse_loaded"] is False


def test_close_error_does_not_cover_original_error(tmp_path):
    """A close failure must not replace the original ClickHouse error."""
    config = _small_config(tmp_path)
    client = FakeClickHouseClient(fail_insert=True, fail_close=True)

    result = load_fixture_to_clickhouse(config, client_factory=lambda _config: client)

    assert result["success"] is False
    assert "insert failed" in result["error"]
    assert "close failed" not in result["error"]


def _row(username):
    return {
        "timestamp": "2026-05-01 09:00:00",
        "log_type": "vpn",
        "username": username,
        "source_ip": "10.1.1.1",
        "destination_ip": "172.20.1.1",
        "src_country": "中国",
        "src_city": "北京",
        "vpn_gateway": "vpn-gw-cn-01",
        "action": "LOGIN",
        "event_type": "LOGIN_SUCCESS",
        "result": "SUCCESS",
        "fail_reason": "",
        "auth_method": "password+mfa",
        "client_software": "OpenVPN Connect",
        "protocol": "SSLVPN",
        "session_duration_sec": 300,
        "bytes_sent": 2048,
        "bytes_recv": 8192,
        "is_off_hours": False,
        "is_unusual_ip": False,
        "parser": "ueba_fixture_v2",
        "raw_log": "ueba fixture generated log fixture_id=ueba_fixture_v2_monthly_seed_42",
    }
