"""Tests for UEBA validation result repository."""

from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import re
import time

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


class FakeColumnQueryResult:
    """Minimal result_rows plus column_names result without named_results."""

    def __init__(self, rows, column_names):
        self.result_rows = rows
        self.column_names = column_names


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


def extract_create_table_columns(sql: str) -> dict[str, str]:
    """Return column names and first type token from a CREATE TABLE statement."""
    match = re.search(r"\(\s*(.*?)\s*\)\s*ENGINE", sql, flags=re.IGNORECASE | re.DOTALL)
    assert match is not None
    columns = {}
    for line in match.group(1).splitlines():
        stripped = line.strip().rstrip(",")
        if not stripped:
            continue
        name, type_name, *_rest = stripped.split()
        columns[name] = type_name
    return columns


def extract_order_by_fields(sql: str) -> list[str]:
    """Return the ordered key columns from a simple ORDER BY tuple."""
    match = re.search(r"ORDER BY\s*\(([^)]*)\)", sql, flags=re.IGNORECASE)
    assert match is not None
    return [field.strip() for field in match.group(1).split(",")]


def assert_unlabeled_transport_datetime(value: object, expected_wall_clock: datetime) -> None:
    """Assert DateTime insert values preserve unlabeled wall-clock semantics."""
    assert isinstance(value, datetime)
    assert not isinstance(value, str)
    assert value.tzinfo is timezone.utc
    assert value.replace(tzinfo=None) == expected_wall_clock


def _result(
    validation_id: str = "validation-1",
    *,
    score: int = 35,
    reliable: bool = True,
    reasons: list[ScoreReason] | None = None,
    timestamp: object = "2024-03-01 10:00:00",
    baseline_model_version: str | None = "ueba_baseline_v1",
    baseline_created_at: object = "2024-02-29 00:00:00",
    validation_status: str = "VALIDATED",
    validated_at: object = "2024-03-01 10:00:10",
) -> UebaValidationResult:
    """Build a representative validation result."""
    return UebaValidationResult(
        validation_id=validation_id,
        source_log_id=1001,
        timestamp=timestamp,
        username="alice",
        log_type="vpn",
        baseline_model_version=baseline_model_version,
        baseline_created_at=baseline_created_at,
        baseline_is_reliable=reliable,
        ueba_score=score,
        ueba_risk_level="MEDIUM",
        ueba_anomaly_reasons=reasons or [],
        validation_status=validation_status,
        validated_at=validated_at,
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


def test_ensure_table_uses_non_nullable_baseline_model_version():
    """baseline_model_version is part of ORDER BY and must not be Nullable."""
    client = FakeClient()
    UebaValidationRepository(client).ensure_table()

    columns = extract_create_table_columns(client.commands[0])
    assert columns["baseline_model_version"] == "String"


def test_ensure_table_order_by_excludes_nullable_columns():
    """ClickHouse MergeTree ORDER BY should not include Nullable columns."""
    client = FakeClient()
    UebaValidationRepository(client).ensure_table()

    columns = extract_create_table_columns(client.commands[0])
    nullable_columns = {
        name for name, type_name in columns.items() if "nullable" in type_name.lower()
    }
    order_by_fields = set(extract_order_by_fields(client.commands[0]))

    assert order_by_fields.isdisjoint(nullable_columns)


def test_ensure_table_does_not_enable_nullable_sorting_key():
    """The result table should not rely on a nullable sorting-key setting."""
    client = FakeClient()
    UebaValidationRepository(client).ensure_table()

    forbidden_setting = "allow" + "_nullable" + "_key"
    assert forbidden_setting not in normalize_sql(client.commands[0]).lower()


def test_ensure_table_contains_order_by():
    """The result table should define the stable validation lookup key."""
    client = FakeClient()
    UebaValidationRepository(client).ensure_table()

    normalized = normalize_sql(client.commands[0]).lower()
    assert (
        "order by (baseline_model_version, log_type, timestamp, username, source_log_id)"
        in normalized
    )
    assert extract_order_by_fields(client.commands[0]) == [
        "baseline_model_version",
        "log_type",
        "timestamp",
        "username",
        "source_log_id",
    ]


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


def test_validation_result_to_row_converts_unlabeled_datetime_strings():
    """ClickHouse DateTime columns should use unlabeled wall-clock semantics."""
    row = UebaValidationRepository(FakeClient()).validation_result_to_row(_result())

    assert_unlabeled_transport_datetime(row["timestamp"], datetime(2024, 3, 1, 10, 0, 0))
    assert_unlabeled_transport_datetime(row["validated_at"], datetime(2024, 3, 1, 10, 0, 10))
    assert_unlabeled_transport_datetime(
        row["baseline_created_at"],
        datetime(2024, 2, 29, 0, 0, 0),
    )


def test_to_clickhouse_datetime_returns_transport_datetime_not_string():
    """Direct DateTime conversion should not return str for unlabeled input."""
    value = UebaValidationRepository(FakeClient())._to_clickhouse_datetime(
        "2026-06-01 00:00:00",
        field_name="timestamp",
    )

    assert_unlabeled_transport_datetime(value, datetime(2026, 6, 1, 0, 0, 0))


def test_unlabeled_transport_datetime_ignores_process_timezone(monkeypatch):
    """A process TZ change must not shift the business wall-clock time."""
    original_tz = os.environ.get("TZ")
    monkeypatch.setenv("TZ", "Asia/Shanghai")
    if hasattr(time, "tzset"):
        time.tzset()

    try:
        row = UebaValidationRepository(FakeClient()).validation_result_to_row(
            _result(timestamp="2026-06-01 00:00:00")
        )
        expected_epoch = datetime(2026, 6, 1, 0, 0, 0, tzinfo=timezone.utc).timestamp()

        assert_unlabeled_transport_datetime(row["timestamp"], datetime(2026, 6, 1, 0, 0, 0))
        assert row["timestamp"].timestamp() == expected_epoch
    finally:
        if original_tz is None:
            monkeypatch.delenv("TZ", raising=False)
        else:
            monkeypatch.setenv("TZ", original_tz)
        if hasattr(time, "tzset"):
            time.tzset()


def test_validation_result_to_row_normalizes_iso_and_timezone_marked_strings():
    """Timezone markers are stripped as compatibility input, not converted."""
    row = UebaValidationRepository(FakeClient()).validation_result_to_row(
        _result(
            timestamp="2024-03-01T10:00:00",
            baseline_created_at="2024-02-29T00:00:00Z",
            validated_at="2024-03-01T10:00:10+08:00",
        )
    )

    assert_unlabeled_transport_datetime(row["timestamp"], datetime(2024, 3, 1, 10, 0, 0))
    assert_unlabeled_transport_datetime(
        row["baseline_created_at"],
        datetime(2024, 2, 29, 0, 0, 0),
    )
    assert_unlabeled_transport_datetime(row["validated_at"], datetime(2024, 3, 1, 10, 0, 10))


def test_validation_result_to_row_handles_naive_and_aware_datetimes_as_wall_clock():
    """datetime inputs should keep their wall-clock fields without timezone conversion."""
    timestamp = datetime(2024, 3, 1, 10, 0, 0)
    baseline_created_at = datetime(2024, 2, 29, 0, 0, 0)
    validated_at = datetime(2024, 3, 1, 10, 0, 10, tzinfo=timezone(timedelta(hours=8)))

    row = UebaValidationRepository(FakeClient()).validation_result_to_row(
        _result(
            timestamp=timestamp,
            baseline_created_at=baseline_created_at,
            validated_at=validated_at,
        )
    )

    assert_unlabeled_transport_datetime(row["timestamp"], timestamp)
    assert_unlabeled_transport_datetime(row["baseline_created_at"], baseline_created_at)
    assert_unlabeled_transport_datetime(row["validated_at"], datetime(2024, 3, 1, 10, 0, 10))


def test_validation_result_to_row_rejects_missing_required_datetimes():
    """Non-nullable DateTime columns should fail fast when missing."""
    repository = UebaValidationRepository(FakeClient())

    with pytest.raises(ValueError, match="timestamp is required"):
        repository.validation_result_to_row(_result(timestamp=None))

    with pytest.raises(ValueError, match="validated_at is required"):
        repository.validation_result_to_row(_result(validated_at=None))


def test_validation_result_to_row_rejects_invalid_datetime_string():
    """Invalid DateTime text should be rejected before ClickHouse insert."""
    repository = UebaValidationRepository(FakeClient())

    with pytest.raises(ValueError, match="timestamp must be a valid datetime string"):
        repository.validation_result_to_row(_result(timestamp="not-a-datetime"))


def test_validation_result_to_row_replaces_missing_baseline_version():
    """Rows without a baseline version should use a stable non-null placeholder."""
    row = UebaValidationRepository(FakeClient()).validation_result_to_row(
        _result(
            baseline_model_version=None,
            baseline_created_at=None,
            reliable=False,
            validation_status="NO_BASELINE",
        )
    )

    assert row["baseline_model_version"] == "__NO_BASELINE__"
    assert row["baseline_created_at"] is None
    assert row["validation_status"] == "NO_BASELINE"
    assert row["baseline_is_reliable"] == 0


def test_validation_result_to_row_preserves_existing_baseline_version():
    """Existing baseline versions should be stored unchanged."""
    row = UebaValidationRepository(FakeClient()).validation_result_to_row(
        _result(baseline_model_version="ueba_custom_v2")
    )

    assert row["baseline_model_version"] == "ueba_custom_v2"


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


def test_save_validation_results_inserts_datetime_objects():
    """client.insert should receive transport datetimes for DateTime columns."""
    client = FakeClient()
    repository = UebaValidationRepository(client)

    written = repository.save_validation_results([_result()])

    assert written == 1
    insert_row = dict(zip(client.inserts[0]["column_names"], client.inserts[0]["rows"][0]))
    assert_unlabeled_transport_datetime(
        insert_row["timestamp"],
        datetime(2024, 3, 1, 10, 0, 0),
    )
    assert_unlabeled_transport_datetime(
        insert_row["validated_at"],
        datetime(2024, 3, 1, 10, 0, 10),
    )
    assert_unlabeled_transport_datetime(
        insert_row["baseline_created_at"],
        datetime(2024, 2, 29, 0, 0, 0),
    )


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


MODEL_VERSION = "MODEL_VERSION_SENTINEL"
USERNAME = "USERNAME_SENTINEL"
RISK_LEVEL = "RISK_LEVEL_SENTINEL"
VALIDATION_STATUS = "VALIDATION_STATUS_SENTINEL"


def _validation_row(**overrides):
    """Build one validation result table row for query tests."""
    row = {
        "validation_id": "validation-query-1",
        "source_log_id": 3001,
        "timestamp": "2024-04-01 10:00:00",
        "username": "alice",
        "log_type": "vpn",
        "request_id": "req-query-1",
        "baseline_model_version": "ueba_model_v1",
        "baseline_created_at": "2024-03-31 00:00:00",
        "baseline_is_reliable": 1,
        "ueba_score": 42,
        "ueba_risk_level": "MEDIUM",
        "ueba_anomaly_reasons": "[]",
        "validation_status": "VALIDATED",
        "validated_at": "2024-04-01 10:00:10",
        "error": None,
        "created_at": "2024-04-01 10:00:11",
    }
    row.update(overrides)
    return row


def _validation_tuple(**overrides):
    """Build one tuple row following validation result query columns."""
    row = _validation_row(**overrides)
    return tuple(row[column] for column in UebaValidationRepository.QUERY_COLUMNS)


def capture_query_validation(query_result=None, **kwargs):
    """Run query_validation_results and return the captured query call plus rows."""
    client = FakeClient(
        query_result=[_validation_row()] if query_result is None else query_result,
        exc=kwargs.pop("exc", None),
    )
    repository = UebaValidationRepository(client)
    rows = repository.query_validation_results(
        START_TIME,
        END_TIME,
        MODEL_VERSION,
        log_type=LOG_TYPE,
        limit=kwargs.pop("limit", 25),
        **kwargs,
    )
    assert len(client.queries) == 1
    return client.queries[0], rows


def assert_query_sql_is_controlled(sql: str, parameters: dict, *, optional_filters: bool) -> None:
    """Check validation-result query SQL safety constraints."""
    normalized = normalize_sql(sql).lower()

    assert "from log_analysis.ueba_validation_results" in normalized
    assert not re.search(r"\bselect\s+\*\b", normalized)
    assert "baseline_model_version = %(model_version)s" in normalized
    assert "log_type = %(log_type)s" in normalized
    assert "timestamp >= %(start_time)s" in normalized
    assert "timestamp < %(end_time)s" in normalized
    assert "limit 25" in normalized
    assert "logs_structured" not in normalized
    assert "ueba_baseline_training_logs" not in normalized
    assert "user_behavior_baselines" not in normalized

    for keyword in ("insert", "update", "alter", "delete", "truncate"):
        assert not re.search(rf"\b{keyword}\b", normalized)

    assert START_TIME not in sql
    assert END_TIME not in sql
    assert MODEL_VERSION not in sql
    assert LOG_TYPE not in sql
    assert RISK_LEVEL not in sql
    assert VALIDATION_STATUS not in sql
    assert USERNAME not in sql

    required = {
        "start_time": START_TIME,
        "end_time": END_TIME,
        "model_version": MODEL_VERSION,
        "log_type": LOG_TYPE,
    }
    assert {key: parameters[key] for key in required} == required

    if optional_filters:
        assert "ueba_risk_level = %(risk_level)s" in normalized
        assert "validation_status = %(validation_status)s" in normalized
        assert "username = %(username)s" in normalized
        assert parameters["risk_level"] == RISK_LEVEL
        assert parameters["validation_status"] == VALIDATION_STATUS
        assert parameters["username"] == USERNAME
    else:
        assert "ueba_risk_level = %(risk_level)s" not in normalized
        assert "validation_status = %(validation_status)s" not in normalized
        assert "username = %(username)s" not in normalized
        assert "risk_level" not in parameters
        assert "validation_status" not in parameters
        assert "username" not in parameters


def test_query_validation_results_reads_from_validation_results_table():
    """query_validation_results should read the fixed result table."""
    call, rows = capture_query_validation()

    assert_query_sql_is_controlled(call["sql"], call["parameters"], optional_filters=False)
    assert len(rows) == 1
    assert rows[0]["validation_id"] == "validation-query-1"
    assert rows[0]["source_log_id"] == 3001
    assert rows[0]["baseline_model_version"] == "ueba_model_v1"
    assert rows[0]["ueba_score"] == 42


def test_query_validation_results_appends_optional_filters():
    """Optional risk, status, and username filters should be parameterized."""
    call, _rows = capture_query_validation(
        risk_level=RISK_LEVEL,
        validation_status=VALIDATION_STATUS,
        username=USERNAME,
    )

    assert_query_sql_is_controlled(call["sql"], call["parameters"], optional_filters=True)


def test_query_validation_results_returns_empty_list_for_empty_result():
    """Empty validation result queries should return an empty list."""
    call, rows = capture_query_validation(query_result=[])

    assert_query_sql_is_controlled(call["sql"], call["parameters"], optional_filters=False)
    assert rows == []


def test_query_validation_results_requires_model_version():
    """model_version should reject empty text before querying."""
    repository = UebaValidationRepository(FakeClient())

    with pytest.raises(ValueError, match="model_version must be a non-empty string"):
        repository.query_validation_results(START_TIME, END_TIME, "", log_type=LOG_TYPE)

    with pytest.raises(ValueError, match="model_version must be a non-empty string"):
        repository.query_validation_results(START_TIME, END_TIME, "   ", log_type=LOG_TYPE)


def test_query_validation_results_requires_log_type():
    """log_type should reject empty text before querying."""
    repository = UebaValidationRepository(FakeClient())

    with pytest.raises(ValueError, match="log_type must be a non-empty string"):
        repository.query_validation_results(START_TIME, END_TIME, MODEL_VERSION, log_type="")

    with pytest.raises(ValueError, match="log_type must be a non-empty string"):
        repository.query_validation_results(START_TIME, END_TIME, MODEL_VERSION, log_type="   ")


def test_query_validation_results_limit_must_be_positive():
    """limit should reject non-positive values before querying."""
    repository = UebaValidationRepository(FakeClient())

    with pytest.raises(ValueError, match="limit must be a positive integer"):
        repository.query_validation_results(START_TIME, END_TIME, MODEL_VERSION, log_type=LOG_TYPE, limit=0)

    with pytest.raises(ValueError, match="limit must be a positive integer"):
        repository.query_validation_results(START_TIME, END_TIME, MODEL_VERSION, log_type=LOG_TYPE, limit=-1)


def test_query_validation_results_time_window_must_be_ordered():
    """start_time must be earlier than end_time before querying."""
    repository = UebaValidationRepository(FakeClient())

    with pytest.raises(ValueError, match="start_time must be earlier than end_time"):
        repository.query_validation_results("B", "B", MODEL_VERSION, log_type=LOG_TYPE)

    with pytest.raises(ValueError, match="start_time must be earlier than end_time"):
        repository.query_validation_results("C", "B", MODEL_VERSION, log_type=LOG_TYPE)


def test_query_validation_results_converts_dict_rows():
    """Plain dict rows should convert to result dictionaries."""
    _call, rows = capture_query_validation(
        query_result=[_validation_row(baseline_created_at=None, error="row-error")]
    )

    assert rows[0]["baseline_created_at"] is None
    assert rows[0]["error"] == "row-error"
    assert rows[0]["baseline_is_reliable"] == 1


def test_query_validation_results_converts_tuple_rows():
    """Tuple rows should map by explicit validation result query columns."""
    _call, rows = capture_query_validation(
        query_result=[_validation_tuple(username="bob", ueba_risk_level="LOW")]
    )

    assert rows[0]["username"] == "bob"
    assert rows[0]["ueba_risk_level"] == "LOW"
    assert rows[0]["source_log_id"] == 3001


def test_query_validation_results_converts_named_result_rows():
    """ClickHouse named result rows should convert to result dictionaries."""
    result = FakeNamedQueryResult(
        rows=[_validation_tuple(validation_status="NO_BASELINE")],
        column_names=UebaValidationRepository.QUERY_COLUMNS,
    )

    _call, rows = capture_query_validation(query_result=result)

    assert rows[0]["validation_status"] == "NO_BASELINE"
    assert rows[0]["validation_id"] == "validation-query-1"


def test_query_validation_results_converts_column_result_rows():
    """result_rows plus column_names rows should convert to result dictionaries."""
    result = FakeColumnQueryResult(
        rows=[_validation_tuple(request_id=None)],
        column_names=UebaValidationRepository.QUERY_COLUMNS,
    )

    _call, rows = capture_query_validation(query_result=result)

    assert rows[0]["request_id"] is None
    assert rows[0]["created_at"] == "2024-04-01 10:00:11"


def test_query_validation_results_formats_datetime_values_without_timezone_labels():
    """DateTime result values should be returned as unlabeled strings."""
    tz_datetime = datetime(2024, 4, 1, 10, 0, 0, tzinfo=timezone(timedelta(hours=8)))
    _call, rows = capture_query_validation(
        query_result=[
            _validation_row(
                timestamp=tz_datetime,
                baseline_created_at=datetime(2024, 3, 31, 0, 0, 0),
                validated_at=datetime(2024, 4, 1, 10, 0, 10),
                created_at=datetime(2024, 4, 1, 10, 0, 11),
            )
        ]
    )

    assert rows[0]["timestamp"] == "2024-04-01 10:00:00"
    assert rows[0]["baseline_created_at"] == "2024-03-31 00:00:00"
    assert rows[0]["validated_at"] == "2024-04-01 10:00:10"
    assert rows[0]["created_at"] == "2024-04-01 10:00:11"


def test_query_validation_results_does_not_swallow_query_exceptions():
    """Query failures should propagate to the caller."""
    client = FakeClient(exc=RuntimeError("boom"))
    repository = UebaValidationRepository(client)

    with pytest.raises(RuntimeError, match="boom"):
        repository.query_validation_results(START_TIME, END_TIME, MODEL_VERSION, log_type=LOG_TYPE, limit=25)

    assert len(client.queries) == 1


def test_query_validation_results_rejects_unsafe_database_identifier():
    """database should reject injected SQL fragments for result queries too."""
    with pytest.raises(ValueError, match="invalid ClickHouse identifier"):
        UebaValidationRepository(FakeClient(), database="log_analysis; DROP TABLE x")
