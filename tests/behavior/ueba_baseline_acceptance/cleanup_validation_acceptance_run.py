#!/usr/bin/env python3
"""清理指定 acceptance validation_run_id 的全部结果。

仅供 acceptance 污染批次清理，不是正式应用入口。
只允许清理 fixture_validation_ 前缀的 run_id。

正确运行命令：
    PYTHONPATH=$(pwd) .venv/bin/python -m tests.behavior.ueba_baseline_acceptance.cleanup_validation_acceptance_run \
        --validation-run-id fixture_validation_20260530161359 \
        --model-version ueba_baseline_fixture_v2_monthly \
        --start-time "2026-05-01 00:00:00" \
        --end-time "2026-07-01 00:00:00"
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any

_PROJECT_ROOT = Path(__file__).resolve().parents[3]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))


def _cleanup_acceptance_run(
    client: Any,
    database: str,
    validation_run_id: str,
    model_version: str,
    start_time: str,
    end_time: str,
) -> dict[str, Any]:
    """安全清理指定 acceptance run_id 下的全部结果。"""
    result: dict[str, Any] = {
        "success": False, "before_count": 0, "after_count": 0, "error": None,
    }

    # count 前
    count_sql = f"""
    SELECT count() AS cnt FROM {database}.ueba_validation_results
    WHERE validation_run_id = %(run_id)s
        AND baseline_model_version = %(model)s
        AND timestamp >= %(start)s AND timestamp < %(end)s
    """
    count_params = {"run_id": validation_run_id, "model": model_version, "start": start_time, "end": end_time}

    try:
        rows = list(client.query(count_sql, parameters=count_params).named_results())
        result["before_count"] = int(rows[0].get("cnt", 0) or 0) if rows else 0
    except Exception as exc:
        result["error"] = f"count 失败: {exc}"
        return result

    if result["before_count"] == 0:
        result["success"] = True
        return result

    # DELETE
    delete_sql = f"""
    ALTER TABLE {database}.ueba_validation_results
    DELETE WHERE validation_run_id = %(run_id)s
        AND baseline_model_version = %(model)s
        AND timestamp >= %(start)s AND timestamp < %(end)s
    SETTINGS mutations_sync = 1
    """
    try:
        client.command(delete_sql, parameters=count_params)
    except Exception as exc:
        result["error"] = f"DELETE 失败: {exc}"
        return result

    # count 后
    try:
        rows = list(client.query(count_sql, parameters=count_params).named_results())
        result["after_count"] = int(rows[0].get("cnt", 0) or 0) if rows else 0
    except Exception as exc:
        result["error"] = f"count 后失败: {exc}"
        return result

    result["success"] = result["after_count"] == 0
    if not result["success"]:
        result["error"] = f"清理后仍有 {result['after_count']} 行"
    return result


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="清理指定 acceptance validation run 的全部结果")
    p.add_argument("--validation-run-id", required=True)
    p.add_argument("--model-version", required=True)
    p.add_argument("--start-time", required=True)
    p.add_argument("--end-time", required=True)
    p.add_argument("--host", default="localhost")
    p.add_argument("--port", type=int, default=8123)
    p.add_argument("--username", default="default")
    p.add_argument("--password", default="")
    p.add_argument("--database", default="log_analysis")
    return p.parse_args(argv)


def main(argv: list[str] | None = None):
    args = parse_args(argv)

    if not args.validation_run_id.startswith("fixture_validation_"):
        print(f"ERROR: 只允许清理 fixture_validation_ 前缀的 run_id，当前: {args.validation_run_id}")
        sys.exit(1)

    print(f"即将清理 validation acceptance run:")
    print(f"  run_id:       {args.validation_run_id}")
    print(f"  model_version: {args.model_version}")
    print(f"  窗口:          {args.start_time} ~ {args.end_time}")
    print(f"  database:      {args.database}")
    resp = input("确认继续？(yes/no): ").strip().lower()
    if resp != "yes":
        print("已取消。")
        sys.exit(0)

    import clickhouse_connect
    client = clickhouse_connect.get_client(
        host=args.host, port=args.port, username=args.username, password=args.password,
        database=args.database,
    )

    result = _cleanup_acceptance_run(
        client, args.database, args.validation_run_id, args.model_version,
        args.start_time, args.end_time,
    )

    print(f"清理前: {result['before_count']} 行")
    print(f"清理后: {result['after_count']} 行")
    print(f"结果: {'PASS' if result['success'] else 'FAIL'}")
    if result.get("error"):
        print(f"错误: {result['error']}")
    sys.exit(0 if result["success"] else 1)


if __name__ == "__main__":
    main()
