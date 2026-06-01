"""Deterministic positive UInt64 IDs for UEBA acceptance fixture logs."""

from __future__ import annotations


MAX_UINT64 = 2**64 - 1
_NAMESPACE_CODES = {
    "baseline": 1,
    "validation": 2,
    "continuous": 3,
}
_NAMESPACE_FACTOR = 10**18
_SEED_FACTOR = 10**12
_MONTH_FACTOR = 10**9
_USER_FACTOR = 10**6
_MAX_SEED = _MONTH_FACTOR - 1
_MAX_MONTH_INDEX = _USER_FACTOR - 1
_MAX_USER_INDEX = _USER_FACTOR - 1
_MAX_ROW_INDEX = _USER_FACTOR - 1


def deterministic_log_id(
    *,
    namespace: str,
    seed: int,
    user_index: int,
    row_index: int,
    month_index: int = 0,
) -> int:
    """Generate a stable positive UInt64 acceptance fixture log ID."""
    namespace_code = _namespace_code(namespace)
    seed = _bounded_int("seed", seed, _MAX_SEED)
    month_index = _bounded_int("month_index", month_index, _MAX_MONTH_INDEX)
    user_index = _bounded_int("user_index", user_index, _MAX_USER_INDEX)
    row_index = _bounded_int("row_index", row_index, _MAX_ROW_INDEX)

    value = (
        namespace_code * _NAMESPACE_FACTOR
        + seed * _SEED_FACTOR
        + month_index * _MONTH_FACTOR
        + user_index * _USER_FACTOR
        + row_index
        + 1
    )
    if value <= 0 or value > MAX_UINT64:
        raise ValueError("generated ID is outside positive UInt64 range")
    return value


def _namespace_code(namespace: str) -> int:
    if namespace not in _NAMESPACE_CODES:
        raise ValueError(f"unsupported namespace: {namespace!r}")
    return _NAMESPACE_CODES[namespace]


def _bounded_int(name: str, value: int, max_value: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError(f"{name} must be an integer")
    if value < 0 or value > max_value:
        raise ValueError(f"{name} must be between 0 and {max_value}")
    return value


__all__ = ["MAX_UINT64", "deterministic_log_id"]
