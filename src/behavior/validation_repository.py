"""UEBA validation repository.

This module reads controlled target logs and manages the append-only UEBA
validation result table. It does not read training data, change baselines,
update source logs, or implement scoring.
"""

from collections.abc import Iterable
from datetime import datetime, timezone
from dataclasses import asdict, is_dataclass
import json
import re
from typing import Any

from .validation_schemas import ScoreReason, UebaValidationResult, ValidationTargetLog


class UebaValidationRepository:
    """Persist UEBA validation results into ClickHouse."""

    TABLE_NAME = "ueba_validation_results"
    SOURCE_TABLE = "logs_structured"
    NO_BASELINE_MODEL_VERSION = "__NO_BASELINE__"
    COLUMNS = [
        "validation_id",
        "validation_run_id",
        "source_identity",
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
    QUERY_COLUMNS = [
        "validation_id",
        "validation_run_id",
        "source_identity",
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
        "created_at",
    ]
    DATETIME_QUERY_COLUMNS = {
        "timestamp",
        "baseline_created_at",
        "validated_at",
        "created_at",
    }
    SOURCE_LOG_DETAIL_COLUMNS = [
        "id",
        "source_ip",
        "destination_ip",
        "src_country",
        "src_city",
        "vpn_gateway",
        "auth_method",
        "client_software",
        "protocol",
        "raw_log",
    ]
    SOURCE_LOG_MAX_LOOKUP = 1000
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
            validation_run_id String,
            source_identity String,
            source_log_id UInt64,
            timestamp DateTime,
            username String,
            log_type String,
            request_id Nullable(String),
            baseline_model_version String,
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
        ORDER BY (baseline_model_version, validation_run_id, log_type, timestamp, username, source_identity)
        """
        self._execute_command(sql)

    def fetch_target_logs(
        self,
        start_time: str,
        end_time: str,
        log_type: str = "vpn",
        limit: int = 1000,
        exclude_already_validated: bool = False,
        baseline_model_version: str | None = None,
        require_baseline: bool = False,
    ) -> list[ValidationTargetLog]:
        """Fetch structured source logs for later UEBA validation."""
        self._validate_time_window(start_time, end_time)
        parameters = {
            "start_time": start_time,
            "end_time": end_time,
            "log_type": log_type,
            "limit": self._validate_limit(limit),
        }
        filters = ["username != ''"]
        if exclude_already_validated or require_baseline:
            self._validate_required_text(
                baseline_model_version or "",
                "baseline_model_version",
            )
            parameters["baseline_model_version"] = baseline_model_version

        if exclude_already_validated:
            filters.extend(
                [
                    "id > 0",
                    f"""
                    id NOT IN
                    (
                        SELECT source_log_id
                        FROM {self._qualified_table()}
                        WHERE baseline_model_version = %(baseline_model_version)s
                            AND source_log_id > 0
                    )
                    """,
                ]
            )

        if require_baseline:
            filters.append(
                f"""
                username IN
                (
                    SELECT username
                    FROM {self.database}.user_behavior_baselines
                    WHERE model_version = %(baseline_model_version)s
                        AND is_reliable = 1
                )
                """
            )

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
        WHERE {" AND ".join(filters)}
        ORDER BY timestamp ASC, username ASC, id ASC
        LIMIT %(limit)s
        """
        rows = self._execute_query(sql, parameters)
        return [self._target_row_to_log(row) for row in rows]

    def validation_result_to_row(self, result: UebaValidationResult) -> dict[str, Any]:
        """Convert a validation result dataclass to a ClickHouse insert row."""
        return {
            "validation_id": result.validation_id,
            "validation_run_id": result.validation_run_id,
            "source_identity": result.source_identity,
            "source_log_id": int(result.source_log_id or 0),
            "timestamp": self._to_clickhouse_datetime(
                result.timestamp,
                field_name="timestamp",
            ),
            "username": result.username,
            "log_type": result.log_type,
            "request_id": result.request_id,
            "baseline_model_version": (
                result.baseline_model_version or self.NO_BASELINE_MODEL_VERSION
            ),
            "baseline_created_at": self._to_clickhouse_datetime(
                result.baseline_created_at,
                field_name="baseline_created_at",
                nullable=True,
            ),
            "baseline_is_reliable": 1 if result.baseline_is_reliable else 0,
            "ueba_score": self._clamp_score(result.ueba_score),
            "ueba_risk_level": result.ueba_risk_level,
            "ueba_anomaly_reasons": self._reasons_to_json(result.ueba_anomaly_reasons),
            "validation_status": result.validation_status,
            "validated_at": self._to_clickhouse_datetime(
                result.validated_at,
                field_name="validated_at",
            ),
            "error": result.error,
        }

    def save_validation_results(self, results: list[UebaValidationResult]) -> int:
        """Batch insert validation results and return the written row count."""
        if not results:
            return 0

        self.ensure_table()
        rows = [self.validation_result_to_row(result) for result in results]
        rows = self._new_validation_rows(rows)
        if not rows:
            return 0

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

    def query_validation_results(
        self,
        start_time: str,
        end_time: str,
        model_version: str,
        log_type: str = "vpn",
        risk_level: str | None = None,
        validation_status: str | None = None,
        username: str | None = None,
        validation_run_id: str | None = None,
        source_identity: str | None = None,
        limit: int = 1000,
    ) -> list[dict[str, Any]]:
        """Read validation results from the fixed result table."""
        self._validate_time_window(start_time, end_time)
        self._validate_required_text(model_version, "model_version")
        self._validate_required_text(log_type, "log_type")

        parameters: dict[str, Any] = {
            "start_time": start_time,
            "end_time": end_time,
            "model_version": model_version,
            "log_type": log_type,
        }
        filters = [
            "baseline_model_version = %(model_version)s",
            "log_type = %(log_type)s",
            "timestamp >= %(start_time)s",
            "timestamp < %(end_time)s",
        ]
        if risk_level is not None:
            filters.append("ueba_risk_level = %(risk_level)s")
            parameters["risk_level"] = risk_level
        if validation_status is not None:
            filters.append("validation_status = %(validation_status)s")
            parameters["validation_status"] = validation_status
        if username is not None:
            filters.append("username = %(username)s")
            parameters["username"] = username
        if validation_run_id is not None:
            filters.append("validation_run_id = %(validation_run_id)s")
            parameters["validation_run_id"] = validation_run_id
        if source_identity is not None:
            filters.append("source_identity = %(source_identity)s")
            parameters["source_identity"] = source_identity

        sql = f"""
        SELECT
            validation_id,
            validation_run_id,
            source_identity,
            source_log_id,
            timestamp,
            username,
            log_type,
            request_id,
            baseline_model_version,
            baseline_created_at,
            baseline_is_reliable,
            ueba_score,
            ueba_risk_level,
            ueba_anomaly_reasons,
            validation_status,
            validated_at,
            error,
            created_at
        FROM {self._qualified_table()}
        WHERE {" AND ".join(filters)}
        ORDER BY timestamp ASC, username ASC, source_log_id ASC
        LIMIT {self._validate_limit(limit)}
        """
        rows = self._execute_query(sql, parameters, fallback_columns=self.QUERY_COLUMNS)
        return [self._validation_row_to_dict(row) for row in rows]

    # ------------------------------------------------------------------
    # 只读查询方法
    # ------------------------------------------------------------------

    def get_latest_validation_context(
        self,
        *,
        log_type: str = "vpn",
        validation_run_id: str | None = None,
    ) -> dict[str, Any] | None:
        """返回最新 validation batch 的上下文信息。"""
        if validation_run_id is not None:
            sql = f"""
            SELECT validation_run_id, baseline_model_version, max(validated_at) AS latest_validated_at
            FROM {self._qualified_table()}
            WHERE validation_run_id = %(run_id)s
            GROUP BY validation_run_id, baseline_model_version
            ORDER BY latest_validated_at DESC
            LIMIT 1
            """
            rows = self._execute_query(sql, {"run_id": validation_run_id})
        else:
            sql = f"""
            SELECT validation_run_id, baseline_model_version, max(validated_at) AS latest_validated_at
            FROM {self._qualified_table()}
            WHERE log_type = %(log_type)s
            GROUP BY validation_run_id, baseline_model_version
            ORDER BY latest_validated_at DESC
            LIMIT 1
            """
            rows = self._execute_query(sql, {"log_type": log_type})

        if not rows:
            return None
        row = rows[0]
        return {
            "validation_run_id": str(row["validation_run_id"]),
            "baseline_model_version": str(row["baseline_model_version"]),
            "latest_validated_at": self._format_unlabeled_datetime(row["latest_validated_at"]),
        }

    def query_recent_risk_events(
        self,
        *,
        start_time: str,
        end_time: str,
        model_version: str,
        log_type: str = "vpn",
        validation_run_id: str | None = None,
        username: str | None = None,
        risk_level: str | None = None,
        validation_status: str | None = None,
        limit: int = 20,
    ) -> list[dict[str, Any]]:
        """查询近期风险事件，默认排除 NO_BASELINE 和完全正常事件。"""
        self._validate_time_window(start_time, end_time)
        safe_limit = min(limit, 1000)

        parameters: dict[str, Any] = {
            "start_time": start_time,
            "end_time": end_time,
            "model_version": model_version,
            "log_type": log_type,
        }
        filters = [
            "baseline_model_version = %(model_version)s",
            "log_type = %(log_type)s",
            "timestamp >= %(start_time)s",
            "timestamp < %(end_time)s",
            "validation_status != 'NO_BASELINE'",
            "("
            "  length(ueba_anomaly_reasons) > 2"
            "  OR ueba_risk_level IN ('MEDIUM', 'HIGH', 'CRITICAL')"
            "  OR validation_status != 'VALIDATED'"
            ")",
        ]
        if validation_run_id is not None:
            filters.append("validation_run_id = %(run_id)s")
            parameters["run_id"] = validation_run_id
        if username is not None:
            filters.append("username = %(username)s")
            parameters["username"] = username
        if risk_level is not None:
            filters.append("ueba_risk_level = %(risk_level)s")
            parameters["risk_level"] = risk_level
        if validation_status is not None:
            filters.append("validation_status = %(validation_status)s")
            parameters["validation_status"] = validation_status

        column_list = ", ".join(self.QUERY_COLUMNS)
        sql = f"""
        SELECT {column_list}
        FROM {self._qualified_table()}
        WHERE {" AND ".join(filters)}
        ORDER BY timestamp DESC, validated_at DESC, username ASC, source_log_id ASC
        LIMIT {safe_limit}
        """
        rows = self._execute_query(sql, parameters, fallback_columns=self.QUERY_COLUMNS)
        return [self._validation_row_to_dict(row) for row in rows]

    def query_validation_events_ordered(
        self,
        *,
        start_time: str,
        end_time: str,
        model_version: str,
        log_type: str = "vpn",
        validation_run_id: str | None = None,
        username: str | None = None,
        risk_level: str | None = None,
        validation_status: str | None = None,
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        """通用 validation 事件查询（倒序），不自动排除 NO_BASELINE 或 normal。"""
        self._validate_time_window(start_time, end_time)
        safe_limit = min(limit, 1000)

        parameters: dict[str, Any] = {
            "start_time": start_time,
            "end_time": end_time,
            "model_version": model_version,
            "log_type": log_type,
        }
        filters = [
            "baseline_model_version = %(model_version)s",
            "log_type = %(log_type)s",
            "timestamp >= %(start_time)s",
            "timestamp < %(end_time)s",
        ]
        if validation_run_id is not None:
            filters.append("validation_run_id = %(run_id)s")
            parameters["run_id"] = validation_run_id
        if username is not None:
            filters.append("username = %(username)s")
            parameters["username"] = username
        if risk_level is not None:
            filters.append("ueba_risk_level = %(risk_level)s")
            parameters["risk_level"] = risk_level
        if validation_status is not None:
            filters.append("validation_status = %(validation_status)s")
            parameters["validation_status"] = validation_status

        column_list = ", ".join(self.QUERY_COLUMNS)
        sql = f"""
        SELECT {column_list}
        FROM {self._qualified_table()}
        WHERE {" AND ".join(filters)}
        ORDER BY timestamp DESC, validated_at DESC, username ASC, source_log_id ASC
        LIMIT {safe_limit}
        """
        rows = self._execute_query(sql, parameters, fallback_columns=self.QUERY_COLUMNS)
        return [self._validation_row_to_dict(row) for row in rows]

    def fetch_source_log_details(
        self,
        source_log_ids: list[int],
    ) -> dict[int, list[dict[str, Any]]]:
        """批量回查 logs_structured 的增强字段。

        同一 id 匹配多行时全部保留为列表，由 API 层判断唯一性。
        """
        positive_ids = [i for i in source_log_ids if isinstance(i, int) and i > 0]
        unique_ids = list(set(positive_ids))

        if not unique_ids:
            return {}

        safe_ids = unique_ids[: self.SOURCE_LOG_MAX_LOOKUP]

        columns_to_read = [c for c in self.SOURCE_LOG_DETAIL_COLUMNS if c != "raw_log"]
        column_list = ", ".join(columns_to_read)

        sql = f"""
        SELECT {column_list},
            length(ifNull(raw_log, '')) > 0 AS raw_log_available
        FROM {self._qualified_source_table()}
        WHERE id IN %(source_log_ids)s
        """
        rows = self._execute_query(sql, {"source_log_ids": safe_ids})
        result: dict[int, list[dict[str, Any]]] = {}
        for row in rows:
            row_id = int(row.get("id", 0))
            if row_id > 0:
                detail = {
                    "source_ip": row.get("source_ip"),
                    "destination_ip": row.get("destination_ip"),
                    "src_country": row.get("src_country"),
                    "src_city": row.get("src_city"),
                    "vpn_gateway": row.get("vpn_gateway"),
                    "auth_method": row.get("auth_method"),
                    "client_software": row.get("client_software"),
                    "protocol": row.get("protocol"),
                    "raw_log_available": bool(row.get("raw_log_available")),
                }
                result.setdefault(row_id, []).append(detail)
        return result

    def _new_validation_rows(self, rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """按逻辑唯一键过滤已存在或本批重复的 validation 行。"""
        existing_keys = self._fetch_existing_validation_keys(rows)
        seen_keys: set[tuple[str, int]] = set()
        new_rows: list[dict[str, Any]] = []

        for row in rows:
            key = self._validation_unique_key(row)
            if key is None:
                continue
            if key in existing_keys or key in seen_keys:
                continue
            seen_keys.add(key)
            new_rows.append(row)

        return new_rows

    def _fetch_existing_validation_keys(
        self,
        rows: list[dict[str, Any]],
    ) -> set[tuple[str, int]]:
        """批量查询已存在的 baseline_model_version + source_log_id。"""
        keys = {
            key
            for row in rows
            if (key := self._validation_unique_key(row)) is not None
        }
        if not keys:
            return set()

        parameters = {
            "baseline_model_versions": sorted({model_version for model_version, _ in keys}),
            "source_log_ids": sorted({source_log_id for _, source_log_id in keys}),
        }
        sql = f"""
        SELECT
            baseline_model_version,
            source_log_id
        FROM {self._qualified_table()}
        WHERE baseline_model_version IN %(baseline_model_versions)s
            AND source_log_id IN %(source_log_ids)s
        GROUP BY
            baseline_model_version,
            source_log_id
        """
        existing_rows = self._execute_query(
            sql,
            parameters,
            fallback_columns=["baseline_model_version", "source_log_id"],
        )
        return {
            (str(row["baseline_model_version"]), int(row["source_log_id"]))
            for row in existing_rows
            if row.get("baseline_model_version") is not None
            and int(row.get("source_log_id") or 0) > 0
        }

    def _validation_unique_key(self, row: dict[str, Any]) -> tuple[str, int] | None:
        """生成写入幂等使用的逻辑唯一键。"""
        source_log_id = int(row.get("source_log_id") or 0)
        if source_log_id <= 0:
            return None
        baseline_model_version = str(row.get("baseline_model_version") or "").strip()
        if not baseline_model_version:
            return None
        return baseline_model_version, source_log_id

    def fetch_validation_ranking(
        self,
        *,
        start_time: str,
        end_time: str,
        model_version: str,
        log_type: str = "vpn",
        validation_run_id: str | None = None,
        risk_level: str | None = None,
        validation_status: str | None = None,
        username: str | None = None,
        limit: int = 20,
    ) -> list[dict[str, Any]]:
        """在数据库侧按 username 聚合 validation 排行。

        完整窗口聚合后再截断，不在事件层截断。
        """
        self._validate_time_window(start_time, end_time)
        validated_limit = self._validate_limit(limit)
        safe_limit = min(validated_limit, 1000)

        parameters: dict[str, Any] = {
            "start_time": start_time,
            "end_time": end_time,
            "model_version": model_version,
            "log_type": log_type,
        }
        filters = [
            "baseline_model_version = %(model_version)s",
            "log_type = %(log_type)s",
            "timestamp >= %(start_time)s",
            "timestamp < %(end_time)s",
        ]
        if validation_run_id is not None:
            filters.append("validation_run_id = %(run_id)s")
            parameters["run_id"] = validation_run_id
        if risk_level is not None:
            filters.append("ueba_risk_level = %(risk_level)s")
            parameters["risk_level"] = risk_level
        if validation_status is not None:
            filters.append("validation_status = %(validation_status)s")
            parameters["validation_status"] = validation_status
        if username is not None:
            filters.append("username = %(username)s")
            parameters["username"] = username

        sql = f"""
        SELECT
            username,
            max(ueba_score) AS max_score,
            avg(ueba_score) AS avg_score,
            count() AS event_count,
            countIf(ueba_risk_level = 'HIGH') AS high_risk_count,
            countIf(ueba_risk_level = 'CRITICAL') AS critical_count,
            max(validated_at) AS latest_validated_at,
            argMax(validation_run_id, validated_at) AS latest_validation_run_id,
            multiIf(
                countIf(ueba_risk_level = 'CRITICAL') > 0, 'CRITICAL',
                countIf(ueba_risk_level = 'HIGH') > 0, 'HIGH',
                countIf(ueba_risk_level = 'MEDIUM') > 0, 'MEDIUM',
                'LOW'
            ) AS overall_risk
        FROM {self._qualified_table()}
        WHERE {" AND ".join(filters)}
        GROUP BY username
        ORDER BY max_score DESC, critical_count DESC, high_risk_count DESC, event_count DESC, username ASC
        LIMIT {safe_limit}
        """
        return self._execute_query(sql, parameters)

    def fetch_validation_summary(
        self,
        *,
        start_time: str,
        end_time: str,
        model_version: str,
        log_type: str = "vpn",
        validation_run_id: str | None = None,
    ) -> dict[str, Any] | None:
        """在数据库侧聚合 validation 摘要，不依赖事件列表 limit。"""
        self._validate_time_window(start_time, end_time)

        parameters: dict[str, Any] = {
            "start_time": start_time,
            "end_time": end_time,
            "model_version": model_version,
            "log_type": log_type,
        }
        filters = [
            "baseline_model_version = %(model_version)s",
            "log_type = %(log_type)s",
            "timestamp >= %(start_time)s",
            "timestamp < %(end_time)s",
        ]
        if validation_run_id is not None:
            filters.append("validation_run_id = %(run_id)s")
            parameters["run_id"] = validation_run_id

        sql = f"""
        SELECT
            count() AS total,
            countIf(ueba_risk_level = 'LOW') AS risk_low,
            countIf(ueba_risk_level = 'MEDIUM') AS risk_medium,
            countIf(ueba_risk_level = 'HIGH') AS risk_high,
            countIf(ueba_risk_level = 'CRITICAL') AS risk_critical,
            countIf(validation_status = 'VALIDATED') AS status_validated,
            countIf(validation_status = 'NO_BASELINE') AS status_no_baseline,
            countIf(validation_status = 'UNRELIABLE_BASELINE') AS status_unreliable,
            countIf(validation_status = 'ERROR') AS status_error,
            max(ueba_score) AS max_score,
            avgOrDefault(ueba_score) AS avg_score,
            max(validated_at) AS latest_validated_at,
            argMax(validation_run_id, validated_at) AS latest_validation_run_id
        FROM {self._qualified_table()}
        WHERE {" AND ".join(filters)}
        """
        rows = self._execute_query(sql, parameters)
        if not rows:
            return None
        return rows[0]

    def _execute_command(self, sql: str) -> None:
        """Execute a ClickHouse command with common client interfaces."""
        if hasattr(self.client, "command"):
            self.client.command(sql)
            return
        if hasattr(self.client, "execute"):
            self.client.execute(sql)
            return
        raise TypeError("client must provide command(...) or execute(...)")

    def _execute_query(
        self,
        sql: str,
        parameters: dict[str, Any],
        *,
        fallback_columns: list[str] | None = None,
    ) -> list[dict[str, Any]]:
        """Execute a ClickHouse query and return normalized row dictionaries."""
        if hasattr(self.client, "query"):
            result = self.client.query(sql, parameters=parameters)
        elif hasattr(self.client, "execute"):
            result = self.client.execute(sql, parameters)
        else:
            raise TypeError("client must provide query(...) or execute(...)")
        return self._rows_to_dicts(result, fallback_columns=fallback_columns)

    def _rows_to_dicts(
        self,
        result: Any,
        *,
        fallback_columns: list[str] | None = None,
    ) -> list[dict[str, Any]]:
        """Normalize common ClickHouse client query result shapes."""
        if hasattr(result, "named_results"):
            named_results = result.named_results
            rows = named_results() if callable(named_results) else named_results
            return [dict(row) for row in rows]

        if hasattr(result, "result_rows") and hasattr(result, "column_names"):
            return [dict(zip(result.column_names, row)) for row in result.result_rows]

        columns = fallback_columns or self.TARGET_LOG_COLUMNS

        if isinstance(result, list):
            return self._list_rows_to_dicts(result, columns)

        if isinstance(result, Iterable) and not isinstance(result, (str, bytes, dict)):
            return self._list_rows_to_dicts(list(result), columns)

        raise TypeError("unsupported query result format")

    def _list_rows_to_dicts(
        self,
        rows: list[Any],
        columns: list[str],
    ) -> list[dict[str, Any]]:
        """Convert list results made of dict rows or tuples."""
        if not rows:
            return []
        if all(isinstance(row, dict) for row in rows):
            return [dict(row) for row in rows]
        if all(isinstance(row, tuple) for row in rows):
            return [dict(zip(columns, row)) for row in rows]
        raise TypeError("unsupported query result format")

    def _validation_row_to_dict(self, row: dict[str, Any]) -> dict[str, Any]:
        """Convert one validation result row to a stable query dictionary."""
        result = {column: row.get(column) for column in self.QUERY_COLUMNS}
        for column in self.DATETIME_QUERY_COLUMNS:
            result[column] = self._format_unlabeled_datetime(result[column])
        return result

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
        if isinstance(limit, bool) or not isinstance(limit, int) or limit <= 0:
            raise ValueError("limit must be a positive integer")
        return limit

    def _validate_required_text(self, value: str, field_name: str) -> None:
        """Validate required text filters."""
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"{field_name} must be a non-empty string")

    def _validate_time_window(self, start_time: str, end_time: str) -> None:
        """Validate the target-log time window."""
        if start_time >= end_time:
            raise ValueError("start_time must be earlier than end_time")

    def _clamp_score(self, score: int) -> int:
        """Clamp UEBA score into the UInt8-friendly 0-100 range."""
        value = int(score)
        return max(0, min(100, value))

    def _to_clickhouse_datetime(
        self,
        value: Any,
        *,
        field_name: str,
        nullable: bool = False,
    ) -> datetime | None:
        """Encode unlabeled business time for ClickHouse DateTime inserts."""
        parsed = self._parse_unlabeled_datetime(
            value,
            field_name=field_name,
            nullable=nullable,
        )
        if parsed is None:
            return None

        # Project business times are unlabeled wall-clock values. The UTC tzinfo
        # is transport encoding only, so clickhouse-connect does not apply the
        # Python process local timezone when it calls datetime.timestamp().
        return parsed.replace(tzinfo=timezone.utc)

    def _parse_unlabeled_datetime(
        self,
        value: Any,
        *,
        field_name: str,
        nullable: bool = False,
    ) -> datetime | None:
        """Parse a project business time without applying timezone conversion."""
        if value is None:
            if nullable:
                return None
            raise ValueError(f"{field_name} is required")

        if isinstance(value, datetime):
            return value.replace(tzinfo=None)

        if isinstance(value, str):
            text = value.strip()
            if not text:
                if nullable:
                    return None
                raise ValueError(f"{field_name} is required")
            text = self._remove_timezone_marker(text).replace("T", " ")
            try:
                parsed = datetime.fromisoformat(text)
            except ValueError as exc:
                raise ValueError(f"{field_name} must be a valid datetime string") from exc
            return parsed.replace(tzinfo=None)

        raise TypeError(f"{field_name} must be datetime or str, got {type(value).__name__}")

    def _remove_timezone_marker(self, value: str) -> str:
        """Drop optional timezone markers while keeping the original wall clock."""
        if value.endswith(("Z", "z")):
            return value[:-1]
        return re.sub(r"[+-]\d{2}:?\d{2}$", "", value)

    def _format_unlabeled_datetime(self, value: Any) -> str | None:
        """Format DateTime query values without timezone labels."""
        if value is None:
            return None
        if isinstance(value, datetime):
            return value.replace(tzinfo=None).strftime("%Y-%m-%d %H:%M:%S")
        return str(value)

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
