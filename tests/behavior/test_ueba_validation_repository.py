"""Tests for UEBA validation result repository."""

import json
from pathlib import Path

import pytest

from src.behavior.validation_repository import UebaValidationRepository
from src.behavior.validation_schemas import ScoreReason, UebaValidationResult


class FakeClient:
    """Capture ClickHouse commands and inserts without a real database."""

    def __init__(self):
        self.commands = []
        self.inserts = []

    def command(self, sql):
        self.commands.append(sql)

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
