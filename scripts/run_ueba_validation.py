"""UEBA validation CLI entry point.

The script only parses arguments, creates the ClickHouse client, calls
UebaValidationService.run, and prints a JSON payload.
"""

import argparse
import json
import os
from pathlib import Path
import sys
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.behavior.baseline_store import BaselineStore
from src.behavior.validation_repository import UebaValidationRepository
from src.behavior.validation_service import UebaValidationService


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Parse UEBA validation CLI arguments."""
    parser = argparse.ArgumentParser(
        description="Run UEBA baseline validation and print a JSON result.",
    )
    parser.add_argument("--host", default=os.getenv("CLICKHOUSE_HOST", "localhost"))
    parser.add_argument("--port", type=int, default=_env_int("CLICKHOUSE_PORT", 8123))
    parser.add_argument("--username", default=os.getenv("CLICKHOUSE_USER", "default"))
    parser.add_argument("--password", default=os.getenv("CLICKHOUSE_PASSWORD", ""))
    parser.add_argument("--database", default=os.getenv("CLICKHOUSE_DATABASE", "log_analysis"))
    parser.add_argument("--secure", action="store_true", default=False)

    parser.add_argument("--start-time", required=True)
    parser.add_argument("--end-time", required=True)
    parser.add_argument("--log-type", default="vpn")
    parser.add_argument("--model-version", required=True)
    parser.add_argument("--limit", type=_positive_int, default=1000)
    parser.add_argument("--sample-size", type=_positive_int, default=5)
    parser.add_argument("--write", action="store_true", default=False)
    parser.add_argument("--dry-run", action="store_true", default=False)

    args = parser.parse_args(argv)
    if args.write and args.dry_run:
        parser.error("--write and --dry-run cannot be used together")
    return args


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


def build_service(client: Any, args: argparse.Namespace) -> UebaValidationService:
    """Build validation service collaborators from CLI args."""
    repository = UebaValidationRepository(client=client, database=args.database)
    baseline_store = BaselineStore(client=client, database=args.database)
    return UebaValidationService(
        validation_repository=repository,
        baseline_store=baseline_store,
    )


def run_validation(args: argparse.Namespace, service: UebaValidationService) -> dict[str, Any]:
    """Run validation through the service and return its summary."""
    return service.run(
        start_time=args.start_time,
        end_time=args.end_time,
        log_type=args.log_type,
        model_version=args.model_version,
        limit=args.limit,
        dry_run=not args.write,
        sample_size=args.sample_size,
    )


def result_to_json(payload: dict[str, Any]) -> str:
    """Serialize CLI payload."""
    return json.dumps(payload, ensure_ascii=False, default=str)


def failure_payload(message: str, args: argparse.Namespace | None = None) -> dict[str, Any]:
    """Build a failure payload without exposing credentials."""
    safe_message = _redact_password(message, args)
    return {
        "success": False,
        "processed_count": 0,
        "selected_count": 0,
        "scored_count": 0,
        "written_count": 0,
        "skipped_count": 0,
        "no_baseline_count": 0,
        "unreliable_baseline_count": 0,
        "failed_count": 0,
        "dry_run": None if args is None else not getattr(args, "write", False),
        "risk_level_counts": {},
        "validation_status_counts": {},
        "sample_results": [],
        "start_time": getattr(args, "start_time", None),
        "end_time": getattr(args, "end_time", None),
        "log_type": getattr(args, "log_type", None),
        "model_version": getattr(args, "model_version", None),
        "message": "validation CLI failed",
        "error": safe_message,
    }


def main(argv: list[str] | None = None) -> int:
    """CLI main entry point."""
    args: argparse.Namespace | None = None
    client = None
    try:
        args = parse_args(argv)
        client = create_clickhouse_client(args)
        service = build_service(client, args)
        payload = run_validation(args, service)
        print(result_to_json(payload))
        return 0 if payload.get("success") else 1
    except Exception as exc:
        print(result_to_json(failure_payload(f"{type(exc).__name__}: {exc}", args)))
        return 1
    finally:
        if client is not None and hasattr(client, "close"):
            try:
                client.close()
            except Exception:
                pass


def _redact_password(message: str, args: argparse.Namespace | None) -> str:
    """Redact the configured password from error text."""
    password = getattr(args, "password", None)
    if not password:
        return message
    return message.replace(str(password), "***")


def _positive_int(value: str) -> int:
    """Parse a strictly positive integer."""
    parsed = int(value)
    if parsed <= 0:
        raise argparse.ArgumentTypeError("must be a positive integer")
    return parsed


def _env_int(name: str, default: int) -> int:
    """Read an integer environment variable with fallback."""
    value = os.getenv(name)
    if value is None or value == "":
        return default
    try:
        return int(value)
    except ValueError:
        return default


if __name__ == "__main__":
    raise SystemExit(main())
