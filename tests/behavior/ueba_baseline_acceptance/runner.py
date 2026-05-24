"""Interactive runner for UEBA baseline acceptance fixtures."""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any

from .clickhouse_writer import load_fixture_to_clickhouse
from .config import AcceptanceConfig
from .fixture_generator import generate_fixture_outputs
from .report_writer import ensure_output_dir, read_json


EXPECTED_FILE = "expected_baselines.json"
SUMMARY_FILE = "fixture_summary.json"
RUN_STATE_FILE = "run_state.json"
LOAD_RESULT_FILE = "load_result.json"


def main(argv: list[str] | None = None) -> int:
    """Run the interactive acceptance tool."""
    args = _parse_args(argv)
    config = AcceptanceConfig(dump_logs_jsonl=args.dump_logs_jsonl)
    if args.output_dir:
        config.output_dir = Path(args.output_dir)

    while True:
        _print_menu(config)
        choice = input("请选择操作：").strip()
        if choice == "1":
            state = generate_fixture_outputs(config)
            print("\n已生成 expected_baselines.json 和 fixture_summary.json。")
            print(f"total_logs = {state['total_logs']}")
            print(f"user_count = {state['user_count']}\n")
        elif choice == "2":
            result = load_fixture_to_clickhouse(config)
            if result["success"]:
                print("\n模拟数据已写入 ClickHouse。")
            else:
                print("\n模拟数据写入 ClickHouse 失败。")
            print(f"expected_rows = {result['expected_rows']}")
            print(f"inserted_rows = {result['inserted_rows']}")
            print(f"database_rows = {result['database_rows']}")
            print(f"error = {result['error']}\n")
        elif choice in {"3", "4"}:
            print("\n该操作将在后续阶段实现。\n")
        elif choice == "5":
            _print_recent_summary(config)
        elif choice == "0":
            print("退出。")
            return 0
        else:
            print("\n无效选择，请重新输入。\n")


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="UEBA Baseline 数据库验收工具")
    parser.add_argument("--output-dir", help="覆盖默认输出目录，默认 .tox/ueba_baseline_acceptance")
    parser.add_argument(
        "--dump-logs-jsonl",
        action="store_true",
        help="调试用：同时写出 fixture_logs.jsonl，默认不写原始模拟日志。",
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
    print("- baseline 构建：未执行 / 后续阶段实现")
    print("- 准线对比：未执行 / 后续阶段实现")
    print()
    print("请选择操作：")
    print()
    print("1. 生成 expected_baselines.json 和 fixture_summary.json")
    print("2. 生成模拟数据并写入 ClickHouse")
    print("3. 执行 UEBA baseline 构建（后续阶段）")
    print("4. 对比 expected_baselines.json 与数据库实际 baseline（后续阶段）")
    print("5. 查看最近一次验收摘要")
    print("0. 退出")
    print()


def _current_status(output_dir: Path) -> dict[str, str]:
    state_path = output_dir / RUN_STATE_FILE
    state: dict[str, Any] = read_json(state_path) if state_path.exists() else {}
    return {
        "expected": "已生成" if (output_dir / EXPECTED_FILE).exists() else "未生成",
        "loaded": "已执行" if state.get("clickhouse_loaded") else "未执行",
    }


def _print_recent_summary(config: AcceptanceConfig) -> None:
    output_dir = ensure_output_dir(config)
    summary_path = output_dir / SUMMARY_FILE
    state_path = output_dir / RUN_STATE_FILE
    load_result_path = output_dir / LOAD_RESULT_FILE
    if not summary_path.exists():
        print("\n暂无 fixture_summary.json，请先执行第 1 项。\n")
        return

    summary = read_json(summary_path)
    state: dict[str, Any] = read_json(state_path) if state_path.exists() else {}
    load_result: dict[str, Any] = read_json(load_result_path) if load_result_path.exists() else {}
    print("\n最近一次验收摘要：")
    print(f"fixture_id = {summary.get('fixture_id')}")
    print(f"total_logs = {summary.get('total_logs')}")
    print(f"user_count = {summary.get('user_count')}")
    print(f"model_version = {summary.get('model_version')}")
    print(f"expected_generated = {state.get('expected_generated', False)}")
    print(f"clickhouse_loaded = {state.get('clickhouse_loaded', False)}")
    if load_result:
        print(f"load_success = {load_result.get('success')}")
        print(f"database_rows = {load_result.get('database_rows')}")
        print(f"load_error = {load_result.get('error')}")
    print()


if __name__ == "__main__":
    raise SystemExit(main())
