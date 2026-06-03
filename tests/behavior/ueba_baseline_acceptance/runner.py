"""Interactive runner for UEBA baseline acceptance fixtures."""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any

from .baseline_builder_runner import run_baseline_build
from .baseline_validator import validate_fixture_baselines
from .clickhouse_writer import load_fixture_to_clickhouse
from .config import AcceptanceConfig
from .fixture_generator import generate_fixture_outputs
from .monthly_training_update_runner import run_monthly_training_update
from .report_writer import ensure_output_dir, read_json


EXPECTED_FILE = "expected_baselines.json"
SUMMARY_FILE = "fixture_summary.json"
RUN_STATE_FILE = "run_state.json"
LOAD_RESULT_FILE = "load_result.json"
BUILD_RESULT_FILE = "build_result.json"
VALIDATION_REPORT_FILE = "validation_report.json"
FAILED_DIFF_FILE = "failed_diff.json"


def main(argv: list[str] | None = None) -> int:
    """Run the interactive acceptance tool."""
    args = _parse_args(argv)
    config = AcceptanceConfig(dump_logs_jsonl=args.dump_logs_jsonl)
    if args.output_dir:
        config.output_dir = Path(args.output_dir)

    if args.monthly_training_update:
        result = run_monthly_training_update(config)
        print("success = {}".format(result["success"]))
        print("monthly_training_update_report_path = {}".format(Path(config.output_dir) / "monthly_training_update_report.json"))
        if not result["success"]:
            print("failed_checks = {}".format(result.get("failed_checks", [])))
        return 0 if result["success"] else 1

    action_flags = [
        args.generate_expected,
        args.load_fixture,
        args.build_baseline,
        args.validate_baselines,
        args.print_summary,
    ]
    action_count = sum(1 for f in action_flags if f)
    if action_count > 1:
        print("错误：一次只能指定一个非交互动作参数。同时指定多个参数会导致歧义，已拒绝执行。", file=__import__("sys").stderr)
        return 2
    if action_count == 1:
        return _run_non_interactive(args, config)

    while True:
        _print_menu(config)
        choice = input("请选择操作：").strip()
        if choice == "1":
            _handle_generate_expected(config)
        elif choice == "2":
            _handle_load_fixture(config)
        elif choice == "3":
            _handle_build_baseline(config)
        elif choice == "4":
            _handle_validate_baselines(config)
        elif choice == "5":
            _print_recent_summary(config)
        elif choice == "0":
            print("退出。")
            return 0
        else:
            print("\n无效选择，请重新输入。\n")


def _run_non_interactive(args: argparse.Namespace, config: AcceptanceConfig) -> int:
    """Execute a single non-interactive action and exit."""
    if args.generate_expected:
        return _handle_generate_expected(config)
    if args.load_fixture:
        return _handle_load_fixture(config)
    if args.build_baseline:
        return _handle_build_baseline(config)
    if args.validate_baselines:
        return _handle_validate_baselines(config)
    if args.print_summary:
        _print_recent_summary(config)
        return 0
    return 0


def _handle_generate_expected(config: AcceptanceConfig) -> int:
    state = generate_fixture_outputs(config)
    print("已生成 expected_baselines.json 和 fixture_summary.json。")
    print(f"total_logs = {state['total_logs']}")
    print(f"user_count = {state['user_count']}")
    return 0


def _handle_load_fixture(config: AcceptanceConfig) -> int:
    result = load_fixture_to_clickhouse(config)
    if result["success"]:
        print("模拟数据已写入 ClickHouse。")
    else:
        print("模拟数据写入 ClickHouse 失败。")
    print(f"expected_rows = {result['expected_rows']}")
    print(f"inserted_rows = {result['inserted_rows']}")
    print(f"database_rows = {result['database_rows']}")
    print(f"error = {result['error']}")
    return 0 if result["success"] else 1


def _handle_build_baseline(config: AcceptanceConfig) -> int:
    print("开始执行 UEBA baseline 构建。")
    print("该步骤会调用 scripts/build_ueba_baseline.py，可能需要稍等。")
    result = run_baseline_build(config)
    if result["success"]:
        print("UEBA baseline 构建完成。")
    else:
        print("UEBA baseline 构建失败。")
    print("success = {}".format(result["success"]))
    print("total_log_count = {}".format(result.get("total_log_count", 0)))
    print("total_user_count = {}".format(result.get("total_user_count", 0)))
    print("reliable_user_count = {}".format(result.get("reliable_user_count", 0)))
    print("unreliable_user_count = {}".format(result.get("unreliable_user_count", 0)))
    print("build_result_path = {}".format(Path(config.output_dir) / BUILD_RESULT_FILE))
    if not result["success"]:
        print("error = {}".format(result.get("error")))
        print("请先检查 --load-fixture 是否已成功完成。")
    return 0 if result["success"] else 1


def _handle_validate_baselines(config: AcceptanceConfig) -> int:
    print("开始对比 expected_baselines.json 与数据库实际 baseline。")
    print("该步骤会读取 ClickHouse.user_behavior_baselines，并只验证 fixture_user_% 用户。")
    result = validate_fixture_baselines(config)
    if result["success"]:
        print("UEBA baseline 对比验证通过。")
    else:
        print("UEBA baseline 对比验证失败。")
    print("success = {}".format(result["success"]))
    print("checked_users = {}".format(result.get("checked_users", 0)))
    print("checked_items = {}".format(result.get("checked_items", 0)))
    print("passed_items = {}".format(result.get("passed_items", 0)))
    print("failed_items = {}".format(result.get("failed_items", 0)))
    print("validation_report_path = {}".format(Path(config.output_dir) / VALIDATION_REPORT_FILE))
    print("failed_diff_path = {}".format(Path(config.output_dir) / FAILED_DIFF_FILE))
    if not result["success"]:
        print("error = {}".format(result.get("error")))
    return 0 if result["success"] else 1


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="UEBA Baseline 数据库验收工具")
    parser.add_argument("--output-dir", help="覆盖默认输出目录，默认 .tox/ueba_baseline_acceptance")
    parser.add_argument(
        "--dump-logs-jsonl",
        action="store_true",
        help="调试用：同时写出 fixture_logs.jsonl，默认不写原始模拟日志。",
    )
    parser.add_argument(
        "--monthly-training-update",
        action="store_true",
        help="非交互执行月度训练表更新闭环验收。",
    )
    parser.add_argument(
        "--generate-expected",
        action="store_true",
        help="非交互：生成 expected_baselines.json 和 fixture_summary.json",
    )
    parser.add_argument(
        "--load-fixture",
        action="store_true",
        help="非交互：生成模拟数据并写入 ClickHouse logs_structured",
    )
    parser.add_argument(
        "--build-baseline",
        action="store_true",
        help="非交互：执行 UEBA baseline 构建并写入 user_behavior_baselines",
    )
    parser.add_argument(
        "--validate-baselines",
        action="store_true",
        help="非交互：对比 expected_baselines.json 与数据库实际 baseline",
    )
    parser.add_argument(
        "--print-summary",
        action="store_true",
        help="非交互：打印最近一次验收摘要",
    )
    return parser.parse_args(argv)


def _print_menu(config: AcceptanceConfig) -> None:
    output_dir = ensure_output_dir(config)
    status = _current_status(output_dir)
    print("UEBA Baseline 数据库验收工具")
    print()
    print(f"输出目录：{config.output_dir}")
    print(f"数据源：ClickHouse.{config.clickhouse_database}.logs_structured")
    print(f"准线结果表：ClickHouse.{config.clickhouse_database}.user_behavior_baselines")
    print()
    print("当前状态：")
    print(f"- expected_baselines.json：{status['expected']}")
    print(f"- 模拟数据入库：{status['loaded']}")
    print(f"- baseline 构建：{status['baseline_built']}")
    print(f"- 准线对比：{status['comparison']}")
    print()
    print("请选择操作：")
    print()
    print("1. 生成 expected_baselines.json 和 fixture_summary.json")
    print("2. 生成模拟数据并写入 ClickHouse")
    print("3. 执行 UEBA baseline 构建")
    print("4. 对比 expected_baselines.json 与数据库实际 baseline")
    print("5. 查看最近一次验收摘要")
    print("0. 退出")
    print()
    print("非交互月度训练表更新闭环：")
    print(".venv/bin/python -m tests.behavior.ueba_baseline_acceptance.runner --monthly-training-update")
    print()


def _current_status(output_dir: Path) -> dict[str, str]:
    state_path = output_dir / RUN_STATE_FILE
    state: dict[str, Any] = read_json(state_path) if state_path.exists() else {}
    return {
        "expected": "已生成" if (output_dir / EXPECTED_FILE).exists() else "未生成",
        "loaded": "已执行" if state.get("clickhouse_loaded") else "未执行",
        "baseline_built": "已执行" if state.get("baseline_built") else "未执行",
        "comparison": "已执行" if state.get("comparison_done") else "未执行",
    }


def _print_recent_summary(config: AcceptanceConfig) -> None:
    output_dir = ensure_output_dir(config)
    summary_path = output_dir / SUMMARY_FILE
    state_path = output_dir / RUN_STATE_FILE
    load_result_path = output_dir / LOAD_RESULT_FILE
    build_result_path = output_dir / BUILD_RESULT_FILE
    validation_report_path = output_dir / VALIDATION_REPORT_FILE
    if not summary_path.exists():
        print("\n暂无 fixture_summary.json，请先执行第 1 项。\n")
        return

    summary = read_json(summary_path)
    state: dict[str, Any] = read_json(state_path) if state_path.exists() else {}
    load_result: dict[str, Any] = read_json(load_result_path) if load_result_path.exists() else {}
    build_result: dict[str, Any] = read_json(build_result_path) if build_result_path.exists() else {}
    validation_report: dict[str, Any] = read_json(validation_report_path) if validation_report_path.exists() else {}
    print("\n最近一次验收摘要：")
    print(f"fixture_id = {summary.get('fixture_id')}")
    print(f"total_logs = {summary.get('total_logs')}")
    print(f"user_count = {summary.get('user_count')}")
    print(f"model_version = {summary.get('model_version')}")
    print(f"expected_generated = {state.get('expected_generated', False)}")
    print(f"clickhouse_loaded = {state.get('clickhouse_loaded', False)}")
    print(f"baseline_built = {state.get('baseline_built', False)}")
    print(f"comparison_done = {state.get('comparison_done', False)}")
    if load_result:
        print(f"load_success = {load_result.get('success')}")
        print(f"database_rows = {load_result.get('database_rows')}")
        print(f"load_error = {load_result.get('error')}")
    if build_result:
        print(f"build_success = {build_result.get('success')}")
        print(f"total_log_count = {build_result.get('total_log_count')}")
        print(f"total_user_count = {build_result.get('total_user_count')}")
        print(f"reliable_user_count = {build_result.get('reliable_user_count')}")
        print(f"unreliable_user_count = {build_result.get('unreliable_user_count')}")
        print(f"build_error = {build_result.get('error')}")
    if validation_report:
        print(f"validation_success = {validation_report.get('success')}")
        print(f"checked_users = {validation_report.get('checked_users')}")
        print(f"failed_items = {validation_report.get('failed_items')}")
        print(f"validation_error = {validation_report.get('error')}")
    print()


if __name__ == "__main__":
    raise SystemExit(main())
