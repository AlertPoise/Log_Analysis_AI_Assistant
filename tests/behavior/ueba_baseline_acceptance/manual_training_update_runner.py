"""Interactive UEBA manual training-table update acceptance runner."""

from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import replace
from pathlib import Path
from typing import Any
import argparse
import json
import time

from .clickhouse_writer import FixtureClickHouseWriter, create_clickhouse_client, validate_identifier
from .config import AcceptanceConfig
from .fixture_generator import iter_fixture_logs
from .monthly_training_update_runner import (
    BASELINE_DIFF_FILE,
    DATASET_ID,
    JUNE_END,
    JUNE_MODEL_VERSION,
    JUNE_START,
    MAY_END,
    MAY_MODEL_VERSION,
    MAY_START,
    build_training_baseline_command,
    build_update_training_command,
    diff_baseline_snapshots,
    fetch_baseline_snapshot,
    fetch_log_window_stats,
    fetch_training_table_stats,
    replace_training_table_from_fixture_logs,
    run_json_command,
)
from .report_writer import ensure_output_dir, read_json, update_run_state, write_json
from src.behavior.baseline_store import BaselineStore
from src.behavior.training_log_store import TrainingLogStore


STATE_FILE = "manual_training_update_state.json"
REPORT_FILE = "manual_training_update_report.json"
MAY_FIXTURE_LOAD_FILE = "manual_may_fixture_load_result.json"
JUNE_FIXTURE_LOAD_FILE = "manual_june_fixture_load_result.json"
MAY_TRAINING_UPDATE_FILE = "manual_may_training_update_result.json"
JUNE_TRAINING_UPDATE_FILE = "manual_june_training_update_result.json"
MAY_BASELINE_BUILD_FILE = "manual_may_baseline_build_result.json"
JUNE_BASELINE_BUILD_FILE = "manual_june_baseline_build_result.json"
BASELINE_AFTER_UPDATE_FILE = "manual_baseline_after_training_update.json"

TEST_WINDOWS_NOTE = (
    "2026-05 and 2026-06 are acceptance test windows only; "
    "production can use any approved training window."
)
MENU_TITLE = "UEBA 训练表手动更新验收工具"
MENU_ACTIONS: dict[str, tuple[str, str]] = {
    "1": ("清空测试样本数据", "clear_test_samples"),
    "2": ("生成 5 月测试数据", "generate_may_fixture"),
    "3": ("用 5 月数据初始化训练表", "initialize_may_training_table"),
    "4": ("用训练表构建 5 月 baseline", "build_may_baseline"),
    "5": ("生成 6 月测试数据", "generate_june_fixture"),
    "6": ("用 6 月数据更新训练表", "update_june_training_table"),
    "7": ("检查更新训练表后 baseline 是否不变", "check_baseline_unchanged"),
    "8": ("用训练表构建 6 月 baseline", "build_june_baseline"),
    "9": ("比较 5 月和 6 月 baseline 是否变化", "compare_baseline_changes"),
    "10": ("查看当前状态", "print_current_status"),
    "11": ("一键执行完整演示流程", "run_all"),
}
RUN_ALL_CHOICES = ("1", "2", "3", "4", "5", "6", "7", "8", "9")
DEBUG_ARTIFACT_FILES = (
    MAY_FIXTURE_LOAD_FILE,
    JUNE_FIXTURE_LOAD_FILE,
    MAY_TRAINING_UPDATE_FILE,
    JUNE_TRAINING_UPDATE_FILE,
    MAY_BASELINE_BUILD_FILE,
    JUNE_BASELINE_BUILD_FILE,
    BASELINE_AFTER_UPDATE_FILE,
)
MANUAL_ARTIFACT_FILES = (
    STATE_FILE,
    REPORT_FILE,
    BASELINE_DIFF_FILE,
    *DEBUG_ARTIFACT_FILES,
)
FINAL_SUCCESS_FIELDS = (
    "may_fixture_loaded",
    "june_fixture_loaded",
    "may_training_initialized",
    "may_baseline_built",
    "june_training_updated",
    "baseline_unchanged_after_training_update",
    "june_baseline_built",
    "baseline_changed_after_rebuild",
)


class ManualTrainingUpdateRunner:
    """Step-by-step acceptance runner for manual UEBA training-table updates."""

    def __init__(
        self,
        config: AcceptanceConfig | None = None,
        *,
        client_factory: Callable[[AcceptanceConfig], Any] = create_clickhouse_client,
        command_runner: Callable[[list[str], str], dict[str, Any]] | None = None,
        debug_artifacts: bool = False,
    ) -> None:
        self.config = config or AcceptanceConfig()
        self.client_factory = client_factory
        self.command_runner = command_runner or run_json_command
        self.debug_artifacts = debug_artifacts
        ensure_output_dir(self.config)
        cleanup_manual_debug_artifacts(Path(self.config.output_dir))

    def clear_test_samples(self) -> dict[str, Any]:
        """Clear only fixture users, the acceptance dataset, and test model versions."""
        cleanup_manual_artifacts(ensure_output_dir(self.config))
        client = None
        try:
            client = self.client_factory(self.config)
            result = clear_test_samples(self.config, client)
            state = _default_state(self.config)
            state["last_action"] = "clear_test_samples"
            state["clear_test_samples_done"] = bool(result["success"])
            state["cleanup_result"] = result
            return _finish(self.config, state, [], final=False)
        except Exception as exc:
            state = _read_state(self.config)
            state["last_action"] = "clear_test_samples"
            return _finish(self.config, state, [f"clear_test_samples:{type(exc).__name__}:{exc}"], final=False)
        finally:
            _close_client(client)

    def generate_may_fixture(self) -> dict[str, Any]:
        """Generate and load only the May fixture window into logs_structured."""
        return self._load_fixture_window(
            start_time=MAY_START,
            end_time=MAY_END,
            state_key="may_fixture_loaded",
            stage="generate_may_fixture",
            debug_filename=MAY_FIXTURE_LOAD_FILE,
        )

    def generate_june_fixture(self) -> dict[str, Any]:
        """Generate and load only the June fixture window into logs_structured."""
        return self._load_fixture_window(
            start_time=JUNE_START,
            end_time=JUNE_END,
            state_key="june_fixture_loaded",
            stage="generate_june_fixture",
            debug_filename=JUNE_FIXTURE_LOAD_FILE,
        )

    def initialize_may_training_table(self) -> dict[str, Any]:
        """Replace the acceptance training dataset with May fixture data."""
        return self._replace_training_table(
            start_time=MAY_START,
            end_time=MAY_END,
            baseline_purpose="initial_build",
            import_batch_id="may_initial_2026_05",
            stage="may_training_update",
            state_key="may_training_initialized",
            debug_filename=MAY_TRAINING_UPDATE_FILE,
        )

    def update_june_training_table(self) -> dict[str, Any]:
        """Replace the acceptance training dataset with June fixture data only."""
        return self._replace_training_table(
            start_time=JUNE_START,
            end_time=JUNE_END,
            baseline_purpose="manual_update",
            import_batch_id="june_manual_update_2026_06",
            stage="june_training_update",
            state_key="june_training_updated",
            debug_filename=JUNE_TRAINING_UPDATE_FILE,
        )

    def build_may_baseline(self) -> dict[str, Any]:
        """Build the May baseline from the acceptance training table."""
        return self._build_baseline(
            start_time=MAY_START,
            end_time=MAY_END,
            model_version=MAY_MODEL_VERSION,
            stage="may_baseline_build",
            state_key="may_baseline_built",
            debug_filename=MAY_BASELINE_BUILD_FILE,
        )

    def build_june_baseline(self) -> dict[str, Any]:
        """Build the June baseline from the acceptance training table."""
        return self._build_baseline(
            start_time=JUNE_START,
            end_time=JUNE_END,
            model_version=JUNE_MODEL_VERSION,
            stage="june_baseline_build",
            state_key="june_baseline_built",
            debug_filename=JUNE_BASELINE_BUILD_FILE,
        )

    def check_baseline_unchanged(self) -> dict[str, Any]:
        """Verify that replacing the training table did not mutate the May baseline."""
        failed_checks: list[str] = []
        client = None
        try:
            client = self.client_factory(self.config)
            state = _read_state(self.config)
            snapshot = fetch_baseline_snapshot(client, self.config, MAY_MODEL_VERSION)
            previous_fingerprint = state.get("may_baseline_fingerprint")
            unchanged = bool(previous_fingerprint) and previous_fingerprint == snapshot.get("fingerprint")
            if not unchanged:
                failed_checks.append("baseline_unchanged_after_training_update")
            state["last_action"] = "check_baseline_unchanged"
            state["baseline_unchanged_after_training_update"] = unchanged
            state["baseline_after_training_update"] = _snapshot_summary(snapshot)
            _write_debug_artifact(
                ensure_output_dir(self.config),
                BASELINE_AFTER_UPDATE_FILE,
                snapshot,
                self.debug_artifacts,
            )
            _refresh_state_counts(state, self.config, client)
            return _finish(self.config, state, failed_checks, final=False)
        except Exception as exc:
            state = _read_state(self.config)
            state["last_action"] = "check_baseline_unchanged"
            return _finish(
                self.config,
                state,
                [f"check_baseline_unchanged:{type(exc).__name__}:{exc}"],
                final=False,
            )
        finally:
            _close_client(client)

    def compare_baseline_changes(self) -> dict[str, Any]:
        """Compare May and June baselines and write baseline_change_diff.json."""
        failed_checks: list[str] = []
        client = None
        try:
            client = self.client_factory(self.config)
            state = _read_state(self.config)
            may = fetch_baseline_snapshot(client, self.config, MAY_MODEL_VERSION)
            june = fetch_baseline_snapshot(client, self.config, JUNE_MODEL_VERSION)
            _require_baseline_snapshot(may, MAY_MODEL_VERSION, "may_baseline", failed_checks, self.config.expected_user_count)
            _require_baseline_snapshot(june, JUNE_MODEL_VERSION, "june_baseline", failed_checks, self.config.expected_user_count)
            diff = diff_baseline_snapshots(may, june)
            write_json(ensure_output_dir(self.config) / BASELINE_DIFF_FILE, diff)
            changed = int(diff.get("changed_user_count") or 0) > 0
            if not changed:
                failed_checks.append("baseline_changed_after_rebuild")
            state["last_action"] = "compare_baseline_changes"
            state["baseline_changed_after_rebuild"] = changed
            state["changed_user_count"] = int(diff.get("changed_user_count") or 0)
            state["baseline_change_diff"] = diff
            state["may_baseline_fingerprint"] = may.get("fingerprint")
            state["june_baseline_fingerprint"] = june.get("fingerprint")
            _refresh_state_counts(state, self.config, client)
            return _finish(self.config, state, failed_checks, final=True)
        except Exception as exc:
            state = _read_state(self.config)
            state["last_action"] = "compare_baseline_changes"
            return _finish(
                self.config,
                state,
                [f"compare_baseline_changes:{type(exc).__name__}:{exc}"],
                final=True,
            )
        finally:
            _close_client(client)

    def print_current_status(self) -> dict[str, Any]:
        """Refresh state from ClickHouse and print a compact status summary."""
        client = None
        try:
            client = self.client_factory(self.config)
            state = _read_state(self.config)
            state["last_action"] = "print_current_status"
            _refresh_state_counts(state, self.config, client)
            report = _finish(self.config, state, [], final=False)
            _print_status(state, self.config)
            return report
        except Exception as exc:
            state = _read_state(self.config)
            state["last_action"] = "print_current_status"
            return _finish(self.config, state, [f"print_current_status:{type(exc).__name__}:{exc}"], final=False)
        finally:
            _close_client(client)

    def run_all(self) -> dict[str, Any]:
        """Run the complete manual update demonstration flow."""
        cleanup_manual_artifacts(ensure_output_dir(self.config))
        report: dict[str, Any] = {}
        for choice in RUN_ALL_CHOICES:
            print(f"\n执行：{MENU_ACTIONS[choice][0]}")
            report = getattr(self, MENU_ACTIONS[choice][1])()
            if report.get("failed_checks"):
                return report
        return report

    def _load_fixture_window(
        self,
        *,
        start_time: str,
        end_time: str,
        state_key: str,
        stage: str,
        debug_filename: str,
    ) -> dict[str, Any]:
        failed_checks: list[str] = []
        client = None
        try:
            client = self.client_factory(self.config)
            result = load_fixture_window(self.config, client, start_time=start_time, end_time=end_time, stage=stage)
            if result.get("success") is not True:
                failed_checks.append(f"{stage}_success")
            state = _read_state(self.config)
            state["last_action"] = stage
            state[state_key] = bool(result.get("success"))
            _write_debug_artifact(ensure_output_dir(self.config), debug_filename, result, self.debug_artifacts)
            _refresh_state_counts(state, self.config, client)
            return _finish(self.config, state, failed_checks, final=False)
        except Exception as exc:
            state = _read_state(self.config)
            state["last_action"] = stage
            return _finish(self.config, state, [f"{stage}:{type(exc).__name__}:{exc}"], final=False)
        finally:
            _close_client(client)

    def _replace_training_table(
        self,
        *,
        start_time: str,
        end_time: str,
        baseline_purpose: str,
        import_batch_id: str,
        stage: str,
        state_key: str,
        debug_filename: str,
    ) -> dict[str, Any]:
        failed_checks: list[str] = []
        client = None
        try:
            client = self.client_factory(self.config)
            result = replace_training_table_from_fixture_logs(
                client,
                self.config,
                start_time=start_time,
                end_time=end_time,
                baseline_purpose=baseline_purpose,
                import_batch_id=import_batch_id,
                stage=stage,
            )
            if result.get("success") is not True:
                failed_checks.append(f"{stage}_success")
            state = _read_state(self.config)
            state["last_action"] = stage
            state[state_key] = bool(result.get("success"))
            if state_key == "may_training_initialized":
                state["may_training_rows"] = int(result.get("target_rows") or 0)
            if state_key == "june_training_updated":
                state["june_training_rows"] = int(result.get("target_rows") or 0)
            if baseline_purpose == "manual_update":
                state["june_update_did_not_build_baseline"] = True
            _write_debug_artifact(ensure_output_dir(self.config), debug_filename, result, self.debug_artifacts)
            _refresh_state_counts(state, self.config, client)
            return _finish(self.config, state, failed_checks, final=False)
        except Exception as exc:
            state = _read_state(self.config)
            state["last_action"] = stage
            return _finish(self.config, state, [f"{stage}:{type(exc).__name__}:{exc}"], final=False)
        finally:
            _close_client(client)

    def _build_baseline(
        self,
        *,
        start_time: str,
        end_time: str,
        model_version: str,
        stage: str,
        state_key: str,
        debug_filename: str,
    ) -> dict[str, Any]:
        failed_checks: list[str] = []
        client = None
        try:
            client = self.client_factory(self.config)
            command = build_training_baseline_command(
                self.config,
                start_time=start_time,
                end_time=end_time,
                model_version=model_version,
            )
            result = self.command_runner(command, stage)
            if result.get("success") is not True:
                failed_checks.append(f"{stage}_success")
            snapshot = fetch_baseline_snapshot(client, self.config, model_version)
            _require_baseline_snapshot(snapshot, model_version, stage, failed_checks, self.config.expected_user_count)
            state = _read_state(self.config)
            state["last_action"] = stage
            state[state_key] = not failed_checks
            if model_version == MAY_MODEL_VERSION:
                state["may_baseline_fingerprint"] = snapshot.get("fingerprint")
            else:
                state["june_baseline_fingerprint"] = snapshot.get("fingerprint")
            _write_debug_artifact(ensure_output_dir(self.config), debug_filename, result, self.debug_artifacts)
            _refresh_state_counts(state, self.config, client)
            return _finish(self.config, state, failed_checks, final=False)
        except Exception as exc:
            state = _read_state(self.config)
            state["last_action"] = stage
            return _finish(self.config, state, [f"{stage}:{type(exc).__name__}:{exc}"], final=False)
        finally:
            _close_client(client)


def build_may_training_update_command(config: AcceptanceConfig) -> list[str]:
    """Return the official May training-table initialization command."""
    return build_update_training_command(
        config,
        start_time=MAY_START,
        end_time=MAY_END,
        baseline_purpose="initial_build",
        import_batch_id="may_initial_2026_05",
    )


def build_june_training_update_command(config: AcceptanceConfig) -> list[str]:
    """Return the official June manual training-table update command."""
    return build_update_training_command(
        config,
        start_time=JUNE_START,
        end_time=JUNE_END,
        baseline_purpose="manual_update",
        import_batch_id="june_manual_update_2026_06",
    )


def fixture_window_config(config: AcceptanceConfig, start_time: str, end_time: str) -> AcceptanceConfig:
    """Return a config scoped to one visible test window."""
    return replace(config, start_time=start_time, end_time=end_time)


def iter_fixture_window_logs(
    config: AcceptanceConfig,
    *,
    start_time: str,
    end_time: str,
) -> Iterable[dict[str, Any]]:
    """Yield only one acceptance window while preserving May/June fixture variants."""
    full_config = replace(config, start_time=MAY_START, end_time=JUNE_END)
    for row in iter_fixture_logs(full_config):
        timestamp = str(row["timestamp"])
        if start_time <= timestamp < end_time:
            yield row


def load_fixture_window(
    config: AcceptanceConfig,
    client: Any,
    *,
    start_time: str,
    end_time: str,
    stage: str,
) -> dict[str, Any]:
    """Load one fixture window into logs_structured without touching other windows."""
    begin = time.time()
    window_config = fixture_window_config(config, start_time, end_time)
    writer = FixtureClickHouseWriter(
        client=client,
        database=config.clickhouse_database,
        batch_size=config.clickhouse_batch_size,
    )
    writer.clean_fixture_logs(window_config)
    expected_rows = sum(1 for _ in iter_fixture_window_logs(config, start_time=start_time, end_time=end_time))
    inserted_rows = writer.insert_logs(iter_fixture_window_logs(config, start_time=start_time, end_time=end_time))
    stats = fetch_log_window_stats(client, config, start_time, end_time)
    database_rows = int(stats.get("rows") or 0)
    database_users = int(stats.get("users") or 0)
    success = inserted_rows == expected_rows == database_rows and database_users == config.expected_user_count
    return {
        "success": success,
        "stage": stage,
        "fixture_id": config.fixture_id,
        "expected_rows": expected_rows,
        "inserted_rows": inserted_rows,
        "database_rows": database_rows,
        "database_users": database_users,
        "start_time": start_time,
        "end_time": end_time,
        "log_type": config.log_type,
        "source_filter": "username IN (configured fixture usernames)",
        "duration_seconds": round(time.time() - begin, 3),
        "error": None if success else "fixture window row or user count mismatch",
    }


def clear_test_samples(config: AcceptanceConfig, client: Any) -> dict[str, Any]:
    """Clear only acceptance-scoped fixture logs, training rows, and baselines."""
    begin = time.time()
    full_window_config = fixture_window_config(config, MAY_START, JUNE_END)
    FixtureClickHouseWriter(
        client=client,
        database=config.clickhouse_database,
        batch_size=config.clickhouse_batch_size,
    ).clean_fixture_logs(full_window_config)
    TrainingLogStore(client=client, database=config.clickhouse_database).ensure_table()
    TrainingLogStore(client=client, database=config.clickhouse_database).delete_dataset(DATASET_ID)
    BaselineStore(client=client, database=config.clickhouse_database).ensure_table()
    database = validate_identifier(config.clickhouse_database)
    users = config.fixture_usernames
    placeholders = ", ".join(f"%(cu{i})s" for i in range(len(users)))
    sql = f"""
    ALTER TABLE {database}.user_behavior_baselines
    DELETE
    WHERE username IN ({placeholders})
      AND model_version IN (%(may_model_version)s, %(june_model_version)s)
    SETTINGS mutations_sync = 1
    """
    params = {"may_model_version": MAY_MODEL_VERSION, "june_model_version": JUNE_MODEL_VERSION}
    for i, u in enumerate(users):
        params[f"cu{i}"] = u
    _execute_command(client, sql, params)
    return {
        "success": True,
        "dataset_id": DATASET_ID,
        "log_filter": {
            "username": "exact fixture usernames via config",
            "log_type": config.log_type,
            "start_time": MAY_START,
            "end_time": JUNE_END,
        },
        "model_versions": {"may": MAY_MODEL_VERSION, "june": JUNE_MODEL_VERSION},
        "duration_seconds": round(time.time() - begin, 3),
    }


def cleanup_manual_artifacts(output_dir: Path) -> None:
    """Remove only files owned by this manual runner."""
    for filename in MANUAL_ARTIFACT_FILES:
        path = output_dir / filename
        if path.is_file():
            path.unlink()


def cleanup_manual_debug_artifacts(output_dir: Path) -> None:
    """Remove stale debug artifacts while preserving report/state/diff."""
    for filename in DEBUG_ARTIFACT_FILES:
        path = output_dir / filename
        if path.is_file():
            path.unlink()


def build_state(config: AcceptanceConfig, existing: dict[str, Any] | None = None) -> dict[str, Any]:
    """Build a state object with all required manual acceptance fields."""
    state = _default_state(config)
    if existing:
        state.update(existing)
    state["test_windows_note"] = TEST_WINDOWS_NOTE
    return state


def build_report(state: dict[str, Any], failed_checks: list[str], *, final: bool) -> dict[str, Any]:
    """Build the compact manual acceptance report."""
    report = {
        "success": len(failed_checks) == 0 and (not final or all(bool(state.get(key)) for key in FINAL_SUCCESS_FIELDS)),
        "fixture_id": state.get("fixture_id"),
        "dataset_id": state.get("dataset_id"),
        "test_windows_note": TEST_WINDOWS_NOTE,
        "model_versions": state.get("model_versions"),
        "may_rows": int(state.get("may_rows") or 0),
        "june_rows": int(state.get("june_rows") or 0),
        "may_users": int(state.get("may_users") or 0),
        "june_users": int(state.get("june_users") or 0),
        "may_training_rows": int(state.get("may_training_rows") or 0),
        "june_training_rows": int(state.get("june_training_rows") or 0),
        "may_baseline_rows": int(state.get("may_baseline_rows") or 0),
        "june_baseline_rows": int(state.get("june_baseline_rows") or 0),
        "may_baseline_total_sample_count": int(state.get("may_baseline_total_sample_count") or 0),
        "june_baseline_total_sample_count": int(state.get("june_baseline_total_sample_count") or 0),
        "baseline_unchanged_after_training_update": bool(
            state.get("baseline_unchanged_after_training_update")
        ),
        "baseline_changed_after_rebuild": bool(state.get("baseline_changed_after_rebuild")),
        "changed_user_count": int(state.get("changed_user_count") or 0),
        "failed_checks": failed_checks,
    }
    if failed_checks:
        report["message"] = "manual training update acceptance failed"
    elif report["success"]:
        report["message"] = "manual training update acceptance passed"
    else:
        report["message"] = "manual training update acceptance state updated"
    return report


def main(argv: list[str] | None = None) -> int:
    """Run the interactive manual training update acceptance tool."""
    args = _parse_args(argv)
    config = AcceptanceConfig()
    if args.output_dir:
        config.output_dir = Path(args.output_dir)
    runner = ManualTrainingUpdateRunner(config, debug_artifacts=args.debug_artifacts)
    if args.run_all:
        report = runner.run_all()
        print(json.dumps(report, ensure_ascii=False, sort_keys=True, default=str))
        return 0 if report.get("success") is True else 1

    while True:
        _print_menu(config)
        choice = input("请选择操作：").strip()
        if choice == "0":
            print("退出。")
            return 0
        action = MENU_ACTIONS.get(choice)
        if action is None:
            print("\n无效选择，请重新输入。\n")
            continue
        report = getattr(runner, action[1])()
        _print_action_result(report, config)


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="UEBA 训练表手动更新交互式验收工具")
    parser.add_argument("--output-dir", help="覆盖默认输出目录，默认 .tox/ueba_baseline_acceptance")
    parser.add_argument("--debug-artifacts", action="store_true", help="写出中间调试 JSON 产物")
    parser.add_argument("--run-all", action="store_true", help="非交互执行完整演示流程")
    return parser.parse_args(argv)


def _default_state(config: AcceptanceConfig) -> dict[str, Any]:
    return {
        "fixture_id": config.fixture_id,
        "dataset_id": DATASET_ID,
        "test_windows_note": TEST_WINDOWS_NOTE,
        "model_versions": {"may": MAY_MODEL_VERSION, "june": JUNE_MODEL_VERSION},
        "may_fixture_loaded": False,
        "june_fixture_loaded": False,
        "may_training_initialized": False,
        "may_baseline_built": False,
        "june_training_updated": False,
        "baseline_unchanged_after_training_update": False,
        "june_baseline_built": False,
        "baseline_changed_after_rebuild": False,
        "may_rows": 0,
        "june_rows": 0,
        "may_users": 0,
        "june_users": 0,
        "may_training_rows": 0,
        "june_training_rows": 0,
        "may_baseline_rows": 0,
        "june_baseline_rows": 0,
        "may_baseline_total_sample_count": 0,
        "june_baseline_total_sample_count": 0,
        "changed_user_count": 0,
        "manual_training_update_state_path": str(Path(config.output_dir) / STATE_FILE),
        "manual_training_update_report_path": str(Path(config.output_dir) / REPORT_FILE),
        "baseline_change_diff_path": str(Path(config.output_dir) / BASELINE_DIFF_FILE),
    }


def _read_state(config: AcceptanceConfig) -> dict[str, Any]:
    state_path = ensure_output_dir(config) / STATE_FILE
    existing = read_json(state_path) if state_path.exists() else {}
    return build_state(config, existing)


def _finish(
    config: AcceptanceConfig,
    state: dict[str, Any],
    failed_checks: list[str],
    *,
    final: bool,
) -> dict[str, Any]:
    state = build_state(config, state)
    state["failed_checks"] = failed_checks
    report = build_report(state, failed_checks, final=final)
    output_dir = ensure_output_dir(config)
    write_json(output_dir / STATE_FILE, state)
    write_json(output_dir / REPORT_FILE, report)
    update_run_state(
        config,
        manual_training_update_done=bool(report["success"]),
        manual_training_update_report_path=str(output_dir / REPORT_FILE),
        manual_training_update_state_path=str(output_dir / STATE_FILE),
        last_error=None if not failed_checks else "; ".join(failed_checks),
    )
    return report


def _refresh_state_counts(state: dict[str, Any], config: AcceptanceConfig, client: Any) -> None:
    may_logs = fetch_log_window_stats(client, config, MAY_START, MAY_END)
    june_logs = fetch_log_window_stats(client, config, JUNE_START, JUNE_END)
    training_current = fetch_current_training_dataset_stats(client, config)
    may_baseline = fetch_baseline_snapshot(client, config, MAY_MODEL_VERSION)
    june_baseline = fetch_baseline_snapshot(client, config, JUNE_MODEL_VERSION)
    state.update(
        {
            "may_rows": int(may_logs.get("rows") or 0),
            "june_rows": int(june_logs.get("rows") or 0),
            "may_users": int(may_logs.get("users") or 0),
            "june_users": int(june_logs.get("users") or 0),
            "training_table_rows": int(training_current.get("rows") or 0),
            "training_table_users": int(training_current.get("users") or 0),
            "training_table_min_time": training_current.get("min_timestamp"),
            "training_table_max_time": training_current.get("max_timestamp"),
            "may_baseline_rows": int(may_baseline.get("row_count") or 0),
            "june_baseline_rows": int(june_baseline.get("row_count") or 0),
            "may_baseline_total_sample_count": int(may_baseline.get("total_sample_count") or 0),
            "june_baseline_total_sample_count": int(june_baseline.get("total_sample_count") or 0),
            "may_baseline_fingerprint": may_baseline.get("fingerprint") or state.get("may_baseline_fingerprint"),
            "june_baseline_fingerprint": june_baseline.get("fingerprint") or state.get("june_baseline_fingerprint"),
        }
    )


def fetch_current_training_dataset_stats(client: Any, config: AcceptanceConfig) -> dict[str, Any]:
    """Fetch current rows/users/min/max for the acceptance training dataset."""
    database = validate_identifier(config.clickhouse_database)
    sql = f"""
    SELECT
        count() AS rows,
        uniqExact(username) AS users,
        min(timestamp) AS min_timestamp,
        max(timestamp) AS max_timestamp
    FROM {database}.ueba_baseline_training_logs
    WHERE dataset_id = %(dataset_id)s
      AND log_type = %(log_type)s
    """
    rows = _query_rows(client, sql, {"dataset_id": DATASET_ID, "log_type": config.log_type})
    row = rows[0] if rows else {}
    return {
        "rows": int(row.get("rows") or 0),
        "users": int(row.get("users") or 0),
        "min_timestamp": _time_string(row.get("min_timestamp")),
        "max_timestamp": _time_string(row.get("max_timestamp")),
    }


def _require_baseline_snapshot(
    snapshot: dict[str, Any],
    model_version: str,
    label: str,
    failed_checks: list[str],
    expected_user_count: int,
) -> None:
    if snapshot.get("model_version") != model_version:
        failed_checks.append(f"{label}_model_version")
    if int(snapshot.get("row_count") or 0) != expected_user_count:
        failed_checks.append(f"{label}_rows_expected")
    if int(snapshot.get("user_count") or 0) != expected_user_count:
        failed_checks.append(f"{label}_users_expected")
    if int(snapshot.get("total_sample_count") or 0) < 30000:
        failed_checks.append(f"{label}_samples_ge_30000")


def _snapshot_summary(snapshot: dict[str, Any]) -> dict[str, Any]:
    return {
        "model_version": snapshot.get("model_version"),
        "fingerprint": snapshot.get("fingerprint"),
        "rows": int(snapshot.get("row_count") or 0),
        "users": int(snapshot.get("user_count") or 0),
        "total_sample_count": int(snapshot.get("total_sample_count") or 0),
    }


def _write_debug_artifact(output_dir: Path, filename: str, payload: Any, debug_artifacts: bool) -> None:
    if debug_artifacts:
        write_json(output_dir / filename, payload)


def _execute_command(client: Any, sql: str, parameters: dict[str, Any]) -> None:
    if hasattr(client, "command"):
        client.command(sql, parameters=parameters)
        return
    if hasattr(client, "execute"):
        client.execute(sql, parameters)
        return
    raise TypeError("client must provide command(...) or execute(...)")


def _query_rows(client: Any, sql: str, parameters: dict[str, Any]) -> list[dict[str, Any]]:
    if hasattr(client, "query"):
        result = client.query(sql, parameters=parameters)
    elif hasattr(client, "execute"):
        result = client.execute(sql, parameters)
    else:
        raise TypeError("client must provide query(...) or execute(...)")
    if hasattr(result, "named_results"):
        rows = result.named_results() if callable(result.named_results) else result.named_results
        return [dict(row) for row in rows]
    if hasattr(result, "result_rows") and hasattr(result, "column_names"):
        return [dict(zip(result.column_names, row)) for row in result.result_rows]
    if isinstance(result, list):
        return [dict(row) for row in result]
    raise TypeError("unsupported query result format")


def _time_string(value: Any) -> str | None:
    if value in (None, ""):
        return None
    text = str(value)
    if "." in text:
        text = text.split(".", 1)[0]
    return text.replace("T", " ")[:19]


def _close_client(client: Any) -> None:
    if hasattr(client, "close"):
        try:
            client.close()
        except Exception:
            pass


def _print_menu(config: AcceptanceConfig) -> None:
    print(MENU_TITLE)
    print()
    print("说明：")
    print("- 5 月 / 6 月只是测试数据窗口，用于演示初始化和手动更新流程。")
    print("- 正式使用时不要求每个月都跑。")
    print("- 本工具只操作精确 fixture 用户集合和指定 dataset_id，不清理真实业务数据。")
    print(f"- 输出目录：{config.output_dir}")
    print()
    for key, (label, _) in MENU_ACTIONS.items():
        print(f"{key}. {label}")
    print("0. 退出")
    print()


def _print_action_result(report: dict[str, Any], config: AcceptanceConfig) -> None:
    print()
    print(f"success = {report.get('success')}")
    print(f"message = {report.get('message')}")
    print(f"failed_checks = {report.get('failed_checks', [])}")
    print(f"report_path = {Path(config.output_dir) / REPORT_FILE}")
    print()


def _print_status(state: dict[str, Any], config: AcceptanceConfig) -> None:
    print("\n当前状态：")
    print(f"may_logs = {state.get('may_rows')} rows / {state.get('may_users')} users")
    print(f"june_logs = {state.get('june_rows')} rows / {state.get('june_users')} users")
    print(
        "training_table = {} rows / {} users / {} ~ {}".format(
            state.get("training_table_rows"),
            state.get("training_table_users"),
            state.get("training_table_min_time"),
            state.get("training_table_max_time"),
        )
    )
    print(
        "may_baseline = {} rows / samples {}".format(
            state.get("may_baseline_rows"),
            state.get("may_baseline_total_sample_count"),
        )
    )
    print(
        "june_baseline = {} rows / samples {}".format(
            state.get("june_baseline_rows"),
            state.get("june_baseline_total_sample_count"),
        )
    )
    print(
        "baseline_unchanged_after_training_update = {}".format(
            state.get("baseline_unchanged_after_training_update")
        )
    )
    print(f"baseline_changed_after_rebuild = {state.get('baseline_changed_after_rebuild')}")
    print(f"state_path = {Path(config.output_dir) / STATE_FILE}\n")


__all__ = [
    "BASELINE_DIFF_FILE",
    "DATASET_ID",
    "DEBUG_ARTIFACT_FILES",
    "FINAL_SUCCESS_FIELDS",
    "JUNE_END",
    "JUNE_MODEL_VERSION",
    "JUNE_START",
    "MANUAL_ARTIFACT_FILES",
    "MAY_END",
    "MAY_MODEL_VERSION",
    "MAY_START",
    "MENU_ACTIONS",
    "REPORT_FILE",
    "RUN_ALL_CHOICES",
    "STATE_FILE",
    "TEST_WINDOWS_NOTE",
    "ManualTrainingUpdateRunner",
    "build_june_training_update_command",
    "build_may_training_update_command",
    "build_report",
    "build_state",
    "cleanup_manual_artifacts",
    "clear_test_samples",
    "fetch_current_training_dataset_stats",
    "fixture_window_config",
    "iter_fixture_window_logs",
    "load_fixture_window",
    "main",
]


if __name__ == "__main__":
    raise SystemExit(main())
