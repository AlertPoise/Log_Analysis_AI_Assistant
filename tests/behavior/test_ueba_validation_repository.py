"""UEBA validation repository idempotency and SQL regression tests."""

from datetime import datetime, timezone
import re

from src.behavior.validation_repository import UebaValidationRepository
from src.behavior.validation_schemas import UebaValidationResult, ValidationTargetLog


START_TIME = "A_START_SENTINEL"
END_TIME = "Z_END_SENTINEL"
LOG_TYPE = "LOG_TYPE_SENTINEL"
MODEL_VERSION = "MODEL_VERSION_SENTINEL"


class FakeClient:
    """Capture ClickHouse calls without a real database."""

    def __init__(self, *, existing_keys=None, target_rows=None) -> None:
        self.existing_keys = set(existing_keys or set())
        self.target_rows = list(target_rows or [])
        self.commands: list[str] = []
        self.queries: list[dict] = []
        self.inserts: list[dict] = []

    def command(self, sql):
        self.commands.append(sql)

    def query(self, sql, parameters=None):
        parameters = parameters or {}
        self.queries.append({"sql": sql, "parameters": parameters})
        normalized = " ".join(sql.lower().split())
        if "from log_analysis.ueba_validation_results" in normalized:
            versions = set(parameters.get("baseline_model_versions", []))
            ids = set(parameters.get("source_log_ids", []))
            return [
                {"baseline_model_version": version, "source_log_id": source_id}
                for version, source_id in sorted(self.existing_keys)
                if version in versions and source_id in ids
            ]
        if "from log_analysis.logs_structured" in normalized:
            return self.target_rows
        return []

    def insert(self, table, rows, column_names=None, database=None):
        self.inserts.append(
            {
                "table": table,
                "rows": rows,
                "column_names": column_names,
                "database": database,
            }
        )


def _target_row(**overrides):
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


def _result(
    validation_id="validation-1",
    *,
    source_log_id=1001,
    model_version=MODEL_VERSION,
    validation_run_id="run-1",
) -> UebaValidationResult:
    return UebaValidationResult(
        validation_id=validation_id,
        validation_run_id=validation_run_id,
        source_identity=f"request_id:req-{source_log_id}",
        source_log_id=source_log_id,
        timestamp="2024-03-01 10:00:00",
        username="alice",
        log_type="vpn",
        baseline_model_version=model_version,
        baseline_created_at="2024-02-29 00:00:00",
        baseline_is_reliable=True,
        ueba_score=35,
        ueba_risk_level="MEDIUM",
        ueba_anomaly_reasons=[],
        validation_status="VALIDATED",
        validated_at="2024-03-01 10:00:10",
        request_id=f"req-{source_log_id}",
    )


def _inserted_rows(client: FakeClient) -> list[dict]:
    rows = []
    for insert in client.inserts:
        for raw in insert["rows"]:
            rows.append(dict(zip(insert["column_names"], raw)))
    return rows


def _normalize(sql: str) -> str:
    return " ".join(sql.split()).lower()


def test_fetch_target_logs_basic_read() -> None:
    client = FakeClient(target_rows=[_target_row()])
    repository = UebaValidationRepository(client)

    rows = repository.fetch_target_logs(START_TIME, END_TIME, LOG_TYPE, limit=25)

    assert len(rows) == 1
    assert isinstance(rows[0], ValidationTargetLog)
    assert rows[0].id == 2001
    assert rows[0].is_off_hours is False
    assert rows[0].is_unusual_ip is True
    call = client.queries[0]
    sql = call["sql"]
    normalized = _normalize(sql)
    assert "select *" not in normalized
    assert "from log_analysis.logs_structured" in normalized
    assert "log_type = %(log_type)s" in normalized
    assert START_TIME not in sql
    assert END_TIME not in sql
    assert LOG_TYPE not in sql
    assert call["parameters"] == {
        "start_time": START_TIME,
        "end_time": END_TIME,
        "log_type": LOG_TYPE,
        "limit": 25,
    }


def test_fetch_target_logs_can_exclude_already_validated_logs() -> None:
    client = FakeClient(target_rows=[])
    repository = UebaValidationRepository(client)

    rows = repository.fetch_target_logs(
        START_TIME,
        END_TIME,
        LOG_TYPE,
        limit=25,
        exclude_already_validated=True,
        baseline_model_version=MODEL_VERSION,
    )

    assert rows == []
    call = client.queries[0]
    normalized = _normalize(call["sql"])
    assert "from log_analysis.ueba_validation_results" in normalized
    assert "baseline_model_version = %(baseline_model_version)s" in normalized
    assert "id > 0" in normalized
    assert "id not in" in normalized
    assert call["parameters"]["baseline_model_version"] == MODEL_VERSION
    assert MODEL_VERSION not in call["sql"]


def test_fetch_target_logs_incremental_requires_model_version() -> None:
    repository = UebaValidationRepository(FakeClient())

    try:
        repository.fetch_target_logs(
            START_TIME,
            END_TIME,
            LOG_TYPE,
            exclude_already_validated=True,
        )
        assert False, "expected ValueError"
    except ValueError as exc:
        assert "baseline_model_version" in str(exc)


def test_save_validation_results_empty_input() -> None:
    client = FakeClient()
    repository = UebaValidationRepository(client)

    assert repository.save_validation_results([]) == 0
    assert client.commands == []
    assert client.queries == []
    assert client.inserts == []


def test_save_validation_results_inserts_all_new_results() -> None:
    client = FakeClient()
    repository = UebaValidationRepository(client, write_batch_size=10)

    written = repository.save_validation_results([
        _result("validation-1", source_log_id=1001),
        _result("validation-2", source_log_id=1002),
    ])

    assert written == 2
    assert len(client.commands) == 1
    assert len(client.queries) == 1
    assert len(client.inserts) == 1
    assert [row["source_log_id"] for row in _inserted_rows(client)] == [1001, 1002]
    key_query = client.queries[0]
    assert key_query["parameters"]["baseline_model_versions"] == [MODEL_VERSION]
    assert key_query["parameters"]["source_log_ids"] == [1001, 1002]
    assert "validation-1" not in key_query["sql"]


def test_save_validation_results_skips_existing_log_for_same_model() -> None:
    client = FakeClient(existing_keys={(MODEL_VERSION, 1001)})
    repository = UebaValidationRepository(client)

    written = repository.save_validation_results([_result(source_log_id=1001)])

    assert written == 0
    assert client.inserts == []


def test_save_validation_results_inserts_only_new_logs() -> None:
    client = FakeClient(existing_keys={(MODEL_VERSION, 1001)})
    repository = UebaValidationRepository(client)

    written = repository.save_validation_results([
        _result("validation-old", source_log_id=1001),
        _result("validation-new", source_log_id=1002),
    ])

    assert written == 1
    inserted = _inserted_rows(client)
    assert [row["source_log_id"] for row in inserted] == [1002]


def test_save_validation_results_allows_same_log_for_different_model_version() -> None:
    client = FakeClient(existing_keys={("other_model", 1001)})
    repository = UebaValidationRepository(client)

    written = repository.save_validation_results([_result(source_log_id=1001, model_version=MODEL_VERSION)])

    assert written == 1
    assert _inserted_rows(client)[0]["baseline_model_version"] == MODEL_VERSION


def test_save_validation_results_skips_same_source_log_inside_batch_even_with_different_run_id() -> None:
    client = FakeClient()
    repository = UebaValidationRepository(client)

    written = repository.save_validation_results([
        _result("validation-run-1", source_log_id=1001, validation_run_id="run-1"),
        _result("validation-run-2", source_log_id=1001, validation_run_id="run-2"),
    ])

    assert written == 1
    inserted = _inserted_rows(client)
    assert len(inserted) == 1
    assert inserted[0]["source_log_id"] == 1001


def test_save_validation_results_skips_invalid_source_log_id() -> None:
    client = FakeClient()
    repository = UebaValidationRepository(client)

    written = repository.save_validation_results([_result(source_log_id=0)])

    assert written == 0
    assert client.inserts == []


def test_save_validation_results_batches_insert_after_dedupe() -> None:
    client = FakeClient()
    repository = UebaValidationRepository(client, write_batch_size=2)

    written = repository.save_validation_results([
        _result("validation-1", source_log_id=1001),
        _result("validation-2", source_log_id=1002),
        _result("validation-3", source_log_id=1003),
    ])

    assert written == 3
    assert len(client.queries) == 1
    assert len(client.inserts) == 2
    assert len(client.inserts[0]["rows"]) == 2
    assert len(client.inserts[1]["rows"]) == 1


def test_validation_result_to_row_uses_datetime_objects() -> None:
    row = UebaValidationRepository(FakeClient()).validation_result_to_row(_result())

    assert isinstance(row["timestamp"], datetime)
    assert row["timestamp"].tzinfo is timezone.utc
    assert row["source_log_id"] == 1001
    assert row["baseline_model_version"] == MODEL_VERSION
