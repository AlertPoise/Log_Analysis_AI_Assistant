"""UEBA validation service dry-run tests."""

from datetime import datetime

from src.behavior.schemas import CountRatioItem, UserBaseline
from src.behavior.score_calculator import UebaScoreCalculator
from src.behavior.validation_schemas import ValidationTargetLog
from src.behavior.validation_service import UebaValidationService


START_TIME = "2024-03-01 00:00:00"
END_TIME = "2024-03-02 00:00:00"
LOG_TYPE = "vpn"
MODEL_VERSION = "ueba_baseline_v1"
VALIDATED_AT = "2024-03-02T00:00:05+00:00"


class FakeValidationRepository:
    """Fake target-log repository that records dry-run reads."""

    def __init__(self, target_logs=None):
        self.target_logs = list(target_logs or [])
        self.fetch_calls = []
        self.save_called = False
        self.source_write_called = False

    def fetch_target_logs(self, start_time, end_time, log_type="vpn", limit=1000):
        self.fetch_calls.append(
            {
                "start_time": start_time,
                "end_time": end_time,
                "log_type": log_type,
                "limit": limit,
            }
        )
        return list(self.target_logs)

    def save_validation_results(self, results):
        self.save_called = True
        raise AssertionError("dry-run must not save validation results")

    def write_source_logs(self, results):
        self.source_write_called = True
        raise AssertionError("dry-run must not write source logs")


class FakeBaselineStore:
    """Fake baseline reader that records lookups and write attempts."""

    def __init__(self, baselines=None):
        self.baselines = dict(baselines or {})
        self.calls = []
        self.ensure_called = False
        self.save_called = False

    def get_user_baseline(self, username, model_version=None):
        self.calls.append({"username": username, "model_version": model_version})
        return self.baselines.get(username)

    def ensure_table(self):
        self.ensure_called = True
        raise AssertionError("dry-run must not ensure write tables")

    def save_baselines(self, baselines):
        self.save_called = True
        raise AssertionError("dry-run must not save baselines")


class SpyScoreCalculator(UebaScoreCalculator):
    """Score calculator spy that still runs the real scoring rules."""

    def __init__(self):
        super().__init__()
        self.calls = []

    def calculate(self, target_log, baseline, model_version=None, validated_at=None):
        self.calls.append(
            {
                "target_log": target_log,
                "baseline": baseline,
                "model_version": model_version,
                "validated_at": validated_at,
            }
        )
        return super().calculate(
            target_log,
            baseline,
            model_version=model_version,
            validated_at=validated_at,
        )


def _target(**overrides):
    """Build one validation target log."""
    values = {
        "id": 1001,
        "timestamp": "2024-03-01 09:30:00",
        "username": "alice",
        "log_type": LOG_TYPE,
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


def _baseline(username="alice", reliable=True):
    """Build a baseline that matches the default target log."""
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


def _baseline_row(username="alice", reliable=True):
    """Build a BaselineStore-like row with parsed baseline payload."""
    baseline = _baseline(username=username, reliable=reliable)
    return {
        "username": username,
        "baseline": {
            "username": baseline.username,
            "sample_count": baseline.sample_count,
            "is_reliable": 1 if baseline.is_reliable else 0,
            "common_active_hours": [{"value": 9, "count": 80, "ratio": 0.8}],
            "common_source_ips": [{"value": "10.0.0.1", "count": 70, "ratio": 0.7}],
            "common_destination_ips": [{"value": "10.0.1.1", "count": 80, "ratio": 0.8}],
            "common_source_countries": [{"value": "CN", "count": 90, "ratio": 0.9}],
            "common_source_cities": [{"value": "Shanghai", "count": 90, "ratio": 0.9}],
            "common_vpn_gateways": [{"value": "gw-1", "count": 90, "ratio": 0.9}],
            "action_distribution": {"LOGIN": 1.0},
            "event_type_distribution": {"LOGIN_SUCCESS": 1.0},
            "result_distribution": {"SUCCESS": 1.0},
            "fail_reason_distribution": {},
            "auth_method_distribution": {"password": 1.0},
            "client_software_distribution": {"OpenVPN": 1.0},
            "protocol_distribution": {"tcp": 1.0},
            "failed_rate": 0.0,
            "off_hours_rate": 0.05,
            "unusual_ip_rate": 0.02,
            "avg_daily_events": 10.0,
            "session_duration_avg": 300.0,
            "session_duration_p50": 280.0,
            "session_duration_p95": 550.0,
            "bytes_sent_avg": 1024.0,
            "bytes_recv_avg": 4096.0,
            "active_day_avg_events": 10.0,
            "max_daily_events": 18,
            "baseline_start_time": "2024-02-01 00:00:00",
            "baseline_end_time": "2024-03-01 00:00:00",
            "model_version": MODEL_VERSION,
        },
    }


def _service(repository, baseline_store, calculator=None):
    """Build the validation service with fake collaborators."""
    return UebaValidationService(
        validation_repository=repository,
        baseline_store=baseline_store,
        score_calculator=calculator or SpyScoreCalculator(),
    )


def test_dry_run_scores_target_logs_with_existing_baseline():
    """Normal dry-run should read targets, read baseline, score, and return a summary."""
    repository = FakeValidationRepository([_target()])
    baseline_store = FakeBaselineStore({"alice": _baseline()})
    calculator = SpyScoreCalculator()

    summary = _service(repository, baseline_store, calculator).dry_run(
        START_TIME,
        END_TIME,
        log_type=LOG_TYPE,
        limit=50,
        baseline_version=MODEL_VERSION,
        validated_at=VALIDATED_AT,
    )

    assert repository.fetch_calls == [
        {
            "start_time": START_TIME,
            "end_time": END_TIME,
            "log_type": LOG_TYPE,
            "limit": 50,
        }
    ]
    assert baseline_store.calls == [{"username": "alice", "model_version": MODEL_VERSION}]
    assert len(calculator.calls) == 1
    assert summary["processed_count"] == 1
    assert summary["selected_count"] == 1
    assert summary["scored_count"] == 1
    assert summary["skipped_count"] == 0
    assert summary["written_count"] == 0
    assert summary["risk_level_counts"] == {"LOW": 1}
    assert summary["validation_status_counts"] == {"VALIDATED": 1}
    assert summary["sample_results"][0]["username"] == "alice"
    assert summary["sample_results"][0]["validation_status"] == "VALIDATED"


def test_dry_run_empty_target_logs_returns_empty_summary():
    """Empty target windows should not read baselines or write anything."""
    repository = FakeValidationRepository([])
    baseline_store = FakeBaselineStore({"alice": _baseline()})
    calculator = SpyScoreCalculator()

    summary = _service(repository, baseline_store, calculator).dry_run(
        START_TIME,
        END_TIME,
        log_type=LOG_TYPE,
        limit=50,
        baseline_version=MODEL_VERSION,
    )

    assert summary["processed_count"] == 0
    assert summary["scored_count"] == 0
    assert summary["sample_results"] == []
    assert summary["risk_level_counts"] == {}
    assert summary["validation_status_counts"] == {}
    assert baseline_store.calls == []
    assert calculator.calls == []
    assert repository.save_called is False
    assert baseline_store.save_called is False


def test_dry_run_no_baseline_counts_skipped_without_crashing():
    """Missing baseline should be counted and represented in sample results."""
    repository = FakeValidationRepository([_target()])
    baseline_store = FakeBaselineStore({})
    calculator = SpyScoreCalculator()

    summary = _service(repository, baseline_store, calculator).dry_run(
        START_TIME,
        END_TIME,
        log_type=LOG_TYPE,
        limit=50,
        baseline_version=MODEL_VERSION,
        validated_at=VALIDATED_AT,
    )

    assert len(calculator.calls) == 1
    assert calculator.calls[0]["baseline"] is None
    assert summary["processed_count"] == 1
    assert summary["scored_count"] == 1
    assert summary["skipped_count"] == 1
    assert summary["no_baseline_count"] == 1
    assert summary["validation_status_counts"] == {"NO_BASELINE": 1}
    assert summary["sample_results"][0]["validation_status"] == "NO_BASELINE"


def test_dry_run_limits_sample_results_and_excludes_raw_log():
    """sample_results should stay bounded and omit source raw log text."""
    repository = FakeValidationRepository(
        [
            _target(id=1001, username="alice", request_id="req-1", raw_log="raw-a"),
            _target(id=1002, username="bob", request_id="req-2", raw_log="raw-b"),
        ]
    )
    baseline_store = FakeBaselineStore({"alice": _baseline(), "bob": _baseline(username="bob")})

    summary = _service(repository, baseline_store).dry_run(
        START_TIME,
        END_TIME,
        log_type=LOG_TYPE,
        limit=50,
        baseline_version=MODEL_VERSION,
        sample_result_limit=1,
        validated_at=VALIDATED_AT,
    )

    assert summary["processed_count"] == 2
    assert summary["scored_count"] == 2
    assert len(summary["sample_results"]) == 1
    assert "raw_log" not in summary["sample_results"][0]


def test_dry_run_never_calls_write_methods():
    """Dry-run must not call result or baseline write methods."""
    repository = FakeValidationRepository([_target()])
    baseline_store = FakeBaselineStore({"alice": _baseline_row()})

    summary = _service(repository, baseline_store).dry_run(
        START_TIME,
        END_TIME,
        log_type=LOG_TYPE,
        limit=50,
        baseline_version=MODEL_VERSION,
        validated_at=VALIDATED_AT,
    )

    assert summary["written_count"] == 0
    assert repository.save_called is False
    assert repository.source_write_called is False
    assert baseline_store.ensure_called is False
    assert baseline_store.save_called is False


def test_dry_run_preserves_fetch_parameters_and_window_semantics():
    """Service should pass start, end, log_type, and limit directly to target fetch."""
    repository = FakeValidationRepository([_target(username="bob")])
    baseline_store = FakeBaselineStore({"bob": _baseline(username="bob")})

    summary = _service(repository, baseline_store).dry_run(
        "2024-04-01 00:00:00",
        "2024-04-02 00:00:00",
        log_type="vpn-login",
        limit=7,
        baseline_version=MODEL_VERSION,
    )

    assert repository.fetch_calls == [
        {
            "start_time": "2024-04-01 00:00:00",
            "end_time": "2024-04-02 00:00:00",
            "log_type": "vpn-login",
            "limit": 7,
        }
    ]
    assert summary["start_time"] == "2024-04-01 00:00:00"
    assert summary["end_time"] == "2024-04-02 00:00:00"
    assert summary["log_type"] == "vpn-login"
    assert summary["limit"] == 7
