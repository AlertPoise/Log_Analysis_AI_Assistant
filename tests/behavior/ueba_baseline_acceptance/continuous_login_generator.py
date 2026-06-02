"""Continuous UEBA login fixture traffic generator for acceptance demos."""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, timezone
import threading
import time
from typing import Any

from .clickhouse_writer import INSERT_COLUMNS
from .id_generator import MonotonicIdGenerator


DEFAULT_LOGS_PER_SECOND = 20
MAX_LOGS_PER_SECOND = 1000
SUPPORTED_MODES = (
    "normal",
    "new_ip",
    "new_country",
    "new_city",
    "failed_login",
    "off_hours",
    "combo_anomaly",
    "mixed",
)
_SINGLE_ANOMALY_MODES = ("new_ip", "new_country", "new_city", "failed_login", "off_hours")


class ContinuousLoginGenerator:
    """Only for UEBA acceptance and manual demo continuous login traffic."""

    def __init__(
        self,
        *,
        writer_callback: Callable[[list[dict[str, Any]]], Any],
        logs_per_second: int = DEFAULT_LOGS_PER_SECOND,
        mode: str = "mixed",
        username: str = "fixture_user_stable_0001",
        seed: int = 42,
        tick_seconds: float = 1.0,
        id_generator: MonotonicIdGenerator | None = None,
    ) -> None:
        self.writer_callback = writer_callback
        self.username = username
        self.seed = seed
        self.tick_seconds = _validate_tick_seconds(tick_seconds)
        self.id_generator = id_generator or MonotonicIdGenerator()

        self._lock = threading.Lock()
        self._thread: threading.Thread | None = None
        self._paused = False
        self._shutdown = False
        self._logs_per_second = _validate_rate(logs_per_second)
        self._mode = _validate_mode(mode)
        self._generated_rows = 0
        self._written_rows = 0
        self._write_errors = 0
        self._last_error: str | None = None
        self._sequence = 0

    def generate_batch(self, count: int, mode: str | None = None) -> list[dict[str, Any]]:
        """Generate a deterministic logs_structured-compatible batch."""
        if isinstance(count, bool) or not isinstance(count, int) or count < 0:
            raise ValueError("count must be a non-negative integer")
        active_mode = _validate_mode(mode) if mode is not None else self._snapshot()["mode"]
        with self._lock:
            start_sequence = self._sequence
            self._sequence += count
            self._generated_rows += count

        rows = []
        for offset in range(count):
            sequence = start_sequence + offset
            row_mode = self._mode_for_sequence(active_mode, sequence)
            rows.append(self._build_row(row_mode, sequence))
        return rows

    def set_rate(self, logs_per_second: int) -> None:
        """Set generated rows per tick, bounded for acceptance use."""
        rate = _validate_rate(logs_per_second)
        with self._lock:
            self._logs_per_second = rate

    def set_mode(self, mode: str) -> None:
        """Switch generation mode."""
        valid_mode = _validate_mode(mode)
        with self._lock:
            self._mode = valid_mode

    def start(self) -> None:
        """Start the background generator thread and write traffic immediately."""
        with self._lock:
            if self._shutdown:
                raise RuntimeError("generator has been shut down")
            self._paused = False
            if self._thread is not None and self._thread.is_alive():
                return
            self._thread = threading.Thread(target=self._run_loop, name="ueba-continuous-login-generator", daemon=True)
            self._thread.start()

    def pause(self) -> None:
        """Pause writes without stopping the thread."""
        with self._lock:
            self._paused = True

    def resume(self) -> None:
        """Resume writes and start the thread if needed."""
        self.start()

    def shutdown(self) -> None:
        """Stop the background thread; safe to call more than once."""
        with self._lock:
            self._shutdown = True
            thread = self._thread
        if thread is not None and thread.is_alive() and thread is not threading.current_thread():
            thread.join(timeout=max(self.tick_seconds * 4, 1.0))

    def status(self) -> dict[str, Any]:
        """Return current generator state for HTTP status responses."""
        with self._lock:
            return {
                "running": self._thread is not None and self._thread.is_alive(),
                "paused": self._paused,
                "shutdown": self._shutdown,
                "logs_per_second": self._logs_per_second,
                "mode": self._mode,
                "generated_rows": self._generated_rows,
                "written_rows": self._written_rows,
                "write_errors": self._write_errors,
                "last_error": self._last_error,
            }

    def _run_loop(self) -> None:
        while True:
            snapshot = self._snapshot()
            if snapshot["shutdown"]:
                return
            if snapshot["paused"] or snapshot["logs_per_second"] == 0:
                time.sleep(self.tick_seconds)
                continue

            rows = self.generate_batch(snapshot["logs_per_second"], snapshot["mode"])
            try:
                result = self.writer_callback(rows)
                written = int(result) if isinstance(result, int) and not isinstance(result, bool) else len(rows)
                with self._lock:
                    self._written_rows += written
                    self._last_error = None
            except Exception as exc:
                with self._lock:
                    self._write_errors += 1
                    self._last_error = f"{type(exc).__name__}: {exc}"
            time.sleep(self.tick_seconds)

    def _snapshot(self) -> dict[str, Any]:
        with self._lock:
            return {
                "shutdown": self._shutdown,
                "paused": self._paused,
                "logs_per_second": self._logs_per_second,
                "mode": self._mode,
            }

    def _mode_for_sequence(self, mode: str, sequence: int) -> str:
        if mode != "mixed":
            return mode
        position = sequence % 20
        if position < 16:
            return "normal"
        if position < 19:
            return _SINGLE_ANOMALY_MODES[(sequence // 20 + position - 16) % len(_SINGLE_ANOMALY_MODES)]
        return "combo_anomaly"

    def _build_row(self, mode: str, sequence: int) -> dict[str, Any]:
        row = _normal_row(self.username, sequence, self.seed)
        if mode == "new_ip":
            row.update(source_ip=f"198.51.100.{sequence % 250 + 1}", is_unusual_ip=True)
        elif mode == "new_country":
            row.update(src_country="德国", src_city="法兰克福")
        elif mode == "new_city":
            row.update(src_city="深圳")
        elif mode == "failed_login":
            row.update(result="FAILED", event_type="LOGIN_FAIL", fail_reason="PASSWORD_ERROR")
        elif mode == "off_hours":
            row.update(timestamp=_timestamp_for_mode(sequence, off_hours=True), is_off_hours=True)
        elif mode == "combo_anomaly":
            row.update(
                source_ip=f"203.0.113.{sequence % 250 + 1}",
                src_country="德国",
                src_city="柏林",
                result="FAILED",
                event_type="LOGIN_FAIL",
                fail_reason="PASSWORD_ERROR",
                timestamp=_timestamp_for_mode(sequence, off_hours=True),
                is_off_hours=True,
                is_unusual_ip=True,
            )
        elif mode != "normal":
            raise ValueError(f"unsupported mode: {mode!r}")

        row["id"] = self.id_generator.next()
        row["raw_log"] = f"ueba_continuous_fixture mode={mode} seed={self.seed} sequence={sequence}"
        missing = [column for column in INSERT_COLUMNS if column not in row]
        if missing:
            raise RuntimeError(f"generated row missing columns: {missing}")
        return row


def _normal_row(username: str, sequence: int, seed: int) -> dict[str, Any]:
    return {
        "id": 0,
        "timestamp": _timestamp_for_mode(sequence, off_hours=False),
        "log_type": "vpn",
        "username": username,
        "source_ip": "10.10.1.1" if sequence % 5 else "10.10.1.2",
        "destination_ip": "172.20.10.10" if sequence % 3 else "172.20.10.20",
        "src_country": "中国",
        "src_city": "北京",
        "vpn_gateway": "vpn-gw-cn-01",
        "action": "LOGIN",
        "event_type": "LOGIN_SUCCESS",
        "result": "SUCCESS",
        "fail_reason": "",
        "auth_method": "password+mfa",
        "client_software": "OpenVPN Connect",
        "protocol": "SSLVPN",
        "session_duration_sec": 900 + (sequence + seed) % 600,
        "bytes_sent": 12_000 + sequence % 4096,
        "bytes_recv": 48_000 + (sequence * 3) % 8192,
        "is_off_hours": False,
        "is_unusual_ip": False,
        "parser": "ueba_continuous_fixture",
        "raw_log": "ueba_continuous_fixture",
    }


def _timestamp_for_mode(sequence: int, *, off_hours: bool) -> str:
    now = datetime.now(timezone.utc).replace(tzinfo=None, microsecond=0)
    return now.strftime("%Y-%m-%d %H:%M:%S")


def _validate_rate(logs_per_second: int) -> int:
    if isinstance(logs_per_second, bool) or not isinstance(logs_per_second, int):
        raise ValueError("logs_per_second must be an integer")
    if logs_per_second < 0 or logs_per_second > MAX_LOGS_PER_SECOND:
        raise ValueError(f"logs_per_second must be between 0 and {MAX_LOGS_PER_SECOND}")
    return logs_per_second


def _validate_mode(mode: str | None) -> str:
    if not isinstance(mode, str) or mode not in SUPPORTED_MODES:
        raise ValueError(f"mode must be one of: {', '.join(SUPPORTED_MODES)}")
    return mode


def _validate_tick_seconds(tick_seconds: float) -> float:
    if isinstance(tick_seconds, bool) or not isinstance(tick_seconds, (int, float)) or tick_seconds <= 0:
        raise ValueError("tick_seconds must be a positive number")
    return float(tick_seconds)


__all__ = [
    "ContinuousLoginGenerator",
    "DEFAULT_LOGS_PER_SECOND",
    "MAX_LOGS_PER_SECOND",
    "SUPPORTED_MODES",
]
