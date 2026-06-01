"""HTTP control port for the UEBA continuous login fixture generator."""

from __future__ import annotations

import argparse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import signal
import threading
from typing import Any

from .clickhouse_writer import FixtureClickHouseWriter, create_clickhouse_client
from .config import AcceptanceConfig
from .continuous_login_generator import ContinuousLoginGenerator, SUPPORTED_MODES


DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8765
MAX_REQUEST_BYTES = 4096
TOKEN_HEADER = "X-UEBA-Token"


class ContinuousLoginHTTPServer(ThreadingHTTPServer):
    """Threading HTTP server carrying generator and token state."""

    def __init__(self, server_address, RequestHandlerClass, *, generator, token: str | None = None):
        super().__init__(server_address, RequestHandlerClass)
        self.generator = generator
        self.token = token


def create_http_server(
    *,
    generator: Any,
    host: str = DEFAULT_HOST,
    port: int = DEFAULT_PORT,
    token: str | None = None,
) -> ContinuousLoginHTTPServer:
    """Create a local HTTP server without constructing ClickHouse clients."""
    validate_bind_security(host, token)
    return ContinuousLoginHTTPServer((host, port), ContinuousLoginRequestHandler, generator=generator, token=token)


def validate_bind_security(host: str, token: str | None) -> None:
    """Require a token when binding outside loopback."""
    if not _is_loopback_host(host) and not token:
        raise ValueError("--token is required when binding to a non-loopback host")


class ContinuousLoginRequestHandler(BaseHTTPRequestHandler):
    """Small JSON API for controlling a continuous login generator."""

    server: ContinuousLoginHTTPServer

    def do_GET(self) -> None:
        if not self._authorize():
            return
        if self.path != "/status":
            self._json_response(404, {"error": "not found"})
            return
        self._json_response(200, self.server.generator.status())

    def do_POST(self) -> None:
        if not self._authorize():
            return
        if self.path == "/start":
            try:
                self.server.generator.resume()
            except Exception as exc:
                self._json_response(400, {"error": str(exc)})
                return
            self._json_response(200, self.server.generator.status())
            return
        if self.path == "/stop":
            self.server.generator.pause()
            self._json_response(200, self.server.generator.status())
            return
        if self.path == "/rate":
            payload = self._read_json_body()
            if payload is None:
                return
            try:
                self.server.generator.set_rate(payload["logs_per_second"])
            except KeyError:
                self._json_response(400, {"error": "missing logs_per_second"})
                return
            except ValueError as exc:
                self._json_response(400, {"error": str(exc)})
                return
            self._json_response(200, self.server.generator.status())
            return
        if self.path == "/mode":
            payload = self._read_json_body()
            if payload is None:
                return
            try:
                self.server.generator.set_mode(payload["mode"])
            except KeyError:
                self._json_response(400, {"error": "missing mode"})
                return
            except ValueError as exc:
                self._json_response(400, {"error": str(exc)})
                return
            self._json_response(200, self.server.generator.status())
            return
        self._json_response(404, {"error": "not found"})

    def log_message(self, format: str, *args: Any) -> None:
        """Keep tests and demos quiet unless callers add their own logging."""
        return

    def _authorize(self) -> bool:
        token = self.server.token
        if token and self.headers.get(TOKEN_HEADER) != token:
            self._json_response(401, {"error": "invalid or missing token"})
            return False
        return True

    def _read_json_body(self) -> dict[str, Any] | None:
        raw_length = self.headers.get("Content-Length", "0")
        try:
            length = int(raw_length)
        except ValueError:
            self._json_response(400, {"error": "invalid Content-Length"})
            return None
        if length > MAX_REQUEST_BYTES:
            self._json_response(413, {"error": "request body too large"})
            return None
        data = self.rfile.read(length) if length > 0 else b"{}"
        try:
            payload = json.loads(data.decode("utf-8"))
        except json.JSONDecodeError:
            self._json_response(400, {"error": "invalid JSON"})
            return None
        if not isinstance(payload, dict):
            self._json_response(400, {"error": "JSON body must be an object"})
            return None
        return payload

    def _json_response(self, status_code: int, payload: dict[str, Any]) -> None:
        body = json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
        self.send_response(status_code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def build_arg_parser() -> argparse.ArgumentParser:
    """Build CLI parser without connecting to ClickHouse."""
    parser = argparse.ArgumentParser(description="UEBA continuous login fixture HTTP controller")
    parser.add_argument("--host", default=DEFAULT_HOST, help="HTTP bind host, defaults to 127.0.0.1")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT, help="HTTP bind port, defaults to 8765")
    parser.add_argument("--logs-per-second", type=int, default=20, help="Rows generated per second, 0..1000")
    parser.add_argument("--mode", choices=SUPPORTED_MODES, default="mixed", help="Traffic generation mode")
    parser.add_argument("--token", default=None, help="Required for non-loopback binds; sent via X-UEBA-Token")
    parser.add_argument("--username", default="fixture_user_stable_0001", help="Fixture username with an existing baseline")
    return parser


def main(argv: list[str] | None = None) -> int:
    """Run the HTTP controller; this writes to ClickHouse when not using --help."""
    parser = build_arg_parser()
    args = parser.parse_args(argv)
    validate_bind_security(args.host, args.token)

    config = AcceptanceConfig()
    client = create_clickhouse_client(config)
    writer = FixtureClickHouseWriter(client=client, database=config.clickhouse_database, batch_size=config.clickhouse_batch_size)
    generator = ContinuousLoginGenerator(
        writer_callback=writer.insert_logs,
        logs_per_second=args.logs_per_second,
        mode=args.mode,
        username=args.username,
    )
    server = create_http_server(generator=generator, host=args.host, port=args.port, token=args.token)
    stop_once = threading.Event()

    def _shutdown(_signum=None, _frame=None) -> None:
        if stop_once.is_set():
            return
        stop_once.set()
        generator.shutdown()
        writer.close()
        threading.Thread(target=server.shutdown, daemon=True).start()

    signal.signal(signal.SIGINT, _shutdown)
    signal.signal(signal.SIGTERM, _shutdown)

    try:
        generator.start()
        server.serve_forever()
    finally:
        _shutdown()
        server.server_close()
    return 0


def _is_loopback_host(host: str) -> bool:
    normalized = host.strip().lower()
    return normalized in {"localhost", "::1"} or normalized.startswith("127.")


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = [
    "ContinuousLoginHTTPServer",
    "ContinuousLoginRequestHandler",
    "TOKEN_HEADER",
    "build_arg_parser",
    "create_http_server",
    "main",
    "validate_bind_security",
]
