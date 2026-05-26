"""Tests for UEBA acceptance tool config."""

from pathlib import Path

from tests.behavior.ueba_baseline_acceptance.config import AcceptanceConfig


def test_acceptance_config_defaults_are_safe_and_sized():
    """Default config should describe a local acceptance fixture only."""
    config = AcceptanceConfig()

    assert config.output_dir == Path(".tox/ueba_baseline_acceptance")
    assert config.model_version == "ueba_baseline_fixture_v2_monthly"
    assert config.min_sample_count == 20
    assert 10 <= config.expected_user_count <= 30
    assert config.total_expected_logs >= 60000
    assert config.fixture_id == "ueba_fixture_v2_monthly_seed_42"
    assert str(config.seed) in config.fixture_id
    assert config.ip_long_tail_user_count == 2
    assert config.expected_user_count == 26
    assert config.total_expected_logs == 66130
    assert config.clickhouse_password == ""
    assert config.clean_before_load is True
    assert config.clickhouse_batch_size == 1000
    assert config.validation_float_tolerance == 0.0001
    assert config.validation_common_min_ratio == 0.1
