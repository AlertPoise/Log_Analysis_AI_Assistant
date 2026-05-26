"""UEBA 离线用户行为 Baseline 构建 CLI 入口。

脚本只负责解析参数、初始化组件、调用 UebaService，并将结果输出为 JSON。
业务流程由 UebaService 内部完成。
"""

import argparse
from dataclasses import asdict
from datetime import datetime, timedelta
import json
import os
from pathlib import Path
import sys
from typing import Any

# 允许从项目根目录或 scripts 目录直接运行该脚本。
PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.behavior.aggregate_merger import AggregateMerger
from src.behavior.baseline_builder import BaselineBuilder
from src.behavior.baseline_store import BaselineStore
from src.behavior.config import UebaBaselineConfig
from src.behavior.repository import UebaRepository
from src.behavior.schemas import BaselineBuildResult
from src.behavior.service import UebaService


DEFAULT_CONFIG = UebaBaselineConfig()


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """解析 UEBA Baseline 构建 CLI 参数。"""
    parser = argparse.ArgumentParser(
        description="构建 UEBA 离线用户行为 Baseline，并输出 JSON 结果。",
    )
    parser.add_argument("--start-time", help="基线开始时间，支持 YYYY-MM-DD HH:MM:SS、YYYY-MM-DDTHH:MM:SS、YYYY-MM-DD")
    parser.add_argument("--end-time", help="基线结束时间，支持 YYYY-MM-DD HH:MM:SS、YYYY-MM-DDTHH:MM:SS、YYYY-MM-DD")
    parser.add_argument("--log-type", default="vpn", help="日志类型，默认 vpn")
    parser.add_argument(
        "--source-table",
        default="logs_structured",
        choices=("logs_structured", "ueba_baseline_training_logs"),
        help="Baseline 构建数据源表，默认 logs_structured",
    )
    parser.add_argument("--dataset-id", help="source-table 为 ueba_baseline_training_logs 时必填的训练数据集 ID")
    parser.add_argument("--active-only", action="store_true", default=False, help="训练表模式下仅读取 is_active = 1 的行")

    parser.add_argument("--baseline-window-days", type=int, default=DEFAULT_CONFIG.baseline_window_days)
    parser.add_argument("--min-sample-count", type=int, default=DEFAULT_CONFIG.min_sample_count)
    parser.add_argument("--top-source-ip-limit", type=int, default=DEFAULT_CONFIG.top_source_ip_limit)
    parser.add_argument("--top-destination-ip-limit", type=int, default=DEFAULT_CONFIG.top_destination_ip_limit)
    parser.add_argument("--top-source-country-limit", type=int, default=DEFAULT_CONFIG.top_source_country_limit)
    parser.add_argument("--top-source-city-limit", type=int, default=DEFAULT_CONFIG.top_source_city_limit)
    parser.add_argument("--top-vpn-gateway-limit", type=int, default=DEFAULT_CONFIG.top_vpn_gateway_limit)
    parser.add_argument("--top-fail-reason-limit", type=int, default=DEFAULT_CONFIG.top_fail_reason_limit)
    parser.add_argument("--top-client-software-limit", type=int, default=DEFAULT_CONFIG.top_client_software_limit)
    parser.add_argument("--top-action-limit", type=int, default=DEFAULT_CONFIG.top_action_limit)
    parser.add_argument("--top-result-limit", type=int, default=DEFAULT_CONFIG.top_result_limit)
    parser.add_argument("--common-hour-min-ratio", type=float, default=DEFAULT_CONFIG.common_hour_min_ratio)
    parser.add_argument("--common-source-ip-min-ratio", type=float, default=DEFAULT_CONFIG.common_source_ip_min_ratio)
    parser.add_argument("--common-source-city-min-ratio", type=float, default=DEFAULT_CONFIG.common_source_city_min_ratio)
    parser.add_argument("--common-vpn-gateway-min-ratio", type=float, default=DEFAULT_CONFIG.common_vpn_gateway_min_ratio)
    parser.add_argument("--model-version", default=DEFAULT_CONFIG.model_version)
    parser.add_argument("--write-batch-size", type=int, default=DEFAULT_CONFIG.write_batch_size)

    parser.add_argument("--clickhouse-host", default=os.getenv("CLICKHOUSE_HOST", "localhost"))
    parser.add_argument("--clickhouse-port", type=int, default=_env_int("CLICKHOUSE_PORT", 8123))
    parser.add_argument("--clickhouse-user", default=os.getenv("CLICKHOUSE_USER", "default"))
    parser.add_argument("--clickhouse-password", default=os.getenv("CLICKHOUSE_PASSWORD", ""))
    parser.add_argument("--clickhouse-database", default=os.getenv("CLICKHOUSE_DATABASE", "log_analysis"))
    parser.add_argument("--clickhouse-secure", action="store_true", default=False)

    args = parser.parse_args(argv)
    if args.source_table == "logs_structured" and args.active_only:
        parser.error("--active-only can only be used with --source-table ueba_baseline_training_logs")
    if args.source_table == "ueba_baseline_training_logs" and not args.dataset_id:
        parser.error("--dataset-id is required when --source-table ueba_baseline_training_logs")
    return args


def parse_datetime(value: str) -> datetime:
    """解析 CLI 时间字符串，日期输入按当天零点处理。"""
    normalized = value.strip()
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d"):
        try:
            return datetime.strptime(normalized, fmt)
        except ValueError:
            continue
    raise ValueError(f"不支持的时间格式: {value}")


def resolve_time_window(args: argparse.Namespace, config: UebaBaselineConfig) -> tuple[datetime, datetime]:
    """根据 CLI 参数解析时间窗口，缺省时使用最近 baseline_window_days 天。"""
    end_time = parse_datetime(args.end_time) if args.end_time else datetime.now()
    start_time = (
        parse_datetime(args.start_time)
        if args.start_time
        else end_time - timedelta(days=config.baseline_window_days)
    )
    return start_time, end_time


def build_config(args: argparse.Namespace) -> UebaBaselineConfig:
    """根据 CLI 参数构造 UebaBaselineConfig。"""
    return UebaBaselineConfig(
        baseline_window_days=args.baseline_window_days,
        min_sample_count=args.min_sample_count,
        top_source_ip_limit=args.top_source_ip_limit,
        top_destination_ip_limit=args.top_destination_ip_limit,
        top_source_country_limit=args.top_source_country_limit,
        top_source_city_limit=args.top_source_city_limit,
        top_vpn_gateway_limit=args.top_vpn_gateway_limit,
        top_fail_reason_limit=args.top_fail_reason_limit,
        top_client_software_limit=args.top_client_software_limit,
        top_action_limit=args.top_action_limit,
        top_result_limit=args.top_result_limit,
        common_hour_min_ratio=args.common_hour_min_ratio,
        common_source_ip_min_ratio=args.common_source_ip_min_ratio,
        common_source_city_min_ratio=args.common_source_city_min_ratio,
        common_vpn_gateway_min_ratio=args.common_vpn_gateway_min_ratio,
        model_version=args.model_version,
        write_batch_size=args.write_batch_size,
    )


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


def build_service(
    client: Any,
    database: str,
    config: UebaBaselineConfig,
    source_table: str = "logs_structured",
    dataset_id: str | None = None,
    active_only: bool = False,
) -> UebaService:
    """初始化 Repository、Merger、Builder、Store 和 Service。"""
    repository = UebaRepository(
        client=client,
        database=database,
        source_table=source_table,
        dataset_id=dataset_id,
        active_only=active_only,
    )
    aggregate_merger = AggregateMerger()
    baseline_builder = BaselineBuilder(config=config)
    baseline_store = BaselineStore(client=client, database=database, config=config)
    return UebaService(
        repository=repository,
        aggregate_merger=aggregate_merger,
        baseline_builder=baseline_builder,
        baseline_store=baseline_store,
        config=config,
    )


def result_to_json(result: BaselineBuildResult | dict[str, Any]) -> str:
    """将构建结果转换为 JSON 字符串。"""
    payload = asdict(result) if isinstance(result, BaselineBuildResult) else result
    return json.dumps(payload, ensure_ascii=False, default=str)


def failure_payload(message: str, config: UebaBaselineConfig | None = None) -> dict[str, Any]:
    """生成脚本自身初始化失败时的 JSON 结果。"""
    effective_config = config or DEFAULT_CONFIG
    return {
        "success": False,
        "baseline_start_time": None,
        "baseline_end_time": None,
        "total_user_count": 0,
        "reliable_user_count": 0,
        "unreliable_user_count": 0,
        "total_log_count": 0,
        "model_version": effective_config.model_version,
        "duration_seconds": 0.0,
        "message": message,
    }


def main(argv: list[str] | None = None) -> int:
    """CLI 主入口。"""
    config: UebaBaselineConfig | None = None
    client = None
    try:
        args = parse_args(argv)
        config = build_config(args)
        start_time, end_time = resolve_time_window(args, config)
        client = create_clickhouse_client(args)
        service = build_service(
            client,
            args.clickhouse_database,
            config,
            source_table=getattr(args, "source_table", "logs_structured"),
            dataset_id=getattr(args, "dataset_id", None),
            active_only=getattr(args, "active_only", False),
        )
        result = service.build_baseline_once(
            start_time=start_time,
            end_time=end_time,
            log_type=args.log_type,
        )
        print(result_to_json(result))
        return 0 if result.success else 1
    except Exception as exc:
        print(result_to_json(failure_payload(f"脚本执行失败: {type(exc).__name__}: {exc}", config)))
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
