"""UEBA validation service core regression tests."""

from datetime import datetime

from src.behavior.schemas import CountRatioItem, UserBaseline
from src.behavior.score_calculator import UebaScoreCalculator
from src.behavior.validation_schemas import ValidationTargetLog
from src.behavior.validation_service import UebaValidationService


START_TIME = "2024-03-01 00:00:00"
END_TIME = "2024-03-02 00:00:00"
MODEL_VERSION = "ueba_baseline_v1"
VALIDATED_AT = "2024-03-02 00:00:05"
VALIDATION_RUN_ID = "run-service-1"


class FakeValidationRepository:
    """Fake repository with incremental target filtering semantics."""

    def __init__(
        self,
        target_logs: list[ValidationTargetLog],
        *,
        fetch_exc: Exception | None = None,
        save_exc: Exception | None = None,
    ) -> None:
        self.target_logs = list(target_logs)
        self.validated_keys: set[tuple[str, int]] = set()
        self.fetch_calls: list[dict] = []
        self.saved_results = []
        self.save_called = False
        self.fetch_exc = fetch_exc
        self.save_exc = save_exc

    def fetch_target_logs(
        self,
        start_time,
        end_time,
        log_type="vpn",
        limit=1000,
        exclude_already_validated=False,
        baseline_model_version=None,
    ):
        self.fetch_calls.append(
            {
                "start_time": start_time,
                "end_time": end_time,
                "log_type": log_type,
                "limit": limit,
                "exclude_already_validated": exclude_already_validated,
                "baseline_model_version": baseline_model_version,
            }
        )
        if self.fetch_exc is not None:
            raise self.fetch_exc
        rows = list(self.target_logs)
        if exclude_already_validated:
            rows = [
                row for row in rows
                if (baseline_model_version, row.id) not in self.validated_keys
            ]
        return rows[:limit]

    def save_validation_results(self, results):
        self.save_called = True
        if self.save_exc is not None:
            raise self.save_exc
        self.saved_results.extend(results)
        written = 0
        for result in results:
            key = (result.baseline_model_version, result.source_log_id)
            if result.source_log_id > 0 and key not in self.validated_keys:
                self.validated_keys.add(key)
                written += 1
        return written


class FakeBaselineStore:
    """Fake baseline store that records read calls and rejects writes."""

    def __init__(self, baselines: dict[str, UserBaseline | dict]) -> None:
        self.baselines = dict(baselines)
        self.calls: list[dict] = []
        self.ensure_called = False
        self.save_called = False

    def get_user_baseline(self, username, model_version=None):
        self.calls.append({"username": username, "model_version": model_version})
        return self.baselines.get(username)

    def ensure_table(self):
        self.ensure_called = True
        raise AssertionError("validation must not modify baseline tables")

    def save_baselines(self, baselines):
        self.save_called = True
        raise AssertionError("validation must not save baselines")


def _target(**overrides) -> ValidationTargetLog:
    values = {
        "id": 1001,
        "timestamp": "2024-03-01 09:30:00",
        "username": "alice",
        "log_type": "vpn",
        "source_ip": "10.0.0.1",
        "destination_ip": "10.0.1.1",
        "src_country": "CN",
        "src_city": "Shanghai",
        "vpn_gateway": "gw-1",
        "action": "LOGIN",
        "event_type": "LOGIN_SUCCESS",
        "result": "SUCCESS",
        "auth_method": "password",
        "client_software": "OpenVPN",
        "protocol": "tcp",
        "is_off_hours": False,
        "is_unusual_ip": False,
        "request_id": "req-1",
    }
    values.update(overrides)
    return ValidationTargetLog(**values)


def _baseline(username="alice", reliable=True) -> UserBaseline:
    return UserBaseline(
        username=username,
        sample_count=100 if reliable else 5,
        is_reliable=reliable,
        common_active_hours=[CountRatioItem(9, 80, 0.8)],
        common_source_ips=[CountRatioItem("10.0.0.1", 70, 0.7)],
        common_destination_ips=[CountRatioItem("10.0.1.1", 80, 0.8)],
        common_source_countries=[CountRatioItem("CN", 90, 0.9)],
        common_source_cities=[CountRatioItem("Shanghai", 90, 0.9)],
        common_vpn_gateways=[CountRatioItem("gw-1", 90, 0.9)],
        action_distribution={"LOGIN": 1.0},
        event_type_distribution={"LOGIN_SUCCESS": 1.0},
        result_distribution={"SUCCESS": 1.0},
        fail_reason_distribution={},
        auth_method_distribution={"password": 1.0},
        client_software_distribution={"OpenVPN": 1.0},
        protocol_distribution={"tcp": 1.0},
        failed_rate=0.0,
        off_hours_rate=0.05,
        unusual_ip_rate=0.02,
        avg_daily_events=10.0,
        session_duration_avg=300.0,
        session_duration_p50=280.0,
        session_duration_p95=550.0,
        bytes_sent_avg=1024.0,
        bytes_recv_avg=4096.0,
        active_day_avg_events=10.0,
        max_daily_events=18,
        baseline_start_time=datetime(2024, 2, 1),
        baseline_end_time=datetime(2024, 3, 1),
        model_version=MODEL_VERSION,
    )


def _service(repository, baseline_store):
    return UebaValidationService(
        validation_repository=repository,
        baseline_store=baseline_store,
        score_calculator=UebaScoreCalculator(),
    )


def test_validation_does_not_modify_baseline() -> None:
    repository = FakeValidationRepository([_target()])
    baseline_store = FakeBaselineStore({"alice": _baseline()})

    summary = _service(repository, baseline_store).run(
        START_TIME,
        END_TIME,
        model_version=MODEL_VERSION,
        dry_run=False,
        validated_at=VALIDATED_AT,
        validation_run_id=VALIDATION_RUN_ID,
    )

    assert summary["success"] is True
    assert baseline_store.calls == [{"username": "alice", "model_version": MODEL_VERSION}]
    assert baseline_store.ensure_called is False
    assert baseline_store.save_called is False


def test_validation_service_run_write_mode_idempotent() -> None:
    repository = FakeValidationRepository([
        _target(id=1001, request_id="req-1"),
        _target(id=1002, request_id="req-2"),
    ])
    baseline_store = FakeBaselineStore({"alice": _baseline()})
    service = _service(repository, baseline_store)

    first = service.run(
        START_TIME,
        END_TIME,
        model_version=MODEL_VERSION,
        dry_run=False,
        validated_at=VALIDATED_AT,
        validation_run_id="run-1",
    )
    second = service.run(
        START_TIME,
        END_TIME,
        model_version=MODEL_VERSION,
        dry_run=False,
        validated_at=VALIDATED_AT,
        validation_run_id="run-2",
    )

    assert first["success"] is True
    assert first["written_count"] == 2
    assert first["selected_count"] == 2
    assert second["success"] is True
    assert second["written_count"] == 0
    assert second["selected_count"] == 0
    assert repository.fetch_calls[0]["exclude_already_validated"] is True
    assert repository.fetch_calls[0]["baseline_model_version"] == MODEL_VERSION
    assert repository.fetch_calls[1]["exclude_already_validated"] is True
    assert repository.fetch_calls[1]["baseline_model_version"] == MODEL_VERSION


def test_dry_run_keeps_preview_behavior_without_incremental_filter() -> None:
    repository = FakeValidationRepository([_target()])
    baseline_store = FakeBaselineStore({"alice": _baseline()})

    summary = _service(repository, baseline_store).run(
        START_TIME,
        END_TIME,
        model_version=MODEL_VERSION,
        dry_run=True,
        validated_at=VALIDATED_AT,
    )

    assert summary["success"] is True
    assert summary["written_count"] == 0
    assert repository.save_called is False
    assert repository.fetch_calls[0]["exclude_already_validated"] is False
    assert repository.fetch_calls[0]["baseline_model_version"] == MODEL_VERSION


def test_run_fetch_exception_returns_failure_without_saving() -> None:
    repository = FakeValidationRepository(
        [],
        fetch_exc=RuntimeError("clickhouse password=secret-token fetch failed"),
    )
    baseline_store = FakeBaselineStore({"alice": _baseline()})

    summary = _service(repository, baseline_store).run(
        START_TIME,
        END_TIME,
        model_version=MODEL_VERSION,
        dry_run=False,
        validated_at=VALIDATED_AT,
        validation_run_id=VALIDATION_RUN_ID,
    )

    assert summary["success"] is False
    assert summary["processed_count"] == 0
    assert summary["scored_count"] == 0
    assert summary["written_count"] == 0
    assert summary["message"] == "validation target fetch failed"
    assert "secret-token" not in summary["message"]
    assert "password" not in summary["message"]
    assert baseline_store.calls == []
    assert repository.save_called is False


def test_run_write_mode_save_exception_returns_failure() -> None:
    repository = FakeValidationRepository(
        [_target()],
        save_exc=RuntimeError("clickhouse password=secret-token save failed"),
    )
    baseline_store = FakeBaselineStore({"alice": _baseline()})

    summary = _service(repository, baseline_store).run(
        START_TIME,
        END_TIME,
        model_version=MODEL_VERSION,
        dry_run=False,
        validated_at=VALIDATED_AT,
        validation_run_id=VALIDATION_RUN_ID,
    )

    assert summary["success"] is False
    assert summary["dry_run"] is False
    assert summary["processed_count"] == 1
    assert summary["scored_count"] == 1
    assert summary["written_count"] == 0
    assert summary["message"] == "validation result save failed"
    assert "secret-token" not in summary["message"]
    assert "password" not in summary["message"]
    assert repository.save_called is True
    assert repository.saved_results == []
