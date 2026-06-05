"""Export UEBA validation results as CSV or JSON.

This CLI is read-only: it queries UebaValidationRepository and serializes the
returned result rows. It does not run scoring or modify ClickHouse tables.
"""

import argparse
import csv
import json
import os
from pathlib import Path
import sys
from typing import Any, TextIO

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.behavior.validation_repository import UebaValidationRepository


EXPORT_FIELDS = [
    "validation_id",
    "validation_run_id",
    "source_identity",
    "source_log_id",
    "timestamp",
    "username",
    "log_type",
    "request_id",
    "baseline_model_version",
    "baseline_is_reliable",
    "ueba_score",
    "ueba_risk_level",
    "ueba_anomaly_reasons",
    "validation_status",
    "validated_at",
    "error",
    "created_at",
]


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Parse export CLI arguments."""
    parser = argparse.ArgumentParser(
        description="Export UEBA validation results as CSV or JSON.",
    )
    parser.add_argument("--host", default=os.getenv("CLICKHOUSE_HOST", "localhost"))
    parser.add_argument("--port", type=int, default=_env_int("CLICKHOUSE_PORT", 8123))
    parser.add_argument("--username", default=os.getenv("CLICKHOUSE_USER", "default"))
    parser.add_argument("--password", default=os.getenv("CLICKHOUSE_PASSWORD", ""))
    parser.add_argument("--database", default=os.getenv("CLICKHOUSE_DATABASE", "log_analysis"))
    parser.add_argument("--secure", action="store_true", default=False)

    parser.add_argument("--start-time", required=True)
    parser.add_argument("--end-time", required=True)
    parser.add_argument("--model-version", required=True)
    parser.add_argument("--log-type", default="vpn")
    parser.add_argument("--validation-run-id")
    parser.add_argument("--risk-level")
    parser.add_argument("--validation-status")
    parser.add_argument("--username-filter")
    parser.add_argument("--source-identity")
    parser.add_argument("--limit", type=_positive_int, default=1000)
    parser.add_argument("--format", choices=("csv", "json"), default="csv")
    parser.add_argument("--output")
    return parser.parse_args(argv)


def create_clickhouse_client(args: argparse.Namespace):
    """Create a ClickHouse client lazily so --help does not connect."""
    try:
        import clickhouse_connect
    except ImportError as exc:
        raise RuntimeError(
            "Missing clickhouse_connect dependency. Install clickhouse-connect first."
        ) from exc

    client = clickhouse_connect.get_client(
        host=args.host,
        port=args.port,
        username=args.username,
        password=args.password,
        database=args.database,
        secure=args.secure,
    )
    client.command("SELECT 1")
    return client


def query_results(args: argparse.Namespace, client: Any) -> list[dict[str, Any]]:
    """Query validation results through the repository."""
    repository = UebaValidationRepository(client=client, database=args.database)
    return repository.query_validation_results(
        start_time=args.start_time,
        end_time=args.end_time,
        model_version=args.model_version,
        log_type=args.log_type,
        risk_level=args.risk_level,
        validation_status=args.validation_status,
        username=args.username_filter,
        validation_run_id=args.validation_run_id,
        source_identity=args.source_identity,
        limit=args.limit,
    )


def write_csv(rows: list[dict[str, Any]], stream: TextIO) -> None:
    """Write rows as CSV with a stable header."""
    writer = csv.DictWriter(stream, fieldnames=EXPORT_FIELDS, extrasaction="ignore")
    writer.writeheader()
    for row in rows:
        writer.writerow({field: _csv_value(row.get(field)) for field in EXPORT_FIELDS})


def write_json(rows: list[dict[str, Any]], stream: TextIO) -> None:
    """Write rows as JSON."""
    json.dump(rows, stream, ensure_ascii=False, indent=2, default=str)
    stream.write("\n")


def export_rows(rows: list[dict[str, Any]], args: argparse.Namespace) -> None:
    """Serialize rows to stdout or an output path."""
    if args.output:
        output_path = Path(args.output)
        if output_path.parent != Path(""):
            output_path.parent.mkdir(parents=True, exist_ok=True)
        with output_path.open("w", encoding="utf-8", newline="") as stream:
            _write_rows(rows, args.format, stream)
        return
    _write_rows(rows, args.format, sys.stdout)


def main(argv: list[str] | None = None) -> int:
    """CLI main entry point."""
    args: argparse.Namespace | None = None
    client = None
    try:
        args = parse_args(argv)
        client = create_clickhouse_client(args)
        rows = query_results(args, client)
        export_rows(rows, args)
        return 0
    except Exception as exc:
        message = _redact_password(f"{type(exc).__name__}: {exc}", args)
        print(message, file=sys.stderr)
        return 1
    finally:
        if client is not None and hasattr(client, "close"):
            try:
                client.close()
            except Exception:
                pass


def _write_rows(rows: list[dict[str, Any]], output_format: str, stream: TextIO) -> None:
    if output_format == "json":
        write_json(rows, stream)
        return
    write_csv(rows, stream)


def _csv_value(value: Any) -> Any:
    if value is None:
        return ""
    return value


def _redact_password(message: str, args: argparse.Namespace | None) -> str:
    password = getattr(args, "password", None)
    if not password:
        return message
    return message.replace(str(password), "***")


def _positive_int(value: str) -> int:
    parsed = int(value)
    if parsed <= 0:
        raise argparse.ArgumentTypeError("must be a positive integer")
    return parsed


def _env_int(name: str, default: int) -> int:
    value = os.getenv(name)
    if value is None or value == "":
        return default
    try:
        return int(value)
    except ValueError:
        return default


if __name__ == "__main__":
    raise SystemExit(main())
