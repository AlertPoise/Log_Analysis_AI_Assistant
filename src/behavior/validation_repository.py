"""UEBA validation result repository.

This module manages the append-only UEBA validation result table. It does not
read training data, change baselines, update source logs, or implement scoring.
"""

from dataclasses import asdict, is_dataclass
import json
import re
from typing import Any

from .validation_schemas import ScoreReason, UebaValidationResult


class UebaValidationRepository:
    """Persist UEBA validation results into ClickHouse."""

    TABLE_NAME = "ueba_validation_results"
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

    def _qualified_table(self) -> str:
        """Return the database-qualified fixed result table name."""
        return f"{self.database}.{self.TABLE_NAME}"

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

    def _clamp_score(self, score: int) -> int:
        """Clamp UEBA score into the UInt8-friendly 0-100 range."""
        value = int(score)
        return max(0, min(100, value))

    def _reasons_to_json(self, reasons: list[ScoreReason]) -> str:
        """Serialize score reasons using the project JSON convention."""
        payload = [asdict(reason) if is_dataclass(reason) else reason for reason in reasons]
        return json.dumps(payload, ensure_ascii=False, default=str)


__all__ = ["UebaValidationRepository"]
