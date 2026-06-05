"""UEBA Repository 模块，用于读取数据库侧聚合结果。

本模块只封装面向受控数据源表的参数化 GROUP BY 查询。
它不创建数据库连接、不生成 Baseline、不写入数据库，也不把
ClickHouse 原始返回对象暴露给上层模块。
"""

from collections.abc import Iterable
import re
from typing import Any


class UebaRepository:
    """UEBA 第一版离线 Baseline 构建的聚合读取层。"""

    ALLOWED_SOURCE_TABLES = {"logs_structured", "ueba_baseline_training_logs"}

    def __init__(
        self,
        client: Any,
        database: str = "log_analysis",
        source_table: str = "logs_structured",
        dataset_id: str | None = None,
        active_only: bool = False,
        usernames: list[str] | None = None,
    ) -> None:
        """初始化 Repository。

        client 由外部传入，应提供 query(...) 或 execute(...) 方法。
        source_table 只允许在实时结构化日志表和手动训练表之间切换。
        """
        self.client = client
        self.database = self._validate_identifier(database)
        self.source_table = self._validate_source_table(source_table)
        self.dataset_id = dataset_id
        self.active_only = active_only
        self.usernames = self._normalize_usernames(usernames)

        if self.source_table == "ueba_baseline_training_logs" and not self.dataset_id:
            raise ValueError("dataset_id is required when source_table is ueba_baseline_training_logs")

    def fetch_user_summary(self, start_time: Any, end_time: Any, log_type: str = "vpn") -> list[dict]:
        """读取每个用户的总览聚合统计。"""
        sql = f"""
        SELECT
            username,
            count() AS sample_count,
            min(timestamp) AS first_seen,
            max(timestamp) AS last_seen,
            countIf(result IN ('FAILED', 'FAIL') OR event_type = 'LOGIN_FAIL') AS failed_count,
            uniqExact(toDate(timestamp)) AS active_days,
            countIf(is_off_hours) AS off_hours_count,
            countIf(is_unusual_ip) AS unusual_ip_count
        FROM {self._qualified_source_table()}
        PREWHERE {self._prewhere_clause()}
        WHERE username != ''
        GROUP BY username
        """
        return self._execute_query(sql, self._base_parameters(start_time, end_time, log_type))

    def fetch_hour_distribution(self, start_time: Any, end_time: Any, log_type: str = "vpn") -> list[dict]:
        """读取用户小时分布聚合。"""
        sql = f"""
        SELECT
            username,
            toHour(timestamp) AS active_hour,
            count() AS cnt
        FROM {self._qualified_source_table()}
        PREWHERE {self._prewhere_clause()}
        WHERE username != ''
        GROUP BY username, active_hour
        ORDER BY username ASC, active_hour ASC
        """
        return self._execute_query(sql, self._base_parameters(start_time, end_time, log_type))

    def fetch_top_source_ips(self, start_time: Any, end_time: Any, limit: int, log_type: str = "vpn") -> list[dict]:
        """读取每个用户常用来源 IP Top-N。"""
        return self._fetch_top_value_counts(
            start_time=start_time,
            end_time=end_time,
            limit=limit,
            log_type=log_type,
            column_name="source_ip",
            output_name="source_ip",
        )

    def fetch_top_destination_ips(self, start_time: Any, end_time: Any, limit: int, log_type: str = "vpn") -> list[dict]:
        """读取每个用户常用目标 IP Top-N。"""
        return self._fetch_top_value_counts(
            start_time=start_time,
            end_time=end_time,
            limit=limit,
            log_type=log_type,
            column_name="destination_ip",
            output_name="destination_ip",
        )

    def fetch_top_source_countries(self, start_time: Any, end_time: Any, limit: int, log_type: str = "vpn") -> list[dict]:
        """读取每个用户常用来源国家 Top-N。"""
        return self._fetch_top_value_counts(
            start_time=start_time,
            end_time=end_time,
            limit=limit,
            log_type=log_type,
            column_name="src_country",
            output_name="source_country",
        )

    def fetch_top_source_cities(self, start_time: Any, end_time: Any, limit: int, log_type: str = "vpn") -> list[dict]:
        """读取每个用户常用来源城市 Top-N。"""
        return self._fetch_top_value_counts(
            start_time=start_time,
            end_time=end_time,
            limit=limit,
            log_type=log_type,
            column_name="src_city",
            output_name="source_city",
        )

    def fetch_top_vpn_gateways(self, start_time: Any, end_time: Any, limit: int, log_type: str = "vpn") -> list[dict]:
        """读取每个用户常用 VPN 网关 Top-N。"""
        return self._fetch_top_value_counts(
            start_time=start_time,
            end_time=end_time,
            limit=limit,
            log_type=log_type,
            column_name="vpn_gateway",
            output_name="vpn_gateway",
        )

    def fetch_action_distribution(self, start_time: Any, end_time: Any, log_type: str = "vpn") -> list[dict]:
        """读取用户 action 分布。"""
        return self._fetch_distribution(start_time, end_time, log_type, "action", "action")

    def fetch_event_type_distribution(self, start_time: Any, end_time: Any, log_type: str = "vpn") -> list[dict]:
        """读取用户 event_type 分布。"""
        return self._fetch_distribution(start_time, end_time, log_type, "event_type", "event_type")

    def fetch_result_distribution(self, start_time: Any, end_time: Any, log_type: str = "vpn") -> list[dict]:
        """读取用户 result 分布。"""
        return self._fetch_distribution(start_time, end_time, log_type, "result", "result")

    def fetch_fail_reason_distribution(self, start_time: Any, end_time: Any, limit: int, log_type: str = "vpn") -> list[dict]:
        """读取每个用户失败原因 Top-N。"""
        return self._fetch_top_value_counts(
            start_time=start_time,
            end_time=end_time,
            limit=limit,
            log_type=log_type,
            column_name="fail_reason",
            output_name="fail_reason",
        )

    def fetch_auth_method_distribution(self, start_time: Any, end_time: Any, log_type: str = "vpn") -> list[dict]:
        """读取用户 auth_method 分布。"""
        return self._fetch_distribution(start_time, end_time, log_type, "auth_method", "auth_method")

    def fetch_client_software_distribution(self, start_time: Any, end_time: Any, limit: int, log_type: str = "vpn") -> list[dict]:
        """读取每个用户客户端软件 Top-N。"""
        return self._fetch_top_value_counts(
            start_time=start_time,
            end_time=end_time,
            limit=limit,
            log_type=log_type,
            column_name="client_software",
            output_name="client_software",
        )

    def fetch_protocol_distribution(self, start_time: Any, end_time: Any, log_type: str = "vpn") -> list[dict]:
        """读取用户 protocol 分布。"""
        return self._fetch_distribution(start_time, end_time, log_type, "protocol", "protocol")

    def fetch_daily_event_counts(self, start_time: Any, end_time: Any, log_type: str = "vpn") -> list[dict]:
        """读取用户每日事件数聚合。"""
        sql = f"""
        SELECT
            username,
            toDate(timestamp) AS event_date,
            count() AS cnt
        FROM {self._qualified_source_table()}
        PREWHERE {self._prewhere_clause()}
        WHERE username != ''
        GROUP BY username, event_date
        ORDER BY username ASC, event_date ASC
        """
        return self._execute_query(sql, self._base_parameters(start_time, end_time, log_type))

    def fetch_session_metric_summary(self, start_time: Any, end_time: Any, log_type: str = "vpn") -> list[dict]:
        """读取用户会话时长与流量聚合摘要。"""
        sql = f"""
        SELECT
            username,
            avg(session_duration_sec) AS session_duration_avg,
            max(session_duration_sec) AS session_duration_max,
            quantileExact(0.5)(session_duration_sec) AS session_duration_p50,
            quantileExact(0.95)(session_duration_sec) AS session_duration_p95,
            avg(bytes_sent) AS bytes_sent_avg,
            avg(bytes_recv) AS bytes_recv_avg,
            max(bytes_sent) AS bytes_sent_max,
            max(bytes_recv) AS bytes_recv_max
        FROM {self._qualified_source_table()}
        PREWHERE {self._prewhere_clause()}
        WHERE username != ''
        GROUP BY username
        """
        return self._execute_query(sql, self._base_parameters(start_time, end_time, log_type))

    def _fetch_distribution(
        self,
        start_time: Any,
        end_time: Any,
        log_type: str,
        column_name: str,
        output_name: str,
    ) -> list[dict]:
        """读取不需要 Top-N 截断的用户维度分布。"""
        self._validate_known_column(column_name)
        sql = f"""
        SELECT
            username,
            {column_name} AS {output_name},
            count() AS cnt
        FROM {self._qualified_source_table()}
        PREWHERE {self._prewhere_clause()}
        WHERE username != ''
            AND {column_name} != ''
        GROUP BY username, {output_name}
        ORDER BY username ASC, cnt DESC
        """
        return self._execute_query(sql, self._base_parameters(start_time, end_time, log_type))

    def _fetch_top_value_counts(
        self,
        start_time: Any,
        end_time: Any,
        limit: int,
        log_type: str,
        column_name: str,
        output_name: str,
    ) -> list[dict]:
        """读取每个用户的 Top-N 计数分布。"""
        self._validate_known_column(column_name)
        parameters = self._base_parameters(start_time, end_time, log_type)
        parameters["limit"] = self._validate_limit(limit)
        sql = f"""
        SELECT
            username,
            {output_name},
            cnt
        FROM
        (
            SELECT
                username,
                {column_name} AS {output_name},
                count() AS cnt
            FROM {self._qualified_source_table()}
            PREWHERE {self._prewhere_clause()}
            WHERE username != ''
                AND {column_name} != ''
            GROUP BY username, {output_name}
            ORDER BY username ASC, cnt DESC
        )
        LIMIT %(limit)s BY username
        """
        return self._execute_query(sql, parameters)

    @staticmethod
    def _normalize_usernames(usernames: list[str] | None) -> list[str] | None:
        """Normalize and validate usernames. Empty list is rejected."""
        if usernames is None:
            return None
        if not isinstance(usernames, list):
            raise ValueError("usernames must be None or a non-empty list")
        cleaned = sorted({u.strip() for u in usernames if u and u.strip()})
        if not cleaned:
            raise ValueError("usernames must be None or a non-empty list")
        return cleaned

    def _execute_query(self, sql: str, parameters: dict[str, Any]) -> list[dict]:
        """执行查询并统一转换为 list[dict]。"""
        if hasattr(self.client, "query"):
            result = self.client.query(sql, parameters=parameters)
        elif hasattr(self.client, "execute"):
            result = self.client.execute(sql, parameters)
        else:
            raise TypeError("client must provide query(...) or execute(...)")

        return self._rows_to_dicts(result)

    def _rows_to_dicts(self, result: Any) -> list[dict]:
        """兼容常见 ClickHouse 客户端返回结构。"""
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

    def _base_parameters(self, start_time: Any, end_time: Any, log_type: str) -> dict[str, Any]:
        """构造所有查询共用的参数。"""
        parameters: dict[str, Any] = {
            "start_time": start_time,
            "end_time": end_time,
            "log_type": log_type,
        }
        if self.source_table == "ueba_baseline_training_logs":
            parameters["dataset_id"] = self.dataset_id
        if self.usernames:
            for i, u in enumerate(self.usernames):
                parameters[f"uname_{i}"] = u
        return parameters

    def _qualified_source_table(self) -> str:
        """返回受控 database 与 source_table 组成的 ClickHouse 表名。"""
        return f"{self.database}.{self.source_table}"

    def _prewhere_clause(self) -> str:
        """返回所有聚合 SQL 共用的受控 PREWHERE 条件。"""
        conditions = [
            "log_type = %(log_type)s",
            "timestamp >= %(start_time)s",
            "timestamp < %(end_time)s",
        ]
        if self.source_table == "ueba_baseline_training_logs":
            conditions.append("dataset_id = %(dataset_id)s")
            if self.active_only:
                conditions.append("is_active = 1")
        if self.usernames:
            placeholders = ", ".join(f"%(uname_{i})s" for i in range(len(self.usernames)))
            conditions.append(f"username IN ({placeholders})")
        return "\n            AND ".join(conditions)

    def _validate_limit(self, limit: int) -> int:
        """限制 Top-N 参数，避免无效数量进入 SQL 参数。"""
        if not isinstance(limit, int) or limit <= 0:
            raise ValueError("limit must be a positive integer")
        return limit

    def _validate_known_column(self, column_name: str) -> None:
        """只允许内部白名单字段进入固定 SQL 模板。"""
        allowed_columns = {
            "source_ip",
            "destination_ip",
            "src_country",
            "src_city",
            "vpn_gateway",
            "action",
            "event_type",
            "result",
            "fail_reason",
            "auth_method",
            "client_software",
            "protocol",
        }
        if column_name not in allowed_columns:
            raise ValueError(f"unsupported aggregation column: {column_name}")

    def _validate_source_table(self, source_table: str) -> str:
        """限制 source_table，避免任意表名进入 SQL。"""
        if source_table not in self.ALLOWED_SOURCE_TABLES:
            raise ValueError(f"unsupported source_table: {source_table}")
        return source_table

    def _validate_identifier(self, identifier: str) -> str:
        """限制 database 标识符，避免任意 SQL 片段进入表名。"""
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", identifier):
            raise ValueError(f"invalid ClickHouse identifier: {identifier}")
        return identifier


__all__ = ["UebaRepository"]
