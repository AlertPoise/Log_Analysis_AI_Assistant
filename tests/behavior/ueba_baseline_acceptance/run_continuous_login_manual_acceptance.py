"""Manual runner for UEBA continuous login generator acceptance.

This module is an operator-driven acceptance tool. It does not connect to
ClickHouse or start the HTTP server when imported or when invoked with --help.
"""

from __future__ import annotations

import argparse
from collections import Counter
from datetime import date, datetime, timezone
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import time
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from .clickhouse_writer import create_clickhouse_client, validate_identifier
from .config import AcceptanceConfig
from .continuous_login_http_server import DEFAULT_HOST, DEFAULT_PORT


PROJECT_ROOT = Path(__file__).resolve().parents[3]
MARKER = "ueba_continuous_fixture"
DEFAULT_USERNAME = "fixture_user_stable_0001"
DEFAULT_REPORT_PATH = Path(".tox/manual/continuous_login_manual_acceptance_report.json")
DEFAULT_SERVER_LOG_PATH = Path(".tox/manual/continuous_login_http_server.log")
SERVER_MODULE = "tests.behavior.ueba_baseline_acceptance.continuous_login_http_server"
ALLOWED_TABLES = {
    "logs_structured",
    "user_behavior_baselines",
    "ueba_baseline_training_logs",
    "ueba_validation_results",
}
REQUIRED_REPORT_FIELDS = (
    "success",
    "started_at",
    "finished_at",
    "host",
    "port",
    "username",
    "baseline_user_count",
    "counts_before",
    "counts_after",
    "counts_after_cleanup",
    "rate20_delta",
    "rate50_delta",
    "pause_delta",
    "resume_delta",
    "mixed_distribution",
    "quality",
    "table_unchanged",
    "invalid_requests",
    "server_stopped",
    "port_released",
    "cleanup_performed",
    "errors",
)
MODE_NAMES = ("normal", "new_ip", "new_country", "new_city", "failed_login", "off_hours", "combo_anomaly")


class ManualAcceptanceError(RuntimeError):
    """Raised for expected manual acceptance failures."""


class HttpResponse:
    """Small response object for HTTP helper results."""

    def __init__(self, status: int, payload: dict[str, Any] | list[Any] | None = None, body: str = "") -> None:
        self.status = status
        self.payload = payload if payload is not None else {}
        self.body = body


class ManualAcceptanceRunner:
    """Orchestrates the real ClickHouse manual acceptance flow."""

    def __init__(
        self,
        args: argparse.Namespace,
        *,
        client_factory=create_clickhouse_client,
        process_factory=subprocess.Popen,
        http_request=None,
        sleep=time.sleep,
    ) -> None:
        self.args = args
        self.client_factory = client_factory
        self.process_factory = process_factory
        self.http_request = http_request or http_json_request
        self.sleep = sleep
        self.config = AcceptanceConfig()
        self.database = validate_identifier(self.config.clickhouse_database)
        self.report_path = _resolve_project_path(args.report_path)
        self.server_log_path = _resolve_project_path(args.server_log_path)
        self.client: Any = None
        self.process: Any = None
        self.server_log = None
        self.cleanup_performed = False
        self.server_stopped = False
        self.port_released = False
        self.report = _empty_report(args)

    def run(self) -> dict[str, Any]:
        """Run manual acceptance and always write a JSON report."""
        try:
            validate_cli_gates(self.args)
            self._check_port_free()
            self.client = self.client_factory(self.config)
            self.report["counts_before"] = self._collect_counts()
            self.report["baseline_user_count"] = self._baseline_user_count()
            if self.report["baseline_user_count"] <= 0:
                raise ManualAcceptanceError(f"baseline not found for username={self.args.username!r}")

            old_rows = int(self.report["counts_before"].get("continuous_logs", 0))
            if old_rows > 0 and not self.args.cleanup_before:
                raise ManualAcceptanceError(
                    f"found {old_rows} existing continuous rows; rerun with --cleanup-before --confirm-cleanup"
                )
            if self.args.cleanup_before:
                self._cleanup_continuous_logs()
                self.report["counts_before"] = self._collect_counts()

            self._start_server()
            self._wait_for_status()
            self._run_rate_checks()
            self.report["quality"] = self._quality_summary()
            self._run_pause_resume_checks()
            self._run_mode_checks()
            self._run_mixed_check()
            self._run_invalid_request_checks()
            self.report["counts_after"] = self._collect_counts()
            self.report["table_unchanged"] = self._table_unchanged(
                self.report["counts_before"], self.report["counts_after"]
            )
            if not all(self.report["table_unchanged"].values()):
                raise ManualAcceptanceError("baseline/training/validation table counts changed")
            self.report["success"] = True
        except Exception as exc:
            self.report["errors"].append(f"{type(exc).__name__}: {exc}")
        finally:
            self._stop_server()
            self.port_released = wait_for_port_release(self.args.host, self.args.port, timeout_seconds=5.0)
            self.report["server_stopped"] = self.server_stopped
            self.report["port_released"] = self.port_released
            should_cleanup = (self.report["success"] and self.args.cleanup_after) or (
                (not self.report["success"]) and self.args.cleanup_on_failure
            )
            if should_cleanup and self.client is not None:
                try:
                    self._cleanup_continuous_logs()
                except Exception as exc:
                    self.report["success"] = False
                    self.report["errors"].append(f"cleanup failed: {type(exc).__name__}: {exc}")
            if self.client is not None:
                try:
                    self.report["counts_after_cleanup"] = self._collect_counts()
                except Exception:
                    self.report["counts_after_cleanup"] = {}
                try:
                    self.client.close()
                except Exception:
                    pass
            self.report["cleanup_performed"] = self.cleanup_performed
            self.report["finished_at"] = _utc_now()
            write_report(self.report_path, self.report)
        return self.report

    def _check_port_free(self) -> None:
        if is_port_open(self.args.host, self.args.port):
            raise ManualAcceptanceError(f"port already in use: {self.args.host}:{self.args.port}")

    def _start_server(self) -> None:
        self.server_log_path.parent.mkdir(parents=True, exist_ok=True)
        self.server_log = self.server_log_path.open("a", encoding="utf-8")
        env = os.environ.copy()
        env["PYTHONPATH"] = str(PROJECT_ROOT)
        command = [
            sys.executable,
            "-m",
            SERVER_MODULE,
            "--logs-per-second",
            "20",
            "--host",
            self.args.host,
            "--port",
            str(self.args.port),
            "--username",
            self.args.username,
        ]
        self.process = self.process_factory(
            command,
            cwd=str(PROJECT_ROOT),
            env=env,
            stdout=self.server_log,
            stderr=subprocess.STDOUT,
        )

    def _wait_for_status(self) -> dict[str, Any]:
        deadline = time.time() + 20.0
        last_error = "not ready"
        while time.time() < deadline:
            if self.process is not None and self.process.poll() is not None:
                raise ManualAcceptanceError(f"server exited early with code {self.process.poll()}")
            try:
                response = self._http("GET", "/status")
                if response.status == 200 and isinstance(response.payload, dict):
                    return response.payload
                last_error = f"HTTP {response.status}"
            except Exception as exc:
                last_error = str(exc)
            self.sleep(0.25)
        raise ManualAcceptanceError(f"server did not become ready: {last_error}")

    def _run_rate_checks(self) -> None:
        before20 = self._continuous_count()
        self.sleep(self.args.rate_seconds)
        after20 = self._continuous_count()
        self.report["rate20_delta"] = after20 - before20
        if not 160 <= self.report["rate20_delta"] <= 240:
            raise ManualAcceptanceError(f"20/sec delta out of range: {self.report['rate20_delta']}")

        response = self._http("POST", "/rate", {"logs_per_second": 50})
        if response.status != 200:
            raise ManualAcceptanceError(f"/rate 50 failed with HTTP {response.status}")
        before50 = self._continuous_count()
        self.sleep(self.args.rate_seconds)
        after50 = self._continuous_count()
        self.report["rate50_delta"] = after50 - before50
        if not 450 <= self.report["rate50_delta"] <= 550:
            raise ManualAcceptanceError(f"50/sec delta out of range: {self.report['rate50_delta']}")

    def _run_pause_resume_checks(self) -> None:
        response = self._http("POST", "/stop")
        if response.status != 200:
            raise ManualAcceptanceError(f"/stop failed with HTTP {response.status}")
        before_pause = self._continuous_count()
        self.sleep(2)
        self.report["pause_delta"] = self._continuous_count() - before_pause
        if self.report["pause_delta"] != 0:
            raise ManualAcceptanceError(f"pause delta must be 0, got {self.report['pause_delta']}")

        response = self._http("POST", "/start")
        if response.status != 200:
            raise ManualAcceptanceError(f"/start failed with HTTP {response.status}")
        before_resume = self._continuous_count()
        self.sleep(2)
        self.report["resume_delta"] = self._continuous_count() - before_resume
        if self.report["resume_delta"] <= 0:
            raise ManualAcceptanceError("resume did not generate new rows")

    def _run_mode_checks(self) -> None:
        results = {}
        for mode in MODE_NAMES:
            response = self._http("POST", "/mode", {"mode": mode})
            if response.status != 200:
                raise ManualAcceptanceError(f"/mode {mode} failed with HTTP {response.status}")
            before = self._continuous_count()
            self.sleep(2)
            rows = self._latest_rows_since(before, limit=100)
            results[mode] = self._validate_mode_rows(mode, rows)
            if not results[mode]["ok"]:
                raise ManualAcceptanceError(f"mode {mode} failed: {results[mode]}")
        self.report["mode_checks"] = results

    def _run_mixed_check(self) -> None:
        self._http("POST", "/rate", {"logs_per_second": 20})
        response = self._http("POST", "/mode", {"mode": "mixed"})
        if response.status != 200:
            raise ManualAcceptanceError(f"/mode mixed failed with HTTP {response.status}")
        before = self._continuous_count()
        deadline = time.time() + 10.0
        rows = []
        while time.time() < deadline:
            rows = self._latest_rows_since(before, limit=120)
            if len(rows) >= 100:
                break
            self.sleep(0.5)
        latest = rows[-100:] if len(rows) >= 100 else rows
        distribution = mode_distribution_from_rows(latest)
        self.report["mixed_distribution"] = distribution
        if distribution != {"normal": 80, "single_anomaly": 15, "combo_anomaly": 5}:
            raise ManualAcceptanceError(f"mixed distribution mismatch: {distribution}")

    def _run_invalid_request_checks(self) -> None:
        invalid_rate = self._http("POST", "/rate", {"logs_per_second": 1001})
        invalid_mode = self._http("POST", "/mode", {"mode": "invalid_mode"})
        self.report["invalid_requests"] = {
            "invalid_rate_status": invalid_rate.status,
            "invalid_mode_status": invalid_mode.status,
        }
        if invalid_rate.status != 400 or invalid_mode.status != 400:
            raise ManualAcceptanceError(f"invalid request statuses were not 400: {self.report['invalid_requests']}")

    def _validate_mode_rows(self, mode: str, rows: list[dict[str, Any]]) -> dict[str, Any]:
        if not rows:
            return {"ok": False, "reason": "no rows"}
        row = rows[-1]
        checks = {
            "normal": lambda r: r.get("src_country") == "中国"
            and r.get("src_city") == "北京"
            and r.get("vpn_gateway") == "vpn-gw-cn-01"
            and r.get("event_type") == "LOGIN_SUCCESS"
            and r.get("result") == "SUCCESS"
            and not bool(r.get("is_off_hours"))
            and not bool(r.get("is_unusual_ip")),
            "new_ip": lambda r: r.get("src_country") == "中国"
            and r.get("src_city") == "北京"
            and str(r.get("source_ip", "")).startswith("198.51.100.")
            and r.get("result") == "SUCCESS"
            and bool(r.get("is_unusual_ip")),
            "new_country": lambda r: r.get("src_country") == "德国" and r.get("src_city") == "法兰克福" and r.get("result") == "SUCCESS",
            "new_city": lambda r: r.get("src_country") == "中国" and r.get("src_city") == "深圳" and r.get("result") == "SUCCESS",
            "failed_login": lambda r: r.get("event_type") == "LOGIN_FAIL"
            and r.get("result") == "FAILED"
            and r.get("fail_reason") == "PASSWORD_ERROR",
            "off_hours": lambda r: bool(r.get("is_off_hours")) and _row_is_recent(r),
            "combo_anomaly": lambda r: r.get("src_country") == "德国"
            and r.get("src_city") == "柏林"
            and r.get("event_type") == "LOGIN_FAIL"
            and r.get("result") == "FAILED"
            and r.get("fail_reason") == "PASSWORD_ERROR"
            and bool(r.get("is_off_hours"))
            and bool(r.get("is_unusual_ip"))
            and _row_is_recent(r),
        }
        return {"ok": bool(checks[mode](row)), "sample": row}

    def _http(self, method: str, path: str, payload: dict[str, Any] | None = None) -> HttpResponse:
        return self.http_request(self.args.host, self.args.port, method, path, payload)

    def _collect_counts(self) -> dict[str, int]:
        return {
            "continuous_logs": self._continuous_count(),
            "baselines": self._table_count("user_behavior_baselines"),
            "training_logs": self._table_count("ueba_baseline_training_logs"),
            "validation_results": self._table_count("ueba_validation_results"),
        }

    def _table_count(self, table: str) -> int:
        table = _validate_allowed_table(table)
        sql = f"SELECT count() AS cnt FROM {self.database}.{table}"
        return int(_scalar_query(self.client, sql, {}))

    def _continuous_count(self) -> int:
        sql = f"""
        SELECT count() AS cnt
        FROM {self.database}.logs_structured
        WHERE position(raw_log, '{MARKER}') > 0
        """
        return int(_scalar_query(self.client, sql, {}))

    def _baseline_user_count(self) -> int:
        sql = f"""
        SELECT count() AS cnt
        FROM {self.database}.user_behavior_baselines
        WHERE username = %(username)s
        """
        return int(_scalar_query(self.client, sql, {"username": self.args.username}))

    def _quality_summary(self) -> dict[str, int]:
        sql = f"""
        SELECT
            count() AS rows,
            countIf(id <= 0) AS invalid_ids,
            countIf(id <= 3000000000000000000) AS below_namespace,
            countIf(id >= 4000000000000000000) AS above_namespace,
            countIf(timestamp > now()) AS future_rows,
            uniqExact(id) AS unique_ids
        FROM {self.database}.logs_structured
        WHERE position(raw_log, '{MARKER}') > 0
        """
        rows = _named_query(self.client, sql, {})
        if not rows:
            return {"rows": 0, "invalid_ids": 0, "below_namespace": 0, "above_namespace": 0, "future_rows": 0, "unique_ids": 0}
        return {key: int(rows[0].get(key, 0) or 0) for key in ("rows", "invalid_ids", "below_namespace", "above_namespace", "future_rows", "unique_ids")}

    def _latest_rows_since(self, before_count: int, *, limit: int) -> list[dict[str, Any]]:
        sql = f"""
        SELECT
            id, timestamp, raw_log, source_ip, src_country, src_city, vpn_gateway,
            event_type, result, fail_reason, is_off_hours, is_unusual_ip
        FROM {self.database}.logs_structured
        WHERE position(raw_log, '{MARKER}') > 0
        ORDER BY id ASC
        LIMIT %(limit)s OFFSET %(offset)s
        """
        return _named_query(self.client, sql, {"limit": int(limit), "offset": int(before_count)})

    def _cleanup_continuous_logs(self) -> None:
        cleanup_continuous_logs(self.client, self.database)
        self.cleanup_performed = True

    def _table_unchanged(self, before: dict[str, int], after: dict[str, int]) -> dict[str, bool]:
        return {
            "baselines": before.get("baselines") == after.get("baselines"),
            "training_logs": before.get("training_logs") == after.get("training_logs"),
            "validation_results": before.get("validation_results") == after.get("validation_results"),
        }

    def _stop_server(self) -> None:
        process = self.process
        if process is None:
            self.server_stopped = True
            return
        try:
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=8)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=5)
            self.server_stopped = process.poll() is not None
        finally:
            if self.server_log is not None:
                self.server_log.close()


def build_arg_parser() -> argparse.ArgumentParser:
    """Build CLI parser without connecting to ClickHouse."""
    parser = argparse.ArgumentParser(description="Run UEBA continuous login manual acceptance")
    parser.add_argument("--confirm-write", action="store_true", help="required to perform real ClickHouse writes")
    parser.add_argument("--confirm-cleanup", action="store_true", help="required with any cleanup option")
    parser.add_argument("--cleanup-before", action="store_true", help="delete existing continuous fixture rows before running")
    parser.add_argument("--cleanup-after", action="store_true", help="delete generated continuous fixture rows after success")
    parser.add_argument("--cleanup-on-failure", action="store_true", help="delete generated rows on failure; requires --confirm-cleanup")
    parser.add_argument("--keep-data-on-failure", action="store_true", default=True, help="default: keep data after failure for debugging")
    parser.add_argument("--host", default=DEFAULT_HOST, help="HTTP server host")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT, help="HTTP server port")
    parser.add_argument("--username", default=DEFAULT_USERNAME, help="baseline fixture username")
    parser.add_argument("--rate-seconds", type=int, default=10, help="seconds to measure 20/sec and 50/sec rates")
    parser.add_argument("--report-path", default=str(DEFAULT_REPORT_PATH), help="project-local JSON report path")
    parser.add_argument("--server-log-path", default=str(DEFAULT_SERVER_LOG_PATH), help="project-local server log path")
    return parser


def validate_cli_gates(args: argparse.Namespace) -> None:
    """Validate destructive-operation gates before any ClickHouse work."""
    if not args.confirm_write:
        raise ManualAcceptanceError("refusing real writes without --confirm-write")
    if (args.cleanup_before or args.cleanup_after or args.cleanup_on_failure) and not args.confirm_cleanup:
        raise ManualAcceptanceError("cleanup options require --confirm-cleanup")
    if isinstance(args.rate_seconds, bool) or args.rate_seconds <= 0:
        raise ManualAcceptanceError("--rate-seconds must be a positive integer")


def encode_json_body(payload: dict[str, Any] | None) -> bytes:
    """Encode JSON request bodies without shell quoting or string concatenation."""
    return json.dumps(payload or {}).encode("utf-8")


def http_json_request(
    host: str,
    port: int,
    method: str,
    path: str,
    payload: dict[str, Any] | None = None,
    *,
    timeout: float = 5.0,
) -> HttpResponse:
    """Send one JSON HTTP request to the local control port."""
    url = f"http://{host}:{port}{path}"
    body = encode_json_body(payload) if method.upper() == "POST" else None
    headers = {"Content-Type": "application/json"} if body is not None else {}
    request = Request(url, data=body, method=method.upper(), headers=headers)
    try:
        with urlopen(request, timeout=timeout) as response:
            text = response.read().decode("utf-8")
            return HttpResponse(response.status, _decode_json(text), text)
    except HTTPError as exc:
        text = exc.read().decode("utf-8")
        return HttpResponse(exc.code, _decode_json(text), text)
    except URLError as exc:
        raise ManualAcceptanceError(f"HTTP request failed: {exc}") from exc


def cleanup_continuous_logs(client: Any, database: str) -> None:
    """Clean only rows carrying the continuous fixture marker."""
    database = validate_identifier(database)
    sql = f"""
    ALTER TABLE {database}.logs_structured
    DELETE WHERE position(raw_log, '{MARKER}') > 0
    SETTINGS mutations_sync = 1
    """
    client.command(sql, parameters={})


def mode_distribution_from_rows(rows: list[dict[str, Any]]) -> dict[str, int]:
    """Return normal/single/combo counts parsed from raw_log mode markers."""
    modes = Counter(_mode_from_raw_log(str(row.get("raw_log", ""))) for row in rows)
    single = sum(modes[mode] for mode in ("new_ip", "new_country", "new_city", "failed_login", "off_hours"))
    return {
        "normal": modes["normal"],
        "single_anomaly": single,
        "combo_anomaly": modes["combo_anomaly"],
    }


def _json_default(value: object) -> str:
    """Stable JSON serializer for datetime and date."""
    if isinstance(value, datetime):
        return value.isoformat(sep=" ")
    if isinstance(value, date):
        return value.isoformat()
    raise TypeError(f"Object of type {type(value).__name__} is not JSON serializable")


def write_report(path: Path, report: dict[str, Any]) -> None:
    """Write the manual acceptance report as project-local JSON."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True, default=_json_default) + "\n", encoding="utf-8")


def is_port_open(host: str, port: int, *, timeout: float = 0.25) -> bool:
    """Return True when the TCP port accepts connections."""
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def wait_for_port_release(host: str, port: int, *, timeout_seconds: float) -> bool:
    """Wait until a TCP port no longer accepts connections."""
    deadline = time.time() + timeout_seconds
    while time.time() < deadline:
        if not is_port_open(host, port):
            return True
        time.sleep(0.1)
    return not is_port_open(host, port)


def main(argv: list[str] | None = None, **runner_kwargs: Any) -> int:
    """CLI entry point. Returns non-zero on failure."""
    parser = build_arg_parser()
    args = parser.parse_args(argv)
    runner = ManualAcceptanceRunner(args, **runner_kwargs)
    report = runner.run()
    if report["success"]:
        print("===== MANUAL ACCEPTANCE PASSED =====")
        return 0
    print("===== MANUAL ACCEPTANCE FAILED =====")
    for error in report.get("errors", []):
        print(f"- {error}")
    print(f"Report: {runner.report_path}")
    return 1


def _empty_report(args: argparse.Namespace) -> dict[str, Any]:
    report = {
        "success": False,
        "started_at": _utc_now(),
        "finished_at": None,
        "host": args.host,
        "port": args.port,
        "username": args.username,
        "baseline_user_count": 0,
        "counts_before": {},
        "counts_after": {},
        "counts_after_cleanup": {},
        "rate20_delta": 0,
        "rate50_delta": 0,
        "pause_delta": 0,
        "resume_delta": 0,
        "mixed_distribution": {},
        "quality": {},
        "table_unchanged": {"baselines": False, "training_logs": False, "validation_results": False},
        "invalid_requests": {},
        "server_stopped": False,
        "port_released": False,
        "cleanup_performed": False,
        "errors": [],
    }
    missing = [field for field in REQUIRED_REPORT_FIELDS if field not in report]
    if missing:
        raise RuntimeError(f"report missing required fields: {missing}")
    return report


def _resolve_project_path(value: str | os.PathLike[str]) -> Path:
    path = Path(value)
    if not path.is_absolute():
        path = PROJECT_ROOT / path
    resolved = path.resolve()
    if not resolved.is_relative_to(PROJECT_ROOT):
        raise ManualAcceptanceError(f"path must stay inside project directory: {value}")
    return resolved


def _validate_allowed_table(table: str) -> str:
    if table not in ALLOWED_TABLES:
        raise ManualAcceptanceError(f"table is not allowed for this runner: {table}")
    return table


def _scalar_query(client: Any, sql: str, parameters: dict[str, Any]) -> Any:
    rows = _named_query(client, sql, parameters)
    if rows:
        first = rows[0]
        if "cnt" in first:
            return first["cnt"]
        return next(iter(first.values()))
    return 0


def _named_query(client: Any, sql: str, parameters: dict[str, Any]) -> list[dict[str, Any]]:
    result = client.query(sql, parameters=parameters)
    if hasattr(result, "named_results"):
        rows = result.named_results() if callable(result.named_results) else result.named_results
        return list(rows)
    if hasattr(result, "result_rows") and hasattr(result, "column_names"):
        return [dict(zip(result.column_names, row)) for row in result.result_rows]
    if isinstance(result, list):
        return [dict(row) for row in result]
    return []


def _decode_json(text: str) -> dict[str, Any] | list[Any]:
    if not text:
        return {}
    try:
        value = json.loads(text)
    except json.JSONDecodeError:
        return {"raw": text}
    return value


def _mode_from_raw_log(raw_log: str) -> str:
    marker = "mode="
    if marker not in raw_log:
        return "unknown"
    return raw_log.split(marker, 1)[1].split(" ", 1)[0]


def _row_is_recent(row: dict[str, Any]) -> bool:
    value = row.get("timestamp")
    if isinstance(value, datetime):
        timestamp = value.replace(tzinfo=None)
    else:
        timestamp = datetime.strptime(str(value), "%Y-%m-%d %H:%M:%S")
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    return 0 <= (now - timestamp).total_seconds() <= 300


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = [
    "DEFAULT_REPORT_PATH",
    "DEFAULT_SERVER_LOG_PATH",
    "MARKER",
    "ManualAcceptanceError",
    "ManualAcceptanceRunner",
    "REQUIRED_REPORT_FIELDS",
    "build_arg_parser",
    "cleanup_continuous_logs",
    "_json_default",
    "encode_json_body",
    "http_json_request",
    "main",
    "mode_distribution_from_rows",
    "validate_cli_gates",
    "write_report",
]
