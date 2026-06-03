"""Tests for AcceptanceConfig CLICKHOUSE_* environment variable reading."""

from __future__ import annotations

import os
from unittest import mock

import pytest

from tests.behavior.ueba_baseline_acceptance.config import AcceptanceConfig, _env_clickhouse_port


def _clear_clickhouse_env(monkeypatch):
    for var in ("CLICKHOUSE_HOST", "CLICKHOUSE_PORT", "CLICKHOUSE_USERNAME",
                "CLICKHOUSE_USER", "CLICKHOUSE_PASSWORD", "CLICKHOUSE_DATABASE"):
        monkeypatch.delenv(var, raising=False)


class TestEnvClickhousePort:
    def test_default_port(self, monkeypatch):
        monkeypatch.delenv("CLICKHOUSE_PORT", raising=False)
        assert _env_clickhouse_port() == 8123

    def test_valid_port(self, monkeypatch):
        monkeypatch.setenv("CLICKHOUSE_PORT", "9000")
        assert _env_clickhouse_port() == 9000

    def test_non_numeric_port_raises(self, monkeypatch):
        monkeypatch.setenv("CLICKHOUSE_PORT", "abc")
        with pytest.raises(ValueError, match="必须为正整数"):
            _env_clickhouse_port()

    def test_zero_port_raises(self, monkeypatch):
        monkeypatch.setenv("CLICKHOUSE_PORT", "0")
        with pytest.raises(ValueError, match="必须为 1..65535"):
            _env_clickhouse_port()

    def test_negative_port_raises(self, monkeypatch):
        monkeypatch.setenv("CLICKHOUSE_PORT", "-5")
        with pytest.raises(ValueError, match="必须为 1..65535"):
            _env_clickhouse_port()


class TestAcceptanceConfigDefaults:
    def test_all_defaults(self, monkeypatch):
        _clear_clickhouse_env(monkeypatch)
        cfg = AcceptanceConfig()
        assert cfg.clickhouse_host == "localhost"
        assert cfg.clickhouse_port == 8123
        assert cfg.clickhouse_user == "default"
        assert cfg.clickhouse_password == ""
        assert cfg.clickhouse_database == "log_analysis"

    def test_host_env(self, monkeypatch):
        _clear_clickhouse_env(monkeypatch)
        monkeypatch.setenv("CLICKHOUSE_HOST", "ch.example.com")
        cfg = AcceptanceConfig()
        assert cfg.clickhouse_host == "ch.example.com"

    def test_port_env(self, monkeypatch):
        _clear_clickhouse_env(monkeypatch)
        monkeypatch.setenv("CLICKHOUSE_PORT", "9000")
        cfg = AcceptanceConfig()
        assert cfg.clickhouse_port == 9000

    def test_username_preferred_over_user(self, monkeypatch):
        _clear_clickhouse_env(monkeypatch)
        monkeypatch.setenv("CLICKHOUSE_USERNAME", "admin1")
        monkeypatch.setenv("CLICKHOUSE_USER", "old_user")
        cfg = AcceptanceConfig()
        assert cfg.clickhouse_user == "admin1"

    def test_user_fallback(self, monkeypatch):
        _clear_clickhouse_env(monkeypatch)
        monkeypatch.setenv("CLICKHOUSE_USER", "alt_user")
        cfg = AcceptanceConfig()
        assert cfg.clickhouse_user == "alt_user"

    def test_password_env(self, monkeypatch):
        _clear_clickhouse_env(monkeypatch)
        monkeypatch.setenv("CLICKHOUSE_PASSWORD", "s3cret")
        cfg = AcceptanceConfig()
        assert cfg.clickhouse_password == "s3cret"

    def test_database_env(self, monkeypatch):
        _clear_clickhouse_env(monkeypatch)
        monkeypatch.setenv("CLICKHOUSE_DATABASE", "test_db")
        cfg = AcceptanceConfig()
        assert cfg.clickhouse_database == "test_db"

    def test_port_boundary_65536_raises(self, monkeypatch):
        monkeypatch.setenv("CLICKHOUSE_PORT", "65536")
        with pytest.raises(ValueError, match="必须为 1..65535"):
            _env_clickhouse_port()

    def test_no_network_connection(self):
        cfg = AcceptanceConfig()
        assert cfg.clickhouse_host == "localhost"
