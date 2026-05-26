"""ClickHouse loader for UEBA baseline acceptance fixtures."""

from __future__ import annotations

from collections.abc import Callable, Iterable
from datetime import datetime
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any
import re
import time

from .config import AcceptanceConfig
from .fixture_generator import generate_fixture_outputs, iter_fixture_logs
from .report_writer import ensure_output_dir, update_run_state, write_json


LOGS_TABLE = "logs_structured"
LOAD_RESULT_FILE = "load_result.json"
INSERT_COLUMNS = [
    "timestamp",
    "log_type",
    "username",
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
    "session_duration_sec",
    "bytes_sent",
    "bytes_recv",
    "is_off_hours",
    "is_unusual_ip",
    "parser",
    "raw_log",
]


@dataclass(slots=True)
class FixtureLoadResult:
    """Structured result for fixture log loading."""

    success: bool
    fixture_id: str
    expected_rows: int
    inserted_rows: int
    database_rows: int
    duration_seconds: float
    database: str
    table: str
    start_time: str
    end_time: str
    log_type: str
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        """Convert the result to a JSON-ready dict."""
        return asdict(self)


def create_clickhouse_client(config: AcceptanceConfig) -> Any:
    """Create a ClickHouse client and execute SELECT 1 as a connection probe."""
    try:
        import clickhouse_connect
    except ImportError as exc:
        raise RuntimeError(
            "缺少 clickhouse_connect 依赖，请确认 requirements.txt 已安装 clickhouse-connect。"
        ) from exc

    client = clickhouse_connect.get_client(
        host=config.clickhouse_host,
        port=config.clickhouse_port,
        username=config.clickhouse_user,
        password=config.clickhouse_password,
        database=config.clickhouse_database,
    )
    client.command("SELECT 1")
    return client


def validate_identifier(identifier: str) -> str:
    """Validate a ClickHouse database or table identifier."""
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", identifier):
        raise ValueError(f"invalid ClickHouse identifier: {identifier}")
    return identifier


class FixtureClickHouseWriter:
    """Batch writer for generated fixture logs."""

    def __init__(self, client: Any, database: str = "log_analysis", batch_size: int = 1000) -> None:
        """Initialize the writer with an external ClickHouse client."""
        if not isinstance(batch_size, int) or batch_size <= 0:
            raise ValueError("batch_size must be a positive integer")
        self.client = client
        self.database = validate_identifier(database)
        self.table = LOGS_TABLE
        self.batch_size = batch_size

    def clean_fixture_logs(self, config: AcceptanceConfig) -> None:
        """Delete only fixture rows within the configured window and log_type."""
        sql = f"""
        ALTER TABLE {self.database}.{self.table}
        DELETE
        WHERE username LIKE 'fixture_user_%%'
          AND log_type = %(log_type)s
          AND timestamp >= %(start_time)s
          AND timestamp < %(end_time)s
        SETTINGS mutations_sync = 1
        """
        self._execute_command(sql, _base_parameters(config))

    def insert_logs(self, rows: Iterable[dict[str, Any]]) -> int:
        """Insert fixture logs using explicit column names and configured batches."""
        inserted_rows = 0
        batch: list[list[Any]] = []
        for row in rows:
            batch.append([_column_value(row, column) for column in INSERT_COLUMNS])
            if len(batch) >= self.batch_size:
                self._insert_batch(batch)
                inserted_rows += len(batch)
                batch = []

        if batch:
            self._insert_batch(batch)
            inserted_rows += len(batch)
        return inserted_rows

    def count_fixture_logs(self, config: AcceptanceConfig) -> int:
        """Count fixture rows for this fixture window in ClickHouse."""
        sql = f"""
        SELECT count() AS cnt
        FROM {self.database}.{self.table}
        WHERE username LIKE 'fixture_user_%%'
          AND log_type = %(log_type)s
          AND timestamp >= %(start_time)s
          AND timestamp < %(end_time)s
        """
        return int(_scalar_query(self.client, sql, _base_parameters(config)))

    def close(self) -> None:
        """Close the underlying client when it supports close()."""
        if hasattr(self.client, "close"):
            self.client.close()

    def _insert_batch(self, batch: list[list[Any]]) -> None:
        self.client.insert(
            self.table,
            batch,
            column_names=INSERT_COLUMNS,
            database=self.database,
        )

    def _execute_command(self, sql: str, parameters: dict[str, Any]) -> None:
        if hasattr(self.client, "command"):
            self.client.command(sql, parameters=parameters)
            return
        if hasattr(self.client, "execute"):
            self.client.execute(sql, parameters)
            return
        raise TypeError("client must provide command(...) or execute(...)")


def load_fixture_to_clickhouse(
    config: AcceptanceConfig,
    client_factory: Callable[[AcceptanceConfig], Any] = create_clickhouse_client,
) -> dict[str, Any]:
    """Generate expected files, load fixture logs into ClickHouse, and write reports."""
    begin = time.time()
    output_dir = ensure_output_dir(config)
    fixture_state = generate_fixture_outputs(config)
    expected_rows = int(fixture_state["total_logs"])
    client = None
    writer: FixtureClickHouseWriter | None = None
    inserted_rows = 0
    database_rows = 0
    error: str | None = None

    try:
        client = client_factory(config)
        writer = FixtureClickHouseWriter(
            client=client,
            database=config.clickhouse_database,
            batch_size=config.clickhouse_batch_size,
        )
        if config.clean_before_load:
            writer.clean_fixture_logs(config)
        inserted_rows = writer.insert_logs(iter_fixture_logs(config))
        database_rows = writer.count_fixture_logs(config)
        success = inserted_rows == expected_rows and database_rows == expected_rows
        if not success:
            error = (
                f"row count mismatch: expected_rows={expected_rows}, "
                f"inserted_rows={inserted_rows}, database_rows={database_rows}"
            )
    except Exception as exc:
        success = False
        error = f"{type(exc).__name__}: {exc}"
    finally:
        if writer is not None:
            try:
                writer.close()
            except Exception:
                pass
        elif client is not None and hasattr(client, "close"):
            try:
                client.close()
            except Exception:
                pass

    result = FixtureLoadResult(
        success=success,
        fixture_id=config.fixture_id,
        expected_rows=expected_rows,
        inserted_rows=inserted_rows,
        database_rows=database_rows,
        duration_seconds=round(time.time() - begin, 3),
        database=config.clickhouse_database,
        table=LOGS_TABLE,
        start_time=config.start_time,
        end_time=config.end_time,
        log_type=config.log_type,
        error=error,
    ).to_dict()
    write_json(output_dir / LOAD_RESULT_FILE, result)
    update_run_state(
        config,
        expected_generated=True,
        clickhouse_loaded=bool(result["success"]),
        baseline_built=False,
        comparison_done=False,
        fixture_logs_path=None,
        load_result_path=str(output_dir / LOAD_RESULT_FILE),
        inserted_rows=inserted_rows,
        database_rows=database_rows,
        load_error=error,
    )
    return result


def _base_parameters(config: AcceptanceConfig) -> dict[str, Any]:
    return {
        "start_time": config.start_time,
        "end_time": config.end_time,
        "log_type": config.log_type,
    }


def _column_value(row: dict[str, Any], column: str) -> Any:
    value = row[column]
    if column == "timestamp" and isinstance(value, str):
        naive_time = datetime.strptime(value, "%Y-%m-%d %H:%M:%S")
        local_offset = datetime.now().astimezone().utcoffset()
        return naive_time + local_offset if local_offset else naive_time
    return value


def _scalar_query(client: Any, sql: str, parameters: dict[str, Any]) -> Any:
    if hasattr(client, "query"):
        result = client.query(sql, parameters=parameters)
    elif hasattr(client, "execute"):
        result = client.execute(sql, parameters)
    else:
        raise TypeError("client must provide query(...) or execute(...)")

    if hasattr(result, "result_rows") and result.result_rows:
        return result.result_rows[0][0]
    if hasattr(result, "named_results"):
        rows = result.named_results() if callable(result.named_results) else result.named_results
        if rows:
            return rows[0].get("cnt")
    if isinstance(result, list) and result:
        first = result[0]
        if isinstance(first, dict):
            return first.get("cnt")
        if isinstance(first, (list, tuple)):
            return first[0]
    return 0


__all__ = [
    "FixtureClickHouseWriter",
    "FixtureLoadResult",
    "INSERT_COLUMNS",
    "create_clickhouse_client",
    "load_fixture_to_clickhouse",
    "validate_identifier",
]
