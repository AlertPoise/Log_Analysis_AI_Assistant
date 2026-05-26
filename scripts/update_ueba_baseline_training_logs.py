"""手动更新 UEBA baseline 训练日志表的 CLI。

脚本只负责解析参数、初始化 ClickHouse client、调用 TrainingLogStore，
并将执行结果输出为 JSON；不会写入本地状态文件。
"""

import argparse
from datetime import datetime
import json
import os
from pathlib import Path
import sys
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.behavior.training_log_store import TrainingLogStore


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """解析训练日志表更新 CLI 参数。"""
    parser = argparse.ArgumentParser(
        description="从 logs_structured 手动复制数据到 UEBA baseline 训练日志表，并输出 JSON 结果。",
    )
    parser.add_argument("--mode", choices=("append", "replace"), required=True, help="更新模式：append 追加，replace 替换 dataset_id")
    parser.add_argument("--dataset-id", required=True, help="训练数据集 ID")
    parser.add_argument("--baseline-purpose", required=True, help="训练数据用途，例如 initial_build")
    parser.add_argument("--import-batch-id", required=True, help="本次导入批次 ID")
    parser.add_argument("--start-time", required=True, help="筛选开始时间，支持 YYYY-MM-DD HH:MM:SS、YYYY-MM-DDTHH:MM:SS、YYYY-MM-DD")
    parser.add_argument("--end-time", required=True, help="筛选结束时间，支持 YYYY-MM-DD HH:MM:SS、YYYY-MM-DDTHH:MM:SS、YYYY-MM-DD")
    parser.add_argument("--log-type", default="vpn", help="日志类型，默认 vpn")
    parser.add_argument("--inactive", action="store_true", default=False, help="写入训练表时将 is_active 置为 0")
    parser.add_argument("--remark", help="写入训练表的备注")
    parser.add_argument("--created-by", help="写入训练表的创建人标识")

    parser.add_argument("--clickhouse-host", default=os.getenv("CLICKHOUSE_HOST", "localhost"))
    parser.add_argument("--clickhouse-port", type=int, default=_env_int("CLICKHOUSE_PORT", 8123))
    parser.add_argument("--clickhouse-user", default=os.getenv("CLICKHOUSE_USER", "default"))
    parser.add_argument("--clickhouse-password", default=os.getenv("CLICKHOUSE_PASSWORD", ""))
    parser.add_argument("--clickhouse-database", default=os.getenv("CLICKHOUSE_DATABASE", "log_analysis"))
    parser.add_argument("--clickhouse-secure", action="store_true", default=False)
    return parser.parse_args(argv)


def parse_datetime(value: str) -> datetime:
    """解析 CLI 时间字符串，日期输入按当天零点处理。"""
    normalized = value.strip()
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d"):
        try:
            return datetime.strptime(normalized, fmt)
        except ValueError:
            continue
    raise ValueError(f"不支持的时间格式: {value}")


def create_clickhouse_client(args: argparse.Namespace):
    """创建 ClickHouse client，延迟导入以保证 --help 和脚本导入可用。"""
    try:
        import clickhouse_connect
    except ImportError as exc:
        raise RuntimeError(
            "缺少 clickhouse_connect 依赖，请确认 requirements.txt 已安装 clickhouse-connect。"
        ) from exc

    client = clickhouse_connect.get_client(
        host=args.clickhouse_host,
        port=args.clickhouse_port,
        username=args.clickhouse_user,
        password=args.clickhouse_password,
        database=args.clickhouse_database,
        secure=args.clickhouse_secure,
    )
    client.command("SELECT 1")
    return client


def run_update(args: argparse.Namespace, client: Any) -> dict[str, Any]:
    """执行训练日志表更新并返回 JSON payload。"""
    start_time = parse_datetime(args.start_time)
    end_time = parse_datetime(args.end_time)
    if start_time >= end_time:
        raise ValueError("start_time 必须早于 end_time")

    store = TrainingLogStore(client=client, database=args.clickhouse_database)
    store.ensure_table()
    common = {
        "dataset_id": args.dataset_id,
        "baseline_purpose": args.baseline_purpose,
        "import_batch_id": args.import_batch_id,
        "start_time": start_time,
        "end_time": end_time,
        "log_type": args.log_type,
        "is_active": 0 if args.inactive else 1,
        "remark": args.remark,
        "created_by": args.created_by,
    }
    if args.mode == "replace":
        counts = store.replace_from_logs_structured(**common)
    else:
        counts = store.append_from_logs_structured(**common)

    return {
        "success": True,
        "mode": args.mode,
        "dataset_id": args.dataset_id,
        "source_table": TrainingLogStore.SOURCE_TABLE,
        "target_table": TrainingLogStore.TARGET_TABLE,
        "selected_rows": counts["selected_rows"],
        "inserted_rows": counts["inserted_rows"],
        "target_rows": counts["target_rows"],
        "start_time": start_time,
        "end_time": end_time,
        "log_type": args.log_type,
        "message": f"{args.mode} dataset {args.dataset_id}: inserted {counts['inserted_rows']} rows",
        "error": None,
    }


def failure_payload(message: str, args: argparse.Namespace | None = None) -> dict[str, Any]:
    """生成脚本失败时的 JSON payload。"""
    return {
        "success": False,
        "mode": getattr(args, "mode", None),
        "dataset_id": getattr(args, "dataset_id", None),
        "source_table": TrainingLogStore.SOURCE_TABLE,
        "target_table": TrainingLogStore.TARGET_TABLE,
        "selected_rows": 0,
        "inserted_rows": 0,
        "target_rows": 0,
        "start_time": getattr(args, "start_time", None),
        "end_time": getattr(args, "end_time", None),
        "log_type": getattr(args, "log_type", None),
        "message": "执行失败",
        "error": message,
    }


def result_to_json(payload: dict[str, Any]) -> str:
    """序列化 CLI 输出。"""
    return json.dumps(payload, ensure_ascii=False, default=str)


def main(argv: list[str] | None = None) -> int:
    """CLI 主入口。"""
    args: argparse.Namespace | None = None
    client = None
    try:
        args = parse_args(argv)
        client = create_clickhouse_client(args)
        payload = run_update(args, client)
        print(result_to_json(payload))
        return 0
    except Exception as exc:
        print(result_to_json(failure_payload(f"{type(exc).__name__}: {exc}", args)))
        return 1
    finally:
        if client is not None and hasattr(client, "close"):
            try:
                client.close()
            except Exception:
                pass


def _env_int(name: str, default: int) -> int:
    """读取整数环境变量，非法值回退到默认值。"""
    value = os.getenv(name)
    if value is None or value == "":
        return default
    try:
        return int(value)
    except ValueError:
        return default


if __name__ == "__main__":
    raise SystemExit(main())
