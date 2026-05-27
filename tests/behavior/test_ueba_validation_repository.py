"""Tests for UEBA validation result repository."""

import json
from pathlib import Path
import re

import pytest

from src.behavior.validation_repository import UebaValidationRepository
from src.behavior.validation_schemas import (
    ScoreReason,
    UebaValidationResult,
    ValidationTargetLog,
)


class FakeNamedQueryResult:
    """Minimal clickhouse-connect result with named_results."""

    def __init__(self, rows, column_names):
        self.result_rows = rows
        self.column_names = column_names

    def named_results(self):
        """Return dict rows like clickhouse-connect named results."""
        return [dict(zip(self.column_names, row)) for row in self.result_rows]


class FakeClient:
    """Capture ClickHouse commands, inserts, and queries without a real database."""

    def __init__(self, query_result=None, exc: Exception | None = None):
        self.commands = []
        self.inserts = []
        self.queries = []
        self.query_result = [] if query_result is None else query_result
        self.exc = exc

    def command(self, sql):
        self.commands.append(sql)

    def query(self, sql, parameters=None):
        self.queries.append({"sql": sql, "parameters": parameters or {}})
        if self.exc is not None:
            raise self.exc
        return self.query_result

    def insert(self, table, rows, column_names=None, database=None):
        self.inserts.append(
            {
                "table": table,
                "rows": rows,
                "column_names": column_names,
                "database": database,
            }
        )


def normalize_sql(sql: str) -> str:
    """Collapse whitespace for SQL assertions."""
    return " ".join(sql.split())


def _result(
    validation_id: str = "validation-1",
    *,
    score: int = 35,
    reliable: bool = True,
    reasons: list[ScoreReason] | None = None,
) -> UebaValidationResult:
    """Build a representative validation result."""
    return UebaValidationResult(
        validation_id=validation_id,
        source_log_id=1001,
        timestamp="2024-03-01 10:00:00",
        username="alice",
        log_type="vpn",
        baseline_model_version="ueba_baseline_v1",
        baseline_created_at="2024-02-29 00:00:00",
        baseline_is_reliable=reliable,
        ueba_score=score,
        ueba_risk_level="MEDIUM",
        ueba_anomaly_reasons=reasons or [],
        validation_status="VALIDATED",
        validated_at="2024-03-01 10:00:10",
        request_id="req-1",
    )


START_TIME = "A_START_TIME_SENTINEL"
END_TIME = "Z_END_TIME_SENTINEL"
LOG_TYPE = "LOG_TYPE_SENTINEL"


def _target_row(**overrides):
    """Build one logs_structured-like row for target-log fetch tests."""
    row = {
        "id": 2001,
        "timestamp": "2024-03-01 11:00:00",
        "username": "alice",
        "log_type": "vpn",
        "source_ip": "10.0.0.10",
        "destination_ip": "10.0.1.20",
        "src_country": "CN",
        "src_city": "Shanghai",
        "vpn_gateway": "gw-1",
        "action": "LOGIN",
        "event_type": "LOGIN_SUCCESS",
        "result": "SUCCESS",
        "fail_reason": None,
        "auth_method": "password",
        "client_software": "client-a",
        "protocol": "ssl",
        "is_off_hours": 0,
        "is_unusual_ip": 1,
        "request_id": "req-target-1",
        "raw_log": "raw-ref",
    }
    row.update(overrides)
    return row


def _target_tuple(**overrides):
    """Build one tuple row following UebaValidationRepository.TARGET_LOG_COLUMNS."""
    row = _target_row(**overrides)
    return tuple(row[column] for column in UebaValidationRepository.TARGET_LOG_COLUMNS)


def capture_fetch(query_result=None, *, limit=25, exc: Exception | None = None):
    """Run fetch_target_logs and return the captured query call plus rows."""
    client = FakeClient(
        query_result=[_target_row()] if query_result is None else query_result,
        exc=exc,
    )
    repository = UebaValidationRepository(client)
    rows = repository.fetch_target_logs(START_TIME, END_TIME, LOG_TYPE, limit=limit)
    assert len(client.queries) == 1
    return client.queries[0], rows


def assert_fetch_sql_is_controlled(sql: str, parameters: dict) -> None:
    """Check the target-log fetch SQL safety constraints."""
    normalized = normalize_sql(sql).lower()

    assert "from log_analysis.logs_structured" in normalized
    assert not re.search(r"\bselect\s+\*\b", normalized)
    assert "timestamp >= %(start_time)s" in normalized
    assert "timestamp < %(end_time)s" in normalized
    assert "log_type = %(log_type)s" in normalized
    assert "username != ''" in normalized
    assert "limit %(limit)s" in normalized
    assert "ueba_baseline_training_logs" not in normalized
    assert "user_behavior_baselines" not in normalized

    for keyword in ("update", "delete", "insert", "alter", "truncate"):
        assert not re.search(rf"\b{keyword}\b", normalized)

    assert parameters == {
        "start_time": START_TIME,
        "end_time": END_TIME,
        "log_type": LOG_TYPE,
        "limit": 25,
    }
    assert START_TIME not in sql
    assert END_TIME not in sql
    assert LOG_TYPE not in sql
    assert " 25" not in sql


def test_ensure_table_creates_validation_results_table():
    """ensure_table should create the fixed validation result table."""
    client = FakeClient()
    UebaValidationRepository(client).ensure_table()

    normalized = normalize_sql(client.commands[0]).lower()
    assert "create table if not exists log_analysis.ueba_validation_results" in normalized


def test_ensure_table_uses_mergetree_engine():
    """The result table should use append-friendly MergeTree."""
    client = FakeClient()
    UebaValidationRepository(client).ensure_table()

    assert "engine = mergetree()" in normalize_sql(client.commands[0]).lower()


def test_ensure_table_partitions_by_timestamp_month():
    """The result table should partition by source log month."""
    client = FakeClient()
    UebaValidationRepository(client).ensure_table()

    assert "partition by toyyyymm(timestamp)" in normalize_sql(client.commands[0]).lower()


def test_ensure_table_contains_order_by():
    """The result table should define an ORDER BY key."""
    client = FakeClient()
    UebaValidationRepository(client).ensure_table()

    normalized = normalize_sql(client.commands[0]).lower()
    assert "order by" in normalized
    for field in ("baseline_model_version", "log_type", "timestamp", "username", "source_log_id"):
        assert field in normalized


def test_ensure_table_has_no_ttl():
    """The first validation result table should keep results by default."""
    client = FakeClient()
    UebaValidationRepository(client).ensure_table()

    normalized = f" {normalize_sql(client.commands[0]).lower()} "
    assert " ttl " not in normalized


def test_ensure_table_does_not_reference_training_table():
    """Validation result table creation must not touch training data."""
    client = FakeClient()
    UebaValidationRepository(client).ensure_table()

    assert "ueba_baseline_training_logs" not in normalize_sql(client.commands[0]).lower()


def test_ensure_table_does_not_modify_baseline_table():
    """Validation result table creation must not modify baseline storage."""
    client = FakeClient()
    UebaValidationRepository(client).ensure_table()

    normalized = normalize_sql(client.commands[0]).lower()
    assert "user_behavior_baselines" not in normalized
    assert "alter table" not in normalized
    assert "delete where" not in normalized


def test_ensure_table_does_not_modify_source_logs():
    """Validation result table creation must not write source log tables."""
    client = FakeClient()
    UebaValidationRepository(client).ensure_table()

    normalized = normalize_sql(client.commands[0]).lower()
    assert "logs_structured" not in normalized
    assert " update " not in f" {normalized} "


def test_validation_result_to_row_serializes_reasons_json():
    """Score reasons should be stored as JSON text."""
    reason = ScoreReason(
        code="NEW_SOURCE_IP",
        message="new source ip",
        score_delta=12,
        evidence={"source_ip": "10.0.0.10"},
    )

    row = UebaValidationRepository(FakeClient()).validation_result_to_row(
        _result(reasons=[reason])
    )

    payload = json.loads(row["ueba_anomaly_reasons"])
    assert payload == [
        {
            "code": "NEW_SOURCE_IP",
            "message": "new source ip",
            "score_delta": 12,
            "evidence": {"source_ip": "10.0.0.10"},
        }
    ]


def test_validation_result_to_row_converts_reliability_to_uint8():
    """baseline_is_reliable should be converted to 1 or 0."""
    repository = UebaValidationRepository(FakeClient())

    assert repository.validation_result_to_row(_result(reliable=True))["baseline_is_reliable"] == 1
    assert repository.validation_result_to_row(_result(reliable=False))["baseline_is_reliable"] == 0


def test_validation_result_to_row_clamps_score_to_zero_to_one_hundred():
    """ueba_score should be clamped to the persisted 0-100 range."""
    repository = UebaValidationRepository(FakeClient())

    assert repository.validation_result_to_row(_result(score=-10))["ueba_score"] == 0
    assert repository.validation_result_to_row(_result(score=150))["ueba_score"] == 100


def test_save_validation_results_empty_list_returns_zero():
    """Empty writes should not create tables or insert rows."""
    client = FakeClient()
    repository = UebaValidationRepository(client)

    assert repository.save_validation_results([]) == 0
    assert client.commands == []
    assert client.inserts == []


def test_save_validation_results_batches_writes():
    """save_validation_results should batch rows by write_batch_size."""
    client = FakeClient()
    repository = UebaValidationRepository(client, write_batch_size=2)

    written = repository.save_validation_results(
        [_result("validation-1"), _result("validation-2"), _result("validation-3")]
    )

    assert written == 3
    assert len(client.commands) == 1
    assert len(client.inserts) == 2
    assert len(client.inserts[0]["rows"]) == 2
    assert len(client.inserts[1]["rows"]) == 1
    assert client.inserts[0]["table"] == "ueba_validation_results"
    assert client.inserts[0]["database"] == "log_analysis"
    assert client.inserts[0]["column_names"] == UebaValidationRepository.COLUMNS


def test_write_batch_size_must_be_positive():
    """write_batch_size should reject non-positive values."""
    with pytest.raises(ValueError, match="write_batch_size must be a positive integer"):
        UebaValidationRepository(FakeClient(), write_batch_size=0)

    with pytest.raises(ValueError, match="write_batch_size must be a positive integer"):
        UebaValidationRepository(FakeClient(), write_batch_size=-1)


def test_database_identifier_must_be_safe():
    """database should reject injected SQL fragments."""
    with pytest.raises(ValueError, match="invalid ClickHouse identifier"):
        UebaValidationRepository(FakeClient(), database="log_analysis; DROP TABLE x")


def test_src_behavior_has_no_forbidden_stage_markers():
    """Runtime behavior source should not contain stage-only fixture markers."""
    forbidden = [
        "." + "tox",
        "fixture" + "_user",
        "2026" + "-05",
        "2026" + "-06",
        "accept" + "ance",
        "manual" + "_training" + "_update",
        "monthly" + "_training" + "_update",
    ]
    files = list(Path("src/behavior").glob("*.py"))

    assert files
    for path in files:
        source = path.read_text(encoding="utf-8")
        for marker in forbidden:
            assert marker not in source, f"{path} contains forbidden marker {marker!r}"


def test_fetch_target_logs_reads_from_logs_structured():
    """fetch_target_logs should read the fixed logs_structured source table."""
    call, rows = capture_fetch()

    assert_fetch_sql_is_controlled(call["sql"], call["parameters"])
    assert len(rows) == 1
    target = rows[0]
    assert isinstance(target, ValidationTargetLog)
    assert target.id == 2001
    assert target.timestamp == "2024-03-01 11:00:00"
    assert target.username == "alice"
    assert target.log_type == "vpn"
    assert target.source_ip == "10.0.0.10"
    assert target.is_off_hours is False
    assert target.is_unusual_ip is True
    assert target.request_id == "req-target-1"
    assert target.raw_log == "raw-ref"


def test_fetch_target_logs_returns_empty_list_for_empty_result():
    """Empty target-log queries should return an empty list."""
    call, rows = capture_fetch(query_result=[])

    assert_fetch_sql_is_controlled(call["sql"], call["parameters"])
    assert rows == []


def test_fetch_target_logs_converts_named_result_rows():
    """ClickHouse named result rows should convert to ValidationTargetLog."""
    result = FakeNamedQueryResult(
        rows=[_target_tuple(is_off_hours=True, is_unusual_ip=False)],
        column_names=UebaValidationRepository.TARGET_LOG_COLUMNS,
    )

    _call, rows = capture_fetch(query_result=result)

    assert rows[0].is_off_hours is True
    assert rows[0].is_unusual_ip is False
    assert rows[0].source_ip == "10.0.0.10"


def test_fetch_target_logs_converts_dict_rows():
    """Plain dict rows should convert to ValidationTargetLog."""
    _call, rows = capture_fetch(
        query_result=[_target_row(is_off_hours="true", is_unusual_ip="0")]
    )

    assert rows[0].is_off_hours is True
    assert rows[0].is_unusual_ip is False
    assert rows[0].destination_ip == "10.0.1.20"


def test_fetch_target_logs_converts_tuple_rows():
    """Tuple rows should be mapped by the explicit target-log column order."""
    _call, rows = capture_fetch(query_result=[_target_tuple(src_city=None, raw_log=None)])

    assert rows[0].src_city is None
    assert rows[0].raw_log is None
    assert rows[0].vpn_gateway == "gw-1"


def test_fetch_target_logs_limit_must_be_positive():
    """limit should reject non-positive values before querying."""
    repository = UebaValidationRepository(FakeClient())

    with pytest.raises(ValueError, match="limit must be a positive integer"):
        repository.fetch_target_logs(START_TIME, END_TIME, LOG_TYPE, limit=0)

    with pytest.raises(ValueError, match="limit must be a positive integer"):
        repository.fetch_target_logs(START_TIME, END_TIME, LOG_TYPE, limit=-1)


def test_fetch_target_logs_time_window_must_be_ordered():
    """start_time must be earlier than end_time before querying."""
    repository = UebaValidationRepository(FakeClient())

    with pytest.raises(ValueError, match="start_time must be earlier than end_time"):
        repository.fetch_target_logs("2024-03-02 00:00:00", "2024-03-02 00:00:00")

    with pytest.raises(ValueError, match="start_time must be earlier than end_time"):
        repository.fetch_target_logs("2024-03-03 00:00:00", "2024-03-02 00:00:00")


def test_fetch_target_logs_does_not_swallow_query_exceptions():
    """Query failures should propagate to the caller."""
    client = FakeClient(exc=RuntimeError("boom"))
    repository = UebaValidationRepository(client)

    with pytest.raises(RuntimeError, match="boom"):
        repository.fetch_target_logs(START_TIME, END_TIME, LOG_TYPE, limit=25)

    assert len(client.queries) == 1


def test_validation_repository_source_has_no_forbidden_stage_markers():
    """validation_repository.py should not carry stage-only fixture markers."""
    source = Path("src/behavior/validation_repository.py").read_text(encoding="utf-8")
    forbidden = [
        "." + "tox",
        "fixture" + "_user",
        "2026" + "-05",
        "2026" + "-06",
        "accept" + "ance",
        "manual" + "_training" + "_update",
        "monthly" + "_training" + "_update",
    ]

    for marker in forbidden:
        assert marker not in source
