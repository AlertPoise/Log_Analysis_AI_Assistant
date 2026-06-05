"""验收用 validation 结果安全清理。

只清理 ueba_validation_results 中明确限定范围内的 fixture 数据。
禁止 DROP / TRUNCATE / 无条件 DELETE。
"""

from __future__ import annotations

from typing import Any

from .config import AcceptanceConfig

CLEANUP_TABLE = "ueba_validation_results"


def _count_validation_results(
    client: Any,
    database: str,
    username: str,
    validation_run_id: str,
    model_version: str,
    start_time: str,
    end_time: str,
    log_type: str = "vpn",
) -> int:
    """统计待清理的 validation 结果行数。"""
    sql = f"""
    SELECT count() AS cnt
    FROM {database}.{CLEANUP_TABLE}
    WHERE username = %(username)s
        AND validation_run_id = %(validation_run_id)s
        AND baseline_model_version = %(model_version)s
        AND timestamp >= %(start_time)s
        AND timestamp < %(end_time)s
        AND log_type = %(log_type)s
    """
    params = {
        "username": username,
        "validation_run_id": validation_run_id,
        "model_version": model_version,
        "start_time": start_time,
        "end_time": end_time,
        "log_type": log_type,
    }
    rows = list(client.query(sql, parameters=params).named_results())
    if not rows:
        return 0
    return int(rows[0].get("cnt", 0) or 0)


def cleanup_validation_results(
    client: Any,
    config: AcceptanceConfig,
    validation_run_id: str,
    start_time: str,
    end_time: str,
    *,
    username: str,
) -> dict[str, Any]:
    """安全清理本轮 validation 验收结果（精确用户名）。

    username 参数为精确匹配，不是 LIKE 前缀。
    只清理指定用户 + validation_run_id + 时间窗口内的数据。

    Returns:
        dict 包含 success / before_count / after_count / error
    """
    result: dict[str, Any] = {
        "success": False,
        "before_count": 0,
        "after_count": 0,
        "error": None,
    }

    # 清理前 count
    try:
        result["before_count"] = _count_validation_results(
            client=client,
            database=config.clickhouse_database,
            username=username,
            validation_run_id=validation_run_id,
            model_version=config.model_version,
            start_time=start_time,
            end_time=end_time,
            log_type=config.log_type,
        )
    except Exception as exc:
        result["error"] = f"清理前 count 失败: {type(exc).__name__}: {exc}"
        return result

    if result["before_count"] == 0:
        result["success"] = True
        result["after_count"] = 0
        return result

    # 执行 DELETE（参数化，精确 username =）
    delete_sql = f"""
    ALTER TABLE {config.clickhouse_database}.{CLEANUP_TABLE}
    DELETE WHERE username = %(username)s
        AND validation_run_id = %(validation_run_id)s
        AND baseline_model_version = %(model_version)s
        AND timestamp >= %(start_time)s
        AND timestamp < %(end_time)s
        AND log_type = %(log_type)s
    SETTINGS mutations_sync = 1
    """
    delete_params = {
        "username": username,
        "validation_run_id": validation_run_id,
        "model_version": config.model_version,
        "start_time": start_time,
        "end_time": end_time,
        "log_type": config.log_type,
    }

    try:
        client.command(delete_sql, parameters=delete_params)
    except Exception as exc:
        result["error"] = f"清理 DELETE 失败: {type(exc).__name__}: {exc}"
        return result

    # 清理后 count
    try:
        result["after_count"] = _count_validation_results(
            client=client,
            database=config.clickhouse_database,
            username=username,
            validation_run_id=validation_run_id,
            model_version=config.model_version,
            start_time=start_time,
            end_time=end_time,
            log_type=config.log_type,
        )
    except Exception as exc:
        result["error"] = f"清理后 count 失败: {type(exc).__name__}: {exc}"
        return result

    if result["after_count"] != 0:
        result["error"] = f"清理后仍有 {result['after_count']} 行残留"
        return result

    result["success"] = True
    return result


__all__ = ["cleanup_validation_results", "CLEANUP_TABLE"]
