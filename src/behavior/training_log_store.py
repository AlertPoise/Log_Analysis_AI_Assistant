"""UEBA baseline 训练日志表存储操作。

本模块只负责 ClickHouse 中 ueba_baseline_training_logs 的建表、计数、
按 dataset_id 删除，以及从 logs_structured 手动复制训练数据。
"""

from collections.abc import Iterable
import re
from typing import Any


class TrainingLogStore:
    """管理 UEBA baseline 手动训练日志表。"""

    SOURCE_TABLE = "logs_structured"
    TARGET_TABLE = "ueba_baseline_training_logs"
    INSERT_COLUMNS = [
        "dataset_id",
        "baseline_purpose",
        "is_active",
        "import_batch_id",
        "id",
        "timestamp",
        "log_type",
        "source",
        "username",
        "user_id",
        "dept",
        "role",
        "action",
        "event_type",
        "result",
        "fail_reason",
        "source_ip",
        "destination_ip",
        "vpn_gateway",
        "src_country",
        "src_city",
        "protocol",
        "auth_method",
        "client_software",
        "user_agent",
        "session_id",
        "is_off_hours",
        "is_unusual_ip",
        "session_duration_sec",
        "bytes_sent",
        "bytes_recv",
        "uri",
        "method",
        "status_code",
        "response_time",
        "detail",
        "severity_level",
        "device_info",
        "location",
        "request_id",
        "raw_log",
        "parser",
        "parse_status",
        "source_table",
        "source_record_id",
        "remark",
        "created_by",
    ]

    def __init__(self, client: Any, database: str = "log_analysis") -> None:
        """初始化训练日志表 Store。"""
        self.client = client
        self.database = self._validate_identifier(database)

    def ensure_table(self) -> None:
        """确保 ueba_baseline_training_logs 表存在。"""
        sql = f"""
        CREATE TABLE IF NOT EXISTS {self._qualified_target_table()}
        (
            dataset_id String,
            baseline_purpose String,
            is_active UInt8,
            import_batch_id String,
            id UInt64,
            timestamp DateTime,
            log_type String,
            source String,
            username String,
            user_id Nullable(String),
            dept Nullable(String),
            role Nullable(String),
            action String,
            event_type Nullable(String),
            result Nullable(String),
            fail_reason Nullable(String),
            source_ip Nullable(String),
            destination_ip Nullable(String),
            vpn_gateway Nullable(String),
            src_country Nullable(String),
            src_city Nullable(String),
            protocol Nullable(String),
            auth_method Nullable(String),
            client_software Nullable(String),
            user_agent Nullable(String),
            session_id Nullable(String),
            is_off_hours Nullable(Bool),
            is_unusual_ip Nullable(Bool),
            session_duration_sec Nullable(UInt32),
            bytes_sent Nullable(UInt64),
            bytes_recv Nullable(UInt64),
            uri Nullable(String),
            method Nullable(String),
            status_code Nullable(UInt16),
            response_time Nullable(Float32),
            detail Nullable(String),
            severity_level Nullable(String),
            device_info Nullable(String),
            location Nullable(String),
            request_id Nullable(String),
            raw_log Nullable(String),
            parser Nullable(String),
            parse_status Nullable(String),
            source_table Nullable(String),
            source_record_id Nullable(String),
            remark Nullable(String),
            created_by Nullable(String),
            created_at DateTime DEFAULT now()
        )
        ENGINE = MergeTree()
        PARTITION BY toYYYYMM(timestamp)
        ORDER BY (dataset_id, log_type, timestamp, username)
        """
        self._execute_command(sql)

    def append_from_logs_structured(
        self,
        *,
        dataset_id: str,
        baseline_purpose: str,
        import_batch_id: str,
        start_time: Any,
        end_time: Any,
        log_type: str,
        is_active: int = 1,
        remark: str | None = None,
        created_by: str | None = None,
    ) -> dict[str, Any]:
        """向指定 dataset_id 追加 logs_structured 中筛选出的训练数据。"""
        selected_rows = self.count_source_rows(start_time=start_time, end_time=end_time, log_type=log_type)
        self._insert_from_logs_structured(
            dataset_id=dataset_id,
            baseline_purpose=baseline_purpose,
            import_batch_id=import_batch_id,
            start_time=start_time,
            end_time=end_time,
            log_type=log_type,
            is_active=is_active,
            remark=remark,
            created_by=created_by,
        )
        target_rows = self.count_dataset_rows(dataset_id)
        return {
            "selected_rows": selected_rows,
            "inserted_rows": selected_rows,
            "target_rows": target_rows,
        }

    def replace_from_logs_structured(
        self,
        *,
        dataset_id: str,
        baseline_purpose: str,
        import_batch_id: str,
        start_time: Any,
        end_time: Any,
        log_type: str,
        is_active: int = 1,
        remark: str | None = None,
        created_by: str | None = None,
    ) -> dict[str, Any]:
        """删除指定 dataset_id 后重新写入训练数据。"""
        self.delete_dataset(dataset_id)
        return self.append_from_logs_structured(
            dataset_id=dataset_id,
            baseline_purpose=baseline_purpose,
            import_batch_id=import_batch_id,
            start_time=start_time,
            end_time=end_time,
            log_type=log_type,
            is_active=is_active,
            remark=remark,
            created_by=created_by,
        )

    def count_source_rows(self, *, start_time: Any, end_time: Any, log_type: str) -> int:
        """统计 logs_structured 中符合训练导入条件的行数。"""
        sql = f"""
        SELECT count() AS cnt
        FROM {self._qualified_source_table()}
        PREWHERE log_type = %(log_type)s
            AND timestamp >= %(start_time)s
            AND timestamp < %(end_time)s
        WHERE username != ''
        """
        return self._query_count(
            sql,
            {
                "start_time": start_time,
                "end_time": end_time,
                "log_type": log_type,
            },
        )

    def count_dataset_rows(self, dataset_id: str) -> int:
        """统计训练表中指定 dataset_id 的当前行数。"""
        sql = f"""
        SELECT count() AS cnt
        FROM {self._qualified_target_table()}
        WHERE dataset_id = %(dataset_id)s
        """
        return self._query_count(sql, {"dataset_id": dataset_id})

    def delete_dataset(self, dataset_id: str) -> None:
        """删除训练表中指定 dataset_id 的旧数据。"""
        sql = f"""
        ALTER TABLE {self._qualified_target_table()}
        DELETE WHERE dataset_id = %(dataset_id)s
        SETTINGS mutations_sync = 1
        """
        self._execute_command(sql, {"dataset_id": dataset_id})

    def _insert_from_logs_structured(
        self,
        *,
        dataset_id: str,
        baseline_purpose: str,
        import_batch_id: str,
        start_time: Any,
        end_time: Any,
        log_type: str,
        is_active: int,
        remark: str | None,
        created_by: str | None,
    ) -> None:
        """执行从 logs_structured 到训练表的 INSERT SELECT。"""
        columns = ",\n            ".join(self.INSERT_COLUMNS)
        sql = f"""
        INSERT INTO {self._qualified_target_table()}
        (
            {columns}
        )
        SELECT
            %(dataset_id)s AS dataset_id,
            %(baseline_purpose)s AS baseline_purpose,
            %(is_active)s AS is_active,
            %(import_batch_id)s AS import_batch_id,
            id,
            timestamp,
            log_type,
            source,
            username,
            user_id,
            dept,
            role,
            action,
            event_type,
            result,
            fail_reason,
            source_ip,
            destination_ip,
            vpn_gateway,
            src_country,
            src_city,
            protocol,
            auth_method,
            client_software,
            user_agent,
            session_id,
            is_off_hours,
            is_unusual_ip,
            session_duration_sec,
            bytes_sent,
            bytes_recv,
            uri,
            method,
            status_code,
            response_time,
            detail,
            severity_level,
            device_info,
            location,
            request_id,
            raw_log,
            parser,
            parse_status,
            %(source_table)s AS source_table,
            toString(id) AS source_record_id,
            %(remark)s AS remark,
            %(created_by)s AS created_by
        FROM {self._qualified_source_table()}
        PREWHERE log_type = %(log_type)s
            AND timestamp >= %(start_time)s
            AND timestamp < %(end_time)s
        WHERE username != ''
        """
        self._execute_command(
            sql,
            {
                "dataset_id": dataset_id,
                "baseline_purpose": baseline_purpose,
                "is_active": int(is_active),
                "import_batch_id": import_batch_id,
                "start_time": start_time,
                "end_time": end_time,
                "log_type": log_type,
                "source_table": self.SOURCE_TABLE,
                "remark": remark,
                "created_by": created_by,
            },
        )

    def _execute_command(self, sql: str, parameters: dict[str, Any] | None = None) -> None:
        """执行不返回结果的 ClickHouse SQL。"""
        if hasattr(self.client, "command"):
            self.client.command(sql, parameters=parameters)
            return
        if hasattr(self.client, "execute"):
            self.client.execute(sql, parameters or {})
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

    def _query_count(self, sql: str, parameters: dict[str, Any]) -> int:
        """执行 count 查询并返回整数。"""
        rows = self._execute_query(sql, parameters)
        if not rows:
            return 0
        row = rows[0]
        if "cnt" in row:
            return int(row["cnt"] or 0)
        return int(next(iter(row.values())) or 0)

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

    def _qualified_source_table(self) -> str:
        """返回 logs_structured 全限定表名。"""
        return f"{self.database}.{self.SOURCE_TABLE}"

    def _qualified_target_table(self) -> str:
        """返回 ueba_baseline_training_logs 全限定表名。"""
        return f"{self.database}.{self.TARGET_TABLE}"

    def _validate_identifier(self, identifier: str) -> str:
        """限制 database 标识符，避免任意 SQL 片段进入表名。"""
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", identifier):
            raise ValueError(f"invalid ClickHouse identifier: {identifier}")
        return identifier


__all__ = ["TrainingLogStore"]
