"""Tests for the UEBA monthly training update acceptance runner."""

import json
import sys

from tests.behavior.ueba_baseline_acceptance.config import AcceptanceConfig
from tests.behavior.ueba_baseline_acceptance import monthly_training_update_runner as runner


def test_builds_may_and_june_training_update_commands(tmp_path):
    """Runner should construct official update CLI calls for May init and June manual update."""
    config = AcceptanceConfig(output_dir=tmp_path, clickhouse_password="secret")

    may_command = runner.build_update_training_command(
        config,
        start_time=runner.MAY_START,
        end_time=runner.MAY_END,
        baseline_purpose="initial_build",
        import_batch_id="may_initial_2026_05",
    )
    june_command = runner.build_update_training_command(
        config,
        start_time=runner.JUNE_START,
        end_time=runner.JUNE_END,
        baseline_purpose="manual_update",
        import_batch_id="june_manual_update_2026_06",
    )

    assert may_command[0] == sys.executable
    assert "scripts/update_ueba_baseline_training_logs.py" in may_command
    assert "--mode" in may_command and "replace" in may_command
    assert "--dataset-id" in may_command and runner.DATASET_ID in may_command
    assert "initial_build" in may_command
    assert "manual_update" in june_command
    assert runner.MAY_START in may_command and runner.MAY_END in may_command
    assert runner.JUNE_START in june_command and runner.JUNE_END in june_command


def test_builds_training_table_baseline_commands(tmp_path):
    """Baseline commands should read only active rows from the manual training table."""
    config = AcceptanceConfig(output_dir=tmp_path)

    command = runner.build_training_baseline_command(
        config,
        start_time=runner.JUNE_START,
        end_time=runner.JUNE_END,
        model_version=runner.JUNE_MODEL_VERSION,
    )

    assert "scripts/build_ueba_baseline.py" in command
    assert "--source-table" in command
    assert "ueba_baseline_training_logs" in command
    assert "--dataset-id" in command
    assert runner.DATASET_ID in command
    assert "--active-only" in command
    assert runner.JUNE_MODEL_VERSION in command


def test_june_manual_update_command_does_not_build_baseline(tmp_path):
    """The June manual update step should be a training-table update, not a baseline build."""
    config = AcceptanceConfig(output_dir=tmp_path)

    command = runner.build_update_training_command(
        config,
        start_time=runner.JUNE_START,
        end_time=runner.JUNE_END,
        baseline_purpose="manual_update",
        import_batch_id="june_manual_update_2026_06",
    )

    assert "scripts/update_ueba_baseline_training_logs.py" in command
    assert "scripts/build_ueba_baseline.py" not in command
    assert "--model-version" not in command


def test_baseline_fingerprint_identifies_unchanged_and_changed_snapshots():
    """Fingerprint and diff helpers should distinguish no-op training updates from rebuild changes."""
    before = _snapshot(city="北京", gateway="vpn-gw-cn-01", failed_rate=0.03)
    same = json.loads(json.dumps(before))
    changed = _snapshot(city="深圳", gateway="vpn-gw-hk-01", failed_rate=0.08)

    assert runner.baseline_fingerprint(before) == runner.baseline_fingerprint(same)
    assert runner.baseline_fingerprint(before) != runner.baseline_fingerprint(changed)
    diff = runner.diff_baseline_snapshots(before, changed)
    assert diff["changed_user_count"] == 1
    assert diff["checked_user_count"] == 1
    assert "common_source_cities" in diff["changed_fields"]
    assert "failed_rate" in diff["changed_fields"]


def test_build_report_contains_required_fields(tmp_path):
    """Final report should expose the acceptance fields used for manual review."""
    config = AcceptanceConfig(output_dir=tmp_path)
    context = {
        "fixture_stats": {
            "may": {"rows": 33065, "users": 26},
            "june": {"rows": 33065, "users": 26},
        },
        "may_training_stats": {"rows": 33065},
        "june_training_stats": {"rows": 33065},
        "baseline_before_june_update": {"row_count": 26, "total_sample_count": 33065},
        "baseline_after_june_rebuild": {"row_count": 26, "total_sample_count": 33065},
        "baseline_unchanged_after_training_update": True,
        "training_table_replaced_by_june": True,
        "baseline_changed_after_rebuild": True,
        "baseline_change_diff": {"changed_user_count": 12},
    }

    report = runner.build_report(config, context, [])

    assert report["success"] is True
    assert report["fixture_id"] == config.fixture_id
    assert report["dataset_id"] == runner.DATASET_ID
    assert report["test_windows_note"] == runner.TEST_WINDOWS_NOTE
    assert "acceptance test windows only" in report["test_windows_note"]
    assert report["may_rows"] == 33065
    assert report["june_rows"] == 33065
    assert report["may_users"] == 26
    assert report["june_users"] == 26
    assert report["baseline_unchanged_after_training_update"] is True
    assert report["training_table_replaced_by_june"] is True
    assert report["baseline_changed_after_rebuild"] is True
    assert report["changed_user_count"] == 12
    assert report["failed_checks"] == []


def test_tox_references_stay_in_acceptance_tooling():
    """The monthly runner should keep .tox output ownership under acceptance tooling."""
    assert str(AcceptanceConfig().output_dir) == ".tox/ueba_baseline_acceptance"
    for filename in (
        runner.STATE_FILE,
        runner.REPORT_FILE,
        runner.MAY_UPDATE_FILE,
        runner.JUNE_UPDATE_FILE,
        runner.BASELINE_DIFF_FILE,
    ):
        assert filename.endswith(".json")



def test_cleanup_monthly_artifacts_removes_only_runner_owned_files(tmp_path):
    """Startup cleanup should remove monthly artifacts without touching other acceptance outputs."""
    monthly_files = (runner.REPORT_FILE, runner.STATE_FILE, runner.BASELINE_DIFF_FILE, *runner.DEBUG_ARTIFACT_FILES)
    other_files = ("expected_baselines.json", "fixture_summary.json", "load_result.json", "run_state.json")
    for filename in monthly_files + other_files:
        (tmp_path / filename).write_text("{}", encoding="utf-8")

    runner.cleanup_monthly_artifacts(tmp_path)

    for filename in monthly_files:
        assert not (tmp_path / filename).exists()
    for filename in other_files:
        assert (tmp_path / filename).exists()


def test_debug_artifact_writer_only_writes_when_enabled(tmp_path):
    """Intermediate JSON files should be opt-in debug artifacts."""
    runner._write_debug_artifact(tmp_path, runner.MAY_UPDATE_FILE, {"success": True}, False)
    assert not (tmp_path / runner.MAY_UPDATE_FILE).exists()

    runner._write_debug_artifact(tmp_path, runner.MAY_UPDATE_FILE, {"success": True}, True)
    assert json.loads((tmp_path / runner.MAY_UPDATE_FILE).read_text(encoding="utf-8"))["success"] is True


def test_parse_args_accepts_debug_artifacts_flag():
    """The one-shot runner should expose an explicit debug artifact mode."""
    args = runner._parse_args(["--debug-artifacts"])

    assert args.debug_artifacts is True


def test_build_state_consolidates_intermediate_results(tmp_path):
    """State should retain enough intermediate detail when debug files are not emitted."""
    config = AcceptanceConfig(output_dir=tmp_path)
    before = _snapshot(city="北京", gateway="vpn-gw-cn-01", failed_rate=0.03)
    before.update({"fingerprint": "before_fp", "row_count": 26, "user_count": 26, "total_sample_count": 33065})
    after_update = dict(before)
    after_update["fingerprint"] = "before_fp"
    after_rebuild = _snapshot(city="深圳", gateway="vpn-gw-hk-01", failed_rate=0.08)
    after_rebuild.update({"fingerprint": "after_fp", "row_count": 26, "user_count": 26, "total_sample_count": 33065})
    context = {
        "fixture_load_result": {"success": True},
        "fixture_stats": {"may": {"rows": 33065}, "june": {"rows": 33065}},
        "may_training_update_result": {"success": True, "target_rows": 33065},
        "may_baseline_build_result": {"success": True, "payload": {"total_user_count": 26}},
        "june_training_update_result": {"success": True, "target_rows": 33065},
        "june_baseline_build_result": {"success": True, "payload": {"total_user_count": 26}},
        "baseline_before_june_update": before,
        "baseline_after_june_training_update": after_update,
        "baseline_after_june_rebuild": after_rebuild,
        "baseline_unchanged_after_training_update": True,
        "baseline_changed_after_rebuild": True,
        "training_table_replaced_by_june": True,
        "baseline_change_diff": {"changed_user_count": 26, "checked_user_count": 26},
    }
    report = {"success": True}

    state = runner.build_state(config, context, report, [], debug_artifacts=False)

    assert state["debug_artifacts"] is False
    assert state["test_windows_note"] == runner.TEST_WINDOWS_NOTE
    assert "production can use any approved training window" in state["test_windows_note"]
    assert state["may_training_update_result"]["target_rows"] == 33065
    assert state["june_baseline_build_result"]["payload"]["total_user_count"] == 26
    assert state["baseline_before_june_update"]["fingerprint"] == "before_fp"
    assert state["baseline_after_june_training_update"]["unchanged"] is True
    assert state["baseline_after_june_rebuild"]["changed"] is True
    assert state["baseline_change_diff"]["changed_user_count"] == 26


def _snapshot(city, gateway, failed_rate):
    return {
        "model_version": "test_model",
        "row_count": 1,
        "user_count": 1,
        "total_sample_count": 100,
        "users": {
            "fixture_user_stable_0001": {
                "sample_count": 100,
                "common_source_cities": city,
                "common_vpn_gateways": gateway,
                "action_distribution": "{\"LOGIN\": 1.0}",
                "result_distribution": "{\"SUCCESS\": 0.9}",
                "protocol_distribution": "{\"SSLVPN\": 1.0}",
                "failed_rate": failed_rate,
                "off_hours_rate": 0.01,
                "unusual_ip_rate": 0.02,
                "avg_daily_events": 10.0,
                "max_daily_events": 20,
                "full_baseline_json": json.dumps({"city": city, "gateway": gateway}, ensure_ascii=False),
            }
        },
    }
