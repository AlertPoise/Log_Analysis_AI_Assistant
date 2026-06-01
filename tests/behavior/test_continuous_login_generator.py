"""Tests for UEBA continuous login acceptance traffic generator."""

from __future__ import annotations

from collections import Counter
import json
import threading
import time
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import pytest

from tests.behavior.ueba_baseline_acceptance.clickhouse_writer import INSERT_COLUMNS
from tests.behavior.ueba_baseline_acceptance.continuous_login_generator import (
    ContinuousLoginGenerator,
    SUPPORTED_MODES,
)
from tests.behavior.ueba_baseline_acceptance.continuous_login_http_server import (
    TOKEN_HEADER,
    create_http_server,
    validate_bind_security,
)
from tests.behavior.ueba_baseline_acceptance.id_generator import (
    MAX_UINT64,
    MonotonicIdGenerator,
    deterministic_log_id,
)


CONTINUOUS_BASE = 3 * 10**18
CONTINUOUS_LIMIT = 4 * 10**18


class FakeWriter:
    def __init__(self, *, fail: bool = False):
        self.fail = fail
        self.calls: list[list[dict]] = []
        self.event = threading.Event()
        self.lock = threading.Lock()

    def __call__(self, rows):
        with self.lock:
            self.calls.append(list(rows))
            self.event.set()
        if self.fail:
            raise RuntimeError("fake writer failed")
        return len(rows)

    def call_count(self) -> int:
        with self.lock:
            return len(self.calls)

    def batch_sizes(self) -> list[int]:
        with self.lock:
            return [len(rows) for rows in self.calls]

    def total_rows(self) -> int:
        return sum(self.batch_sizes())


class FakeHTTPGenerator:
    def __init__(self):
        self.logs_per_second = 20
        self.mode = "mixed"
        self.paused = False
        self.resume_calls = 0

    def status(self):
        return {
            "running": True,
            "paused": self.paused,
            "shutdown": False,
            "logs_per_second": self.logs_per_second,
            "mode": self.mode,
            "generated_rows": 10,
            "written_rows": 10,
            "write_errors": 0,
            "last_error": None,
        }

    def set_rate(self, value):
        if isinstance(value, bool) or not isinstance(value, int) or value < 0 or value > 1000:
            raise ValueError("invalid rate")
        self.logs_per_second = value

    def set_mode(self, value):
        if value not in SUPPORTED_MODES:
            raise ValueError("invalid mode")
        self.mode = value

    def resume(self):
        self.resume_calls += 1
        self.paused = False

    def pause(self):
        self.paused = True


def test_monotonic_id_generator_is_strictly_increasing_in_continuous_namespace():
    generator = MonotonicIdGenerator()
    values = [generator.next() for _ in range(100)]

    assert values == sorted(values)
    assert len(set(values)) == len(values)
    assert all(0 < value <= MAX_UINT64 for value in values)
    assert all(CONTINUOUS_BASE < value < CONTINUOUS_LIMIT for value in values)


def test_monotonic_id_generator_is_unique_across_threads():
    generator = MonotonicIdGenerator()
    values = []
    lock = threading.Lock()

    def worker():
        local_values = [generator.next() for _ in range(250)]
        with lock:
            values.extend(local_values)

    threads = [threading.Thread(target=worker) for _ in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert len(values) == 2000
    assert len(set(values)) == 2000
    assert all(CONTINUOUS_BASE < value < CONTINUOUS_LIMIT for value in values)


def test_monotonic_id_generator_does_not_collide_with_static_namespaces():
    continuous_id = MonotonicIdGenerator().next()
    baseline_id = deterministic_log_id(namespace="baseline", seed=42, user_index=1, row_index=1)
    validation_id = deterministic_log_id(namespace="validation", seed=42, user_index=1, row_index=1)

    assert continuous_id != baseline_id
    assert continuous_id != validation_id
    assert baseline_id < 2 * 10**18
    assert 2 * 10**18 < validation_id < 3 * 10**18
    assert 3 * 10**18 < continuous_id < 4 * 10**18


def test_generator_defaults_and_rate_validation():
    generator = ContinuousLoginGenerator(writer_callback=FakeWriter())

    assert generator.status()["logs_per_second"] == 20
    generator.set_rate(0)
    assert generator.status()["logs_per_second"] == 0
    generator.set_rate(1000)
    assert generator.status()["logs_per_second"] == 1000
    with pytest.raises(ValueError):
        generator.set_rate(-1)
    with pytest.raises(ValueError):
        generator.set_rate(1001)
    with pytest.raises(ValueError):
        generator.set_rate(True)


@pytest.mark.parametrize("mode", SUPPORTED_MODES)
def test_each_mode_generates_writer_compatible_complete_rows(mode):
    generator = ContinuousLoginGenerator(writer_callback=FakeWriter())
    rows = generator.generate_batch(20 if mode == "mixed" else 3, mode=mode)

    assert rows
    ids = [row["id"] for row in rows]
    assert len(set(ids)) == len(ids)
    assert all(0 < value <= MAX_UINT64 for value in ids)
    assert all(set(INSERT_COLUMNS).issubset(row) for row in rows)
    assert all("ueba_continuous_fixture" in row["raw_log"] for row in rows)


def test_normal_mode_fields_match_stable_user_baseline_habits():
    row = ContinuousLoginGenerator(writer_callback=FakeWriter()).generate_batch(1, mode="normal")[0]

    assert row["username"] == "fixture_user_stable_0001"
    assert row["source_ip"] in {"10.10.1.1", "10.10.1.2"}
    assert row["src_country"] == "中国"
    assert row["src_city"] == "北京"
    assert row["vpn_gateway"] == "vpn-gw-cn-01"
    assert row["result"] == "SUCCESS"
    assert row["event_type"] == "LOGIN_SUCCESS"
    assert row["auth_method"] == "password+mfa"
    assert row["client_software"] == "OpenVPN Connect"
    assert row["protocol"] == "SSLVPN"
    assert row["is_off_hours"] is False
    assert row["is_unusual_ip"] is False


def test_single_dimension_modes_only_change_expected_fields():
    normal = ContinuousLoginGenerator(writer_callback=FakeWriter()).generate_batch(1, mode="normal")[0]
    new_ip = ContinuousLoginGenerator(writer_callback=FakeWriter()).generate_batch(1, mode="new_ip")[0]
    failed = ContinuousLoginGenerator(writer_callback=FakeWriter()).generate_batch(1, mode="failed_login")[0]

    assert new_ip["source_ip"] != normal["source_ip"]
    assert new_ip["is_unusual_ip"] is True
    assert _comparable(new_ip, {"id", "raw_log", "source_ip", "is_unusual_ip"}) == _comparable(
        normal, {"id", "raw_log", "source_ip", "is_unusual_ip"}
    )

    assert failed["result"] == "FAILED"
    assert failed["event_type"] == "LOGIN_FAIL"
    assert failed["fail_reason"] == "PASSWORD_ERROR"
    assert _comparable(failed, {"id", "raw_log", "result", "event_type", "fail_reason"}) == _comparable(
        normal, {"id", "raw_log", "result", "event_type", "fail_reason"}
    )


def test_combo_anomaly_contains_multiple_anomaly_factors():
    row = ContinuousLoginGenerator(writer_callback=FakeWriter()).generate_batch(1, mode="combo_anomaly")[0]

    assert row["source_ip"].startswith("203.0.113.")
    assert row["src_country"] == "德国"
    assert row["src_city"] == "柏林"
    assert row["result"] == "FAILED"
    assert row["event_type"] == "LOGIN_FAIL"
    assert row["is_off_hours"] is True
    assert row["is_unusual_ip"] is True
    assert row["timestamp"][11:13] == "02"


def test_mixed_mode_uses_reproducible_80_15_5_split():
    rows = ContinuousLoginGenerator(writer_callback=FakeWriter()).generate_batch(20, mode="mixed")
    modes = [row["raw_log"].split("mode=", 1)[1].split(" ", 1)[0] for row in rows]
    counts = Counter(modes)

    assert counts["normal"] == 16
    assert counts["combo_anomaly"] == 1
    assert sum(counts[mode] for mode in ("new_ip", "new_country", "new_city", "failed_login", "off_hours")) == 3


def test_background_loop_batches_pause_resume_rate_change_and_shutdown():
    writer = FakeWriter()
    generator = ContinuousLoginGenerator(writer_callback=writer, logs_per_second=3, mode="normal", tick_seconds=0.01)
    try:
        generator.start()
        assert writer.event.wait(1.0)
        assert writer.batch_sizes()[0] == 3

        generator.pause()
        paused_count = writer.call_count()
        assert _stable_call_count(writer, paused_count)

        generator.set_rate(5)
        generator.resume()
        assert _wait_until(lambda: writer.call_count() > paused_count)
        assert 5 in writer.batch_sizes()[paused_count:]
        assert all(size in {3, 5} for size in writer.batch_sizes())
    finally:
        generator.shutdown()
        generator.shutdown()

    assert generator.status()["shutdown"] is True
    assert generator.status()["running"] is False


def test_background_loop_records_writer_errors_and_keeps_thread_alive():
    writer = FakeWriter(fail=True)
    generator = ContinuousLoginGenerator(writer_callback=writer, logs_per_second=2, tick_seconds=0.01)
    try:
        generator.start()
        assert _wait_until(lambda: generator.status()["write_errors"] >= 1)
        status = generator.status()
        assert status["running"] is True
        assert "RuntimeError: fake writer failed" == status["last_error"]
    finally:
        generator.shutdown()


def test_http_status_rate_mode_start_stop_and_invalid_requests():
    fake = FakeHTTPGenerator()
    server = create_http_server(generator=fake, host="127.0.0.1", port=0)
    thread = _serve(server)
    base_url = f"http://127.0.0.1:{server.server_port}"
    try:
        assert _request_json(base_url + "/status")["logs_per_second"] == 20
        assert _request_json(base_url + "/rate", method="POST", payload={"logs_per_second": 50})["logs_per_second"] == 50
        assert _request_json(base_url + "/mode", method="POST", payload={"mode": "combo_anomaly"})["mode"] == "combo_anomaly"
        assert _request_json(base_url + "/stop", method="POST")["paused"] is True
        assert _request_json(base_url + "/start", method="POST")["paused"] is False

        assert _request_error(base_url + "/rate", method="POST", data=b"{") == 400
        assert _request_error(base_url + "/rate", method="POST", payload={"logs_per_second": 1001}) == 400
        assert _request_error(base_url + "/mode", method="POST", payload={"mode": "bad"}) == 400
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=1.0)


def test_http_non_loopback_requires_token_and_token_is_validated():
    with pytest.raises(ValueError):
        validate_bind_security("0.0.0.0", None)
    validate_bind_security("0.0.0.0", "secret")

    fake = FakeHTTPGenerator()
    server = create_http_server(generator=fake, host="127.0.0.1", port=0, token="secret")
    thread = _serve(server)
    base_url = f"http://127.0.0.1:{server.server_port}"
    try:
        assert _request_error(base_url + "/status") == 401
        assert _request_json(base_url + "/status", headers={TOKEN_HEADER: "secret"})["running"] is True
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=1.0)


def _comparable(row: dict, exclude: set[str]) -> dict:
    return {key: value for key, value in row.items() if key not in exclude}


def _serve(server):
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return thread


def _request_json(url, *, method="GET", payload=None, data=None, headers=None):
    body = data
    request_headers = dict(headers or {})
    if payload is not None:
        body = json.dumps(payload).encode("utf-8")
        request_headers["Content-Type"] = "application/json"
    request = Request(url, data=body, method=method, headers=request_headers)
    with urlopen(request, timeout=1.0) as response:
        return json.loads(response.read().decode("utf-8"))


def _request_error(url, *, method="GET", payload=None, data=None, headers=None) -> int:
    with pytest.raises(HTTPError) as exc_info:
        _request_json(url, method=method, payload=payload, data=data, headers=headers)
    return exc_info.value.code


def _wait_until(predicate, *, timeout=1.0) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if predicate():
            return True
        time.sleep(0.005)
    return predicate()


def _stable_call_count(writer: FakeWriter, expected: int) -> bool:
    deadline = time.time() + 0.05
    while time.time() < deadline:
        if writer.call_count() != expected:
            return False
        time.sleep(0.005)
    return writer.call_count() == expected
