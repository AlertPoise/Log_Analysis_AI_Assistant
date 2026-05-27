"""UEBA validation repository.

This module reads controlled target logs and manages the append-only UEBA
validation result table. It does not read training data, change baselines,
update source logs, or implement scoring.
"""

from collections.abc import Iterable
from dataclasses import asdict, is_dataclass
import json
import re
from typing import Any

from .validation_schemas import ScoreReason, UebaValidationResult, ValidationTargetLog


class UebaValidationRepository:
    """Persist UEBA validation results into ClickHouse."""

    TABLE_NAME = "ueba_validation_results"
    SOURCE_TABLE = "logs_structured"
    COLUMNS = [
        "validation_id",
        "source_log_id",
        "timestamp",
        "username",
        "log_type",
        "request_id",
        "baseline_model_version",
        "baseline_created_at",
        "baseline_is_reliable",
        "ueba_score",
        "ueba_risk_level",
        "ueba_anomaly_reasons",
        "validation_status",
        "validated_at",
        "error",
    ]
    TARGET_LOG_COLUMNS = [
        "id",
        "timestamp",
        "username",
        "log_type",
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
        "is_off_hours",
        "is_unusual_ip",
        "request_id",
        "raw_log",
    ]

    def __init__(
        self,
        client: Any,
        database: str = "log_analysis",
        write_batch_size: int = 1000,
    ) -> None:
        """Initialize the validation result repository."""
        self.client = client
        self.database = self._validate_identifier(database)
        self.write_batch_size = self._validate_batch_size(write_batch_size)

    def ensure_table(self) -> None:
        """Ensure the UEBA validation result table exists."""
        sql = f"""
        CREATE TABLE IF NOT EXISTS {self._qualified_table()}
        (
            validation_id String,
            source_log_id UInt64,
            timestamp DateTime,
            username String,
            log_type String,
            request_id Nullable(String),
            baseline_model_version Nullable(String),
            baseline_created_at Nullable(DateTime),
            baseline_is_reliable UInt8,
            ueba_score UInt8,
            ueba_risk_level LowCardinality(String),
            ueba_anomaly_reasons String,
            validation_status LowCardinality(String),
            validated_at DateTime,
            error Nullable(String),
            created_at DateTime DEFAULT now()
        )
        ENGINE = MergeTree()
        PARTITION BY toYYYYMM(timestamp)
        ORDER BY (baseline_model_version, log_type, timestamp, username, source_log_id)
        """
        self._execute_command(sql)

    def fetch_target_logs(
        self,
        start_time: str,
        end_time: str,
        log_type: str = "vpn",
        limit: int = 1000,
    ) -> list[ValidationTargetLog]:
        """Fetch structured source logs for later UEBA validation."""
        self._validate_time_window(start_time, end_time)
        parameters = {
            "start_time": start_time,
            "end_time": end_time,
            "log_type": log_type,
            "limit": self._validate_limit(limit),
        }
        sql = f"""
        SELECT
            id,
            timestamp,
            username,
            log_type,
            source_ip,
            destination_ip,
            src_country,
            src_city,
            vpn_gateway,
            action,
            event_type,
            result,
            fail_reason,
            auth_method,
            client_software,
            protocol,
            is_off_hours,
            is_unusual_ip,
            request_id,
            raw_log
        FROM {self._qualified_source_table()}
        PREWHERE log_type = %(log_type)s
            AND timestamp >= %(start_time)s
            AND timestamp < %(end_time)s
        WHERE username != ''
        ORDER BY timestamp ASC, username ASC, id ASC
        LIMIT %(limit)s
        """
        rows = self._execute_query(sql, parameters)
        return [self._target_row_to_log(row) for row in rows]

    def validation_result_to_row(self, result: UebaValidationResult) -> dict[str, Any]:
        """Convert a validation result dataclass to a ClickHouse insert row."""
        return {
            "validation_id": result.validation_id,
            "source_log_id": result.source_log_id,
            "timestamp": result.timestamp,
            "username": result.username,
            "log_type": result.log_type,
            "request_id": result.request_id,
            "baseline_model_version": result.baseline_model_version,
            "baseline_created_at": result.baseline_created_at,
            "baseline_is_reliable": 1 if result.baseline_is_reliable else 0,
            "ueba_score": self._clamp_score(result.ueba_score),
            "ueba_risk_level": result.ueba_risk_level,
            "ueba_anomaly_reasons": self._reasons_to_json(result.ueba_anomaly_reasons),
            "validation_status": result.validation_status,
            "validated_at": result.validated_at,
            "error": result.error,
        }

    def save_validation_results(self, results: list[UebaValidationResult]) -> int:
        """Batch insert validation results and return the written row count."""
        if not results:
            return 0

        self.ensure_table()
        rows = [self.validation_result_to_row(result) for result in results]
        written_count = 0

        for start in range(0, len(rows), self.write_batch_size):
            batch = rows[start : start + self.write_batch_size]
            insert_rows = [[row[column] for column in self.COLUMNS] for row in batch]
            self.client.insert(
                self.TABLE_NAME,
                insert_rows,
                column_names=self.COLUMNS,
                database=self.database,
            )
            written_count += len(batch)

        return written_count

    def _execute_command(self, sql: str) -> None:
        """Execute a ClickHouse command with common client interfaces."""
        if hasattr(self.client, "command"):
            self.client.command(sql)
            return
        if hasattr(self.client, "execute"):
            self.client.execute(sql)
            return
        raise TypeError("client must provide command(...) or execute(...)")

    def _execute_query(self, sql: str, parameters: dict[str, Any]) -> list[dict[str, Any]]:
        """Execute a ClickHouse query and return normalized row dictionaries."""
        if hasattr(self.client, "query"):
            result = self.client.query(sql, parameters=parameters)
        elif hasattr(self.client, "execute"):
            result = self.client.execute(sql, parameters)
        else:
            raise TypeError("client must provide query(...) or execute(...)")
        return self._rows_to_dicts(result)

    def _rows_to_dicts(self, result: Any) -> list[dict[str, Any]]:
        """Normalize common ClickHouse client query result shapes."""
        if hasattr(result, "named_results"):
            named_results = result.named_results
            rows = named_results() if callable(named_results) else named_results
            return [dict(row) for row in rows]

        if hasattr(result, "result_rows") and hasattr(result, "column_names"):
            return [dict(zip(result.column_names, row)) for row in result.result_rows]

        if isinstance(result, list):
            return self._list_rows_to_dicts(result)

        if isinstance(result, Iterable) and not isinstance(result, (str, bytes, dict)):
            return self._list_rows_to_dicts(list(result))

        raise TypeError("unsupported query result format")

    def _list_rows_to_dicts(self, rows: list[Any]) -> list[dict[str, Any]]:
        """Convert list results made of dict rows or target-log tuples."""
        if not rows:
            return []
        if all(isinstance(row, dict) for row in rows):
            return [dict(row) for row in rows]
        if all(isinstance(row, tuple) for row in rows):
            return [dict(zip(self.TARGET_LOG_COLUMNS, row)) for row in rows]
        raise TypeError("unsupported query result format")

    def _target_row_to_log(self, row: dict[str, Any]) -> ValidationTargetLog:
        """Convert one target-log row to the stable validation schema."""
        return ValidationTargetLog(
            id=int(row["id"]),
            timestamp=str(row["timestamp"]),
            username=str(row["username"]),
            log_type=str(row.get("log_type") or "vpn"),
            source_ip=row.get("source_ip"),
            destination_ip=row.get("destination_ip"),
            src_country=row.get("src_country"),
            src_city=row.get("src_city"),
            vpn_gateway=row.get("vpn_gateway"),
            action=row.get("action"),
            event_type=row.get("event_type"),
            result=row.get("result"),
            fail_reason=row.get("fail_reason"),
            auth_method=row.get("auth_method"),
            client_software=row.get("client_software"),
            protocol=row.get("protocol"),
            is_off_hours=self._to_optional_bool(row.get("is_off_hours")),
            is_unusual_ip=self._to_optional_bool(row.get("is_unusual_ip")),
            request_id=row.get("request_id"),
            raw_log=row.get("raw_log"),
        )

    def _qualified_table(self) -> str:
        """Return the database-qualified fixed result table name."""
        return f"{self.database}.{self.TABLE_NAME}"

    def _qualified_source_table(self) -> str:
        """Return the database-qualified fixed source table name."""
        return f"{self.database}.{self.SOURCE_TABLE}"

    def _validate_identifier(self, identifier: str) -> str:
        """Restrict database identifiers to avoid injecting SQL fragments."""
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", identifier):
            raise ValueError(f"invalid ClickHouse identifier: {identifier}")
        return identifier

    def _validate_batch_size(self, batch_size: int) -> int:
        """Validate the insert batch size."""
        if not isinstance(batch_size, int) or batch_size <= 0:
            raise ValueError("write_batch_size must be a positive integer")
        return batch_size

    def _validate_limit(self, limit: int) -> int:
        """Validate target-log read limit."""
        if not isinstance(limit, int) or limit <= 0:
            raise ValueError("limit must be a positive integer")
        return limit

    def _validate_time_window(self, start_time: str, end_time: str) -> None:
        """Validate the target-log time window."""
        if start_time >= end_time:
            raise ValueError("start_time must be earlier than end_time")

    def _clamp_score(self, score: int) -> int:
        """Clamp UEBA score into the UInt8-friendly 0-100 range."""
        value = int(score)
        return max(0, min(100, value))

    def _reasons_to_json(self, reasons: list[ScoreReason]) -> str:
        """Serialize score reasons using the project JSON convention."""
        payload = [asdict(reason) if is_dataclass(reason) else reason for reason in reasons]
        return json.dumps(payload, ensure_ascii=False, default=str)

    def _to_optional_bool(self, value: Any) -> bool | None:
        """Normalize nullable ClickHouse Bool / UInt8 values."""
        if value is None:
            return None
        if isinstance(value, bool):
            return value
        if isinstance(value, int):
            return bool(value)
        if isinstance(value, str):
            normalized = value.strip().lower()
            if normalized in {"", "none", "null"}:
                return None
            if normalized in {"1", "true", "yes"}:
                return True
            if normalized in {"0", "false", "no"}:
                return False
        return bool(value)


__all__ = ["UebaValidationRepository"]
