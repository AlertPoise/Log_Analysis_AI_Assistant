"""UEBA Baseline 存储模块。

本模块只负责 UserBaseline 的建表、序列化、批量写入和查询。
它不读取 logs_structured，不生成 Baseline，也不执行异常检测。
"""

from collections.abc import Iterable
from dataclasses import asdict
from datetime import datetime
import json
import re
from typing import Any

from .config import UebaBaselineConfig
from .schemas import UserBaseline


class BaselineStore:
    """负责将用户行为 Baseline 写入 ClickHouse。"""

    TABLE_NAME = "user_behavior_baselines"
    COLUMNS = [
        "username",
        "sample_count",
        "is_reliable",
        "common_active_hours",
        "common_source_ips",
        "common_destination_ips",
        "common_source_countries",
        "common_source_cities",
        "common_vpn_gateways",
        "action_distribution",
        "event_type_distribution",
        "result_distribution",
        "fail_reason_distribution",
        "auth_method_distribution",
        "client_software_distribution",
        "protocol_distribution",
        "failed_rate",
        "off_hours_rate",
        "unusual_ip_rate",
        "avg_daily_events",
        "active_day_avg_events",
        "max_daily_events",
        "session_metric_summary",
        "traffic_metric_summary",
        "baseline_start_time",
        "baseline_end_time",
        "model_version",
        "baseline_json",
    ]

    def __init__(
        self,
        client: Any,
        database: str = "log_analysis",
        config: UebaBaselineConfig | None = None,
    ) -> None:
        """初始化 BaselineStore。

        Args:
            client: 外部传入的 ClickHouse client。
            database: 受控配置中的数据库名。
            config: UEBA Baseline 构建配置。
        """
        self.client = client
        self.database = self._validate_identifier(database)
        self.config = config or UebaBaselineConfig()

    def ensure_table(self) -> None:
        """确保 user_behavior_baselines 表存在。"""
        sql = f"""
        CREATE TABLE IF NOT EXISTS {self._qualified_table()}
        (
            username String,
            sample_count UInt64,
            is_reliable UInt8,

            common_active_hours String,
            common_source_ips String,
            common_destination_ips String,
            common_source_countries String,
            common_source_cities String,
            common_vpn_gateways String,

            action_distribution String,
            event_type_distribution String,
            result_distribution String,
            fail_reason_distribution String,
            auth_method_distribution String,
            client_software_distribution String,
            protocol_distribution String,

            failed_rate Float64,
            off_hours_rate Float64,
            unusual_ip_rate Float64,
            avg_daily_events Float64,
            active_day_avg_events Float64,
            max_daily_events UInt64,

            session_metric_summary String,
            traffic_metric_summary String,

            baseline_start_time DateTime,
            baseline_end_time DateTime,
            model_version String,

            baseline_json String,
            created_at DateTime DEFAULT now()
        )
        ENGINE = ReplacingMergeTree(created_at)
        ORDER BY (username, model_version, baseline_start_time, baseline_end_time)
        """
        self._execute_command(sql)

    def baseline_to_row(self, baseline: UserBaseline) -> dict:
        """将 UserBaseline 序列化为可写入 ClickHouse 的行字典。"""
        baseline_dict = asdict(baseline)
        session_metric_summary = {
            "session_duration_avg": baseline.session_duration_avg,
            "session_duration_p50": baseline.session_duration_p50,
            "session_duration_p95": baseline.session_duration_p95,
        }
        traffic_metric_summary = {
            "bytes_sent_avg": baseline.bytes_sent_avg,
            "bytes_recv_avg": baseline.bytes_recv_avg,
        }

        return {
            "username": baseline.username,
            "sample_count": baseline.sample_count,
            "is_reliable": 1 if baseline.is_reliable else 0,
            "common_active_hours": self._to_json(baseline_dict["common_active_hours"]),
            "common_source_ips": self._to_json(baseline_dict["common_source_ips"]),
            "common_destination_ips": self._to_json(baseline_dict["common_destination_ips"]),
            "common_source_countries": self._to_json(baseline_dict["common_source_countries"]),
            "common_source_cities": self._to_json(baseline_dict["common_source_cities"]),
            "common_vpn_gateways": self._to_json(baseline_dict["common_vpn_gateways"]),
            "action_distribution": self._to_json(baseline.action_distribution),
            "event_type_distribution": self._to_json(baseline.event_type_distribution),
            "result_distribution": self._to_json(baseline.result_distribution),
            "fail_reason_distribution": self._to_json(baseline.fail_reason_distribution),
            "auth_method_distribution": self._to_json(baseline.auth_method_distribution),
            "client_software_distribution": self._to_json(baseline.client_software_distribution),
            "protocol_distribution": self._to_json(baseline.protocol_distribution),
            "failed_rate": baseline.failed_rate,
            "off_hours_rate": baseline.off_hours_rate,
            "unusual_ip_rate": baseline.unusual_ip_rate,
            "avg_daily_events": baseline.avg_daily_events,
            "active_day_avg_events": baseline.active_day_avg_events,
            "max_daily_events": baseline.max_daily_events,
            "session_metric_summary": self._to_json(session_metric_summary),
            "traffic_metric_summary": self._to_json(traffic_metric_summary),
            "baseline_start_time": baseline.baseline_start_time,
            "baseline_end_time": baseline.baseline_end_time,
            "model_version": baseline.model_version,
            "baseline_json": self._to_json(baseline_dict),
        }

    def save_baselines(self, baselines: list[UserBaseline]) -> int:
        """按 write_batch_size 批量写入用户 Baseline，返回实际写入行数。"""
        if not baselines:
            return 0

        batch_size = self._validate_batch_size(self.config.write_batch_size)
        rows = [self.baseline_to_row(baseline) for baseline in baselines]
        saved_count = 0

        for start in range(0, len(rows), batch_size):
            batch = rows[start : start + batch_size]
            insert_rows = [[row[column] for column in self.COLUMNS] for row in batch]
            self.client.insert(
                self.TABLE_NAME,
                insert_rows,
                column_names=self.COLUMNS,
                database=self.database,
            )
            saved_count += len(batch)

        return saved_count

    def get_user_baseline(
        self,
        username: str,
        model_version: str | None = None,
    ) -> dict | None:
        """查询某个用户最新的 Baseline 记录。"""
        parameters: dict[str, Any] = {"username": username}
        model_filter = ""
        if model_version is not None:
            model_filter = "AND model_version = %(model_version)s"
            parameters["model_version"] = model_version

        sql = f"""
        SELECT
            username,
            sample_count,
            is_reliable,
            common_active_hours,
            common_source_ips,
            common_destination_ips,
            common_source_countries,
            common_source_cities,
            common_vpn_gateways,
            action_distribution,
            event_type_distribution,
            result_distribution,
            fail_reason_distribution,
            auth_method_distribution,
            client_software_distribution,
            protocol_distribution,
            failed_rate,
            off_hours_rate,
            unusual_ip_rate,
            avg_daily_events,
            active_day_avg_events,
            max_daily_events,
            session_metric_summary,
            traffic_metric_summary,
            baseline_start_time,
            baseline_end_time,
            model_version,
            baseline_json,
            created_at
        FROM {self._qualified_table()}
        WHERE username = %(username)s
            {model_filter}
        ORDER BY created_at DESC
        LIMIT 1
        """
        rows = self._execute_query(sql, parameters)
        if not rows:
            return None

        row = rows[0]
        baseline_json = row.get("baseline_json")
        if isinstance(baseline_json, str):
            try:
                row["baseline"] = json.loads(baseline_json)
            except json.JSONDecodeError:
                pass
        return row

    # ------------------------------------------------------------------
    # 只读查询方法
    # ------------------------------------------------------------------

    def get_latest_model_version(self) -> str | None:
        """获取最近创建的 model_version。"""
        sql = f"""
        SELECT model_version
        FROM {self._qualified_table()}
        ORDER BY created_at DESC
        LIMIT 1
        """
        rows = self._execute_query(sql, {})
        if not rows:
            return None
        return str(rows[0].get("model_version", ""))

    def get_baseline_summary(
        self,
        model_version: str,
    ) -> dict[str, Any] | None:
        """返回指定 model_version 的 baseline 聚合摘要。

        防御多窗口：先确定最新窗口，再只聚合该窗口内的行。
        """
        # 找到该 model_version 的最新训练窗口
        window_sql = f"""
        SELECT
            baseline_start_time,
            baseline_end_time,
            max(created_at) AS latest_created_at
        FROM {self._qualified_table()}
        WHERE model_version = %(model_version)s
        GROUP BY baseline_start_time, baseline_end_time
        ORDER BY latest_created_at DESC
        LIMIT 1
        """
        window_rows = self._execute_query(
            window_sql,
            {"model_version": model_version},
        )
        if not window_rows:
            return None

        window = window_rows[0]
        window_start = window["baseline_start_time"]
        window_end = window["baseline_end_time"]

        summary_sql = f"""
        SELECT
            count() AS user_count,
            sum(sample_count) AS sample_log_count,
            countIf(is_reliable = 1) AS reliable_user_count,
            countIf(is_reliable = 0) AS unreliable_user_count,
            min(b.baseline_start_time) AS summary_baseline_start_time,
            max(b.baseline_end_time) AS summary_baseline_end_time,
            max(b.created_at) AS latest_created_at
        FROM {self._qualified_table()} AS b
        WHERE b.model_version = %(model_version)s
            AND b.baseline_start_time = %(selected_window_start)s
            AND b.baseline_end_time = %(selected_window_end)s
        """
        summary_rows = self._execute_query(
            summary_sql,
            {
                "model_version": model_version,
                "selected_window_start": window_start,
                "selected_window_end": window_end,
            },
        )
        if not summary_rows:
            return None

        row = summary_rows[0]
        return {
            "model_version": model_version,
            "baseline_start_time": self._format_dt(row["summary_baseline_start_time"]),
            "baseline_end_time": self._format_dt(row["summary_baseline_end_time"]),
            "sample_user_count": int(row["user_count"] or 0),
            "sample_log_count": int(row["sample_log_count"] or 0),
            "reliable_user_count": int(row["reliable_user_count"] or 0),
            "unreliable_user_count": int(row["unreliable_user_count"] or 0),
            "latest_created_at": self._format_dt(row["latest_created_at"]),
        }

    @staticmethod
    def _format_dt(value: Any) -> str | None:
        """将 DateTime 值格式化为统一字符串。"""
        if value is None:
            return None
        if isinstance(value, datetime):
            return value.replace(tzinfo=None).strftime("%Y-%m-%d %H:%M:%S")
        return str(value)

    def _execute_command(self, sql: str) -> None:
        """执行不返回结果的 ClickHouse SQL。"""
        if hasattr(self.client, "command"):
            self.client.command(sql)
            return
        if hasattr(self.client, "execute"):
            self.client.execute(sql)
            return
        raise TypeError("client must provide command(...) or execute(...)")

    def _execute_query(self, sql: str, parameters: dict[str, Any]) -> list[dict]:
        """执行参数化查询并转换为 list[dict]。"""
        if hasattr(self.client, "query"):
            result = self.client.query(sql, parameters=parameters)
        elif hasattr(self.client, "execute"):
            result = self.client.execute(sql, parameters)
        else:
            raise TypeError("client must provide query(...) or execute(...)")
        return self._rows_to_dicts(result)

    def _rows_to_dicts(self, result: Any) -> list[dict]:
        """兼容常见 ClickHouse client 的查询返回结构。"""
        if hasattr(result, "named_results"):
            named_results = result.named_results
            rows = named_results() if callable(named_results) else named_results
            return [dict(row) for row in rows]

        if hasattr(result, "result_rows") and hasattr(result, "column_names"):
            return [dict(zip(result.column_names, row)) for row in result.result_rows]

        if isinstance(result, list):
            if not result:
                return []
            if all(isinstance(row, dict) for row in result):
                return [dict(row) for row in result]

        if isinstance(result, Iterable) and not isinstance(result, (str, bytes, dict)):
            rows = list(result)
            if all(isinstance(row, dict) for row in rows):
                return [dict(row) for row in rows]

        raise TypeError("unsupported query result format")

    def _qualified_table(self) -> str:
        """返回受控 database 与固定表名组成的 ClickHouse 表名。"""
        return f"{self.database}.{self.TABLE_NAME}"

    def _validate_identifier(self, identifier: str) -> str:
        """限制 database 标识符，避免任意 SQL 片段进入建表语句。"""
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", identifier):
            raise ValueError(f"invalid ClickHouse identifier: {identifier}")
        return identifier

    def _validate_batch_size(self, batch_size: int) -> int:
        """校验批量写入大小。"""
        if not isinstance(batch_size, int) or batch_size <= 0:
            raise ValueError("write_batch_size must be a positive integer")
        return batch_size

    def _to_json(self, value: Any) -> str:
        """按项目约定序列化 JSON 字符串。"""
        return json.dumps(value, ensure_ascii=False, default=str)


__all__ = ["BaselineStore"]
