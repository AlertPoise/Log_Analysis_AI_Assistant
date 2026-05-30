"""Tests for the UEBA manual training update acceptance runner."""

from pathlib import Path

from tests.behavior.ueba_baseline_acceptance.config import AcceptanceConfig
from tests.behavior.ueba_baseline_acceptance import manual_training_update_runner as runner


def test_menu_items_map_to_expected_actions():
    """The interactive menu should expose the requested step-by-step actions."""
    assert list(runner.MENU_ACTIONS) == [str(index) for index in range(1, 12)]
    assert runner.MENU_ACTIONS["1"] == ("清空测试样本数据", "clear_test_samples")
    assert runner.MENU_ACTIONS["7"] == ("检查更新训练表后 baseline 是否不变", "check_baseline_unchanged")
    assert runner.MENU_ACTIONS["11"] == ("一键执行完整演示流程", "run_all")
    assert runner.RUN_ALL_CHOICES == ("1", "2", "3", "4", "5", "6", "7", "8", "9")


def test_may_and_june_windows_are_fixed_acceptance_windows():
    """May and June windows are fixed half-open acceptance test windows."""
    assert runner.MAY_START == "2026-05-01 00:00:00"
    assert runner.MAY_END == "2026-06-01 00:00:00"
    assert runner.JUNE_START == "2026-06-01 00:00:00"
    assert runner.JUNE_END == "2026-07-01 00:00:00"


def test_clear_test_samples_scopes_deletes_to_fixture_dataset_and_models(tmp_path):
    """Cleanup SQL must be scoped and must not truncate production tables."""
    config = AcceptanceConfig(output_dir=tmp_path)
    client = RecordingClient()

    result = runner.clear_test_samples(config, client)

    sql_text = "\n".join(sql for sql, _ in client.commands)
    parameters = [parameters for _, parameters in client.commands]
    assert result["success"] is True
    assert "TRUNCATE" not in sql_text.upper()
    assert "username LIKE 'fixture_user_%%'" in sql_text
    assert "log_type = %(log_type)s" in sql_text
    assert "timestamp >= %(start_time)s" in sql_text
    assert "timestamp < %(end_time)s" in sql_text
    assert "DELETE WHERE dataset_id = %(dataset_id)s" in sql_text
    assert "model_version IN (%(may_model_version)s, %(june_model_version)s)" in sql_text
    assert {"dataset_id": runner.DATASET_ID} in parameters


def test_generate_may_data_uses_only_may_window(tmp_path):
    """The May fixture helper should only expose May timestamps."""
    config = AcceptanceConfig(output_dir=tmp_path)
    rows = list(
        runner.iter_fixture_window_logs(config, start_time=runner.MAY_START, end_time=runner.MAY_END)
    )

    # 每窗口 = 10_stable + 4_multi + 3_highfail + 3_offhour + 2_longtail = 22 users * 1500
    # + 2 validation baseline users * 1500 + edge_sample_counts(5+19+20+21=65)
    expected_may = (config.stable_user_count + config.multi_location_user_count
                    + config.high_failure_user_count + config.offhour_user_count
                    + config.ip_long_tail_user_count + 2) * config.logs_per_main_user
    expected_may += sum(config.edge_user_sample_counts)
    assert len(rows) == expected_may
    assert {row["log_type"] for row in rows} == {"vpn"}
    assert {row["username"].startswith("fixture_user_") for row in rows} == {True}
    assert all(runner.MAY_START <= row["timestamp"] < runner.MAY_END for row in rows)


def test_generate_june_data_uses_only_june_window_and_keeps_variant_diff(tmp_path):
    """The June fixture helper should keep the second-month behavior variant."""
    config = AcceptanceConfig(output_dir=tmp_path)
    may_rows = list(
        runner.iter_fixture_window_logs(config, start_time=runner.MAY_START, end_time=runner.MAY_END)
    )
    june_rows = list(
        runner.iter_fixture_window_logs(config, start_time=runner.JUNE_START, end_time=runner.JUNE_END)
    )

    expected_june = (config.stable_user_count + config.multi_location_user_count
                     + config.high_failure_user_count + config.offhour_user_count
                     + config.ip_long_tail_user_count + 2) * config.logs_per_main_user
    expected_june += sum(config.edge_user_sample_counts)
    assert len(june_rows) == expected_june
    assert {row["log_type"] for row in june_rows} == {"vpn"}
    assert {row["username"].startswith("fixture_user_") for row in june_rows} == {True}
    assert all(runner.JUNE_START <= row["timestamp"] < runner.JUNE_END for row in june_rows)
    may_stable = [row for row in may_rows if row["username"] == "fixture_user_stable_0001"]
    june_stable = [row for row in june_rows if row["username"] == "fixture_user_stable_0001"]
    assert [row["src_city"] for row in may_stable] != [row["src_city"] for row in june_stable]
    assert [row["result"] for row in may_stable] != [row["result"] for row in june_stable]


def test_may_training_initialization_command_uses_initial_build(tmp_path):
    """May training initialization should be a replace update with initial_build purpose."""
    command = runner.build_may_training_update_command(AcceptanceConfig(output_dir=tmp_path))

    assert "scripts/update_ueba_baseline_training_logs.py" in command
    assert "--mode" in command and "replace" in command
    assert "--dataset-id" in command and runner.DATASET_ID in command
    assert "--baseline-purpose" in command and "initial_build" in command
    assert runner.MAY_START in command and runner.MAY_END in command


def test_june_training_update_command_uses_manual_update_and_does_not_build_baseline(tmp_path):
    """June update should update the training table only."""
    command = runner.build_june_training_update_command(AcceptanceConfig(output_dir=tmp_path))

    assert "scripts/update_ueba_baseline_training_logs.py" in command
    assert "manual_update" in command
    assert runner.JUNE_START in command and runner.JUNE_END in command
    assert "scripts/build_ueba_baseline.py" not in command
    assert "--model-version" not in command


def test_baseline_build_command_reads_training_table(tmp_path):
    """Baseline builds should read active rows from ueba_baseline_training_logs."""
    from tests.behavior.ueba_baseline_acceptance.monthly_training_update_runner import (
        build_training_baseline_command,
    )

    command = build_training_baseline_command(
        AcceptanceConfig(output_dir=tmp_path),
        start_time=runner.JUNE_START,
        end_time=runner.JUNE_END,
        model_version=runner.JUNE_MODEL_VERSION,
    )

    assert "scripts/build_ueba_baseline.py" in command
    assert "--source-table" in command
    assert "ueba_baseline_training_logs" in command
    assert "--dataset-id" in command and runner.DATASET_ID in command
    assert "--active-only" in command


def test_state_file_includes_test_window_note(tmp_path):
    """State should explicitly say May and June are only acceptance windows."""
    state = runner.build_state(AcceptanceConfig(output_dir=tmp_path))

    assert state["test_windows_note"] == runner.TEST_WINDOWS_NOTE
    assert "acceptance test windows only" in state["test_windows_note"]
    assert "production can use any approved training window" in state["test_windows_note"]


def test_default_manual_outputs_are_only_core_artifacts(tmp_path):
    """Default state/report writing should not create intermediate debug artifacts."""
    config = AcceptanceConfig(output_dir=tmp_path)
    state = runner.build_state(config, {"may_fixture_loaded": True})
    report = runner.build_report(state, [], final=False)

    runner.cleanup_manual_artifacts(tmp_path)
    from tests.behavior.ueba_baseline_acceptance.report_writer import write_json

    write_json(tmp_path / runner.STATE_FILE, state)
    write_json(tmp_path / runner.REPORT_FILE, report)
    write_json(tmp_path / runner.BASELINE_DIFF_FILE, {"changed_user_count": 0})

    files = {path.name for path in tmp_path.iterdir() if path.is_file()}
    assert files == {runner.STATE_FILE, runner.REPORT_FILE, runner.BASELINE_DIFF_FILE}
    assert not (files & set(runner.DEBUG_ARTIFACT_FILES))


def test_debug_artifacts_are_opt_in(tmp_path):
    """Intermediate artifacts should be written only when debug mode is enabled."""
    runner._write_debug_artifact(tmp_path, runner.MAY_FIXTURE_LOAD_FILE, {"success": True}, False)
    assert not (tmp_path / runner.MAY_FIXTURE_LOAD_FILE).exists()

    runner._write_debug_artifact(tmp_path, runner.MAY_FIXTURE_LOAD_FILE, {"success": True}, True)
    assert (tmp_path / runner.MAY_FIXTURE_LOAD_FILE).exists()


def test_src_behavior_and_scripts_do_not_reference_tox():
    """Acceptance .tox output references must stay out of production code and scripts."""
    roots = (Path("src/behavior"), Path("scripts"))
    matches: list[str] = []
    for root in roots:
        for path in root.rglob("*.py"):
            text = path.read_text(encoding="utf-8")
            if ".tox" in text:
                matches.append(str(path))

    assert matches == []


class RecordingClient:
    """Small ClickHouse test double that records commands."""

    def __init__(self) -> None:
        self.commands: list[tuple[str, dict | None]] = []

    def command(self, sql, parameters=None):
        self.commands.append((sql, parameters))
