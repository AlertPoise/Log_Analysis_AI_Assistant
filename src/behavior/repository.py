"""UEBA Repository 模块，用于读取数据库侧聚合结果。

本模块只封装面向 logs_structured 的参数化 GROUP BY 查询。
它不创建数据库连接、不生成 Baseline、不写入数据库，也不把
ClickHouse 原始返回对象暴露给上层模块。
"""

from collections.abc import Iterable
from typing import Any


class UebaRepository:
    """UEBA 第一版离线 Baseline 构建的聚合读取层。"""

    def __init__(self, client: Any, database: str = "log_analysis") -> None:
        """初始化 Repository。

        client 由外部传入，应提供 query(...) 或 execute(...) 方法。database
        当前仅保留为受控配置，不参与用户输入拼接。
        """
        self.client = client
        self.database = database

    def fetch_user_summary(self, start_time: Any, end_time: Any, log_type: str = "vpn") -> list[dict]:
        """读取每个用户的总览聚合统计。"""
        sql = """
        SELECT
            username,
            count() AS sample_count,
            min(timestamp) AS first_seen,
            max(timestamp) AS last_seen,
            countIf(result IN ('FAILED', 'FAIL') OR event_type = 'LOGIN_FAIL') AS failed_count,
            uniqExact(toDate(timestamp)) AS active_days,
            countIf(is_off_hours) AS off_hours_count,
            countIf(is_unusual_ip) AS unusual_ip_count
        FROM logs_structured
        PREWHERE log_type = %(log_type)s
            AND timestamp >= %(start_time)s
            AND timestamp < %(end_time)s
        WHERE username != ''
        GROUP BY username
        """
        return self._execute_query(sql, self._base_parameters(start_time, end_time, log_type))

    def fetch_hour_distribution(self, start_time: Any, end_time: Any, log_type: str = "vpn") -> list[dict]:
        """读取用户小时分布聚合。"""
        sql = """
        SELECT
            username,
            toHour(timestamp) AS active_hour,
            count() AS cnt
        FROM logs_structured
        PREWHERE log_type = %(log_type)s
            AND timestamp >= %(start_time)s
            AND timestamp < %(end_time)s
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
        sql = """
        SELECT
            username,
            toDate(timestamp) AS event_date,
            count() AS cnt
        FROM logs_structured
        PREWHERE log_type = %(log_type)s
            AND timestamp >= %(start_time)s
            AND timestamp < %(end_time)s
        WHERE username != ''
        GROUP BY username, event_date
        ORDER BY username ASC, event_date ASC
        """
        return self._execute_query(sql, self._base_parameters(start_time, end_time, log_type))

    def fetch_session_metric_summary(self, start_time: Any, end_time: Any, log_type: str = "vpn") -> list[dict]:
        """读取用户会话时长与流量聚合摘要。"""
        sql = """
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
        FROM logs_structured
        PREWHERE log_type = %(log_type)s
            AND timestamp >= %(start_time)s
            AND timestamp < %(end_time)s
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
        FROM logs_structured
        PREWHERE log_type = %(log_type)s
            AND timestamp >= %(start_time)s
            AND timestamp < %(end_time)s
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
            FROM logs_structured
            PREWHERE log_type = %(log_type)s
                AND timestamp >= %(start_time)s
                AND timestamp < %(end_time)s
            WHERE username != ''
                AND {column_name} != ''
            GROUP BY username, {output_name}
            ORDER BY username ASC, cnt DESC
        )
        LIMIT %(limit)s BY username
        """
        return self._execute_query(sql, parameters)

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
        return {
            "start_time": start_time,
            "end_time": end_time,
            "log_type": log_type,
        }

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


__all__ = ["UebaRepository"]
