"""UEBA dashboard 只读 API 适配层。

本模块为 Streamlit dashboard 提供稳定的只读查询函数。
只读 ueba_validation_results，不触发 validation、不写库、不重跑 baseline、
不修改 logs_structured / user_behavior_baselines / ueba_baseline_training_logs。
"""

from __future__ import annotations

import json
import logging
from typing import Any

from .validation_repository import UebaValidationRepository

logger = logging.getLogger(__name__)

DEFAULT_LIMIT = 100
MAX_LIMIT = 1000


def _clamp_limit(limit: int) -> int:
    """将 limit 截断到安全范围。"""
    if not isinstance(limit, int) or limit <= 0:
        return DEFAULT_LIMIT
    if limit > MAX_LIMIT:
        return MAX_LIMIT
    return limit


def _build_repository(client: Any, database: str) -> UebaValidationRepository:
    """延迟创建 repository，不在 import 时连接数据库。"""
    if client is None:
        try:
            import clickhouse_connect  # noqa: F401 — 延迟导入
        except ImportError as exc:
            raise RuntimeError(
                "缺少 clickhouse_connect 依赖，请确认 requirements.txt 已安装 clickhouse-connect。"
            ) from exc
        client = clickhouse_connect.get_client(database=database)
    return UebaValidationRepository(client=client, database=database)


def _error(code: str, message: str, filters: dict[str, Any]) -> dict[str, Any]:
    """生成统一错误结构。"""
    return {"success": False, "error": {"code": code, "message": message}, "filters": filters}


def _parse_reasons(value: Any) -> list[dict[str, Any]]:
    """安全解析 ueba_anomaly_reasons JSON 字符串。"""
    if value is None:
        return []
    if isinstance(value, list):
        return value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
            if isinstance(parsed, list):
                return parsed
        except (json.JSONDecodeError, TypeError):
            pass
    return []


# ---------------------------------------------------------------------------
# 公共只读查询函数
# ---------------------------------------------------------------------------


def get_validation_summary(
    *,
    client: Any = None,
    database: str = "log_analysis",
    start_time: str,
    end_time: str,
    model_version: str,
    log_type: str = "vpn",
    validation_run_id: str | None = None,
    limit: int = MAX_LIMIT,
) -> dict[str, Any]:
    """返回指定窗口内 validation 结果的聚合摘要。

    不写库、不触发 validation、不重跑 baseline。

    Returns:
        dict 包含 success / error / filters / summary 四个顶层键。
        summary 中 risk_counts 和 status_counts 的未知值归入 "UNKNOWN"。
    """
    safe_limit = _clamp_limit(limit)
    filters: dict[str, Any] = {
        "start_time": start_time,
        "end_time": end_time,
        "model_version": model_version,
        "log_type": log_type,
        "validation_run_id": validation_run_id,
        "limit": safe_limit,
    }
    try:
        repo = _build_repository(client, database)
        rows = repo.query_validation_results(
            start_time=start_time,
            end_time=end_time,
            model_version=model_version,
            log_type=log_type,
            validation_run_id=validation_run_id,
            limit=safe_limit,
        )
    except Exception as exc:
        logger.exception("get_validation_summary 查询失败")
        return _error("UEBA_DASHBOARD_QUERY_ERROR", f"{type(exc).__name__}: {exc}", filters)

    if not rows:
        return {
            "success": True,
            "error": None,
            "filters": filters,
            "summary": _empty_summary(model_version),
        }

    risk_counts: dict[str, int] = {"LOW": 0, "MEDIUM": 0, "HIGH": 0, "CRITICAL": 0, "UNKNOWN": 0}
    status_counts: dict[str, int] = {"VALIDATED": 0, "NO_BASELINE": 0, "UNRELIABLE_BASELINE": 0, "ERROR": 0, "UNKNOWN": 0}
    scores: list[int] = []
    latest_validated_at: str | None = None
    latest_run_id: str | None = None

    for row in rows:
        risk = str(row.get("ueba_risk_level") or "").strip().upper()
        risk_counts[risk if risk in risk_counts else "UNKNOWN"] += 1

        status = str(row.get("validation_status") or "").strip().upper()
        status_counts[status if status in status_counts else "UNKNOWN"] += 1

        score_val = row.get("ueba_score")
        if isinstance(score_val, (int, float)):
            scores.append(int(score_val))

        validated = row.get("validated_at")
        if isinstance(validated, str):
            if latest_validated_at is None or validated > latest_validated_at:
                latest_validated_at = validated
                run_id = row.get("validation_run_id")
                if isinstance(run_id, str):
                    latest_run_id = run_id

    avg_score = round(sum(scores) / len(scores), 2) if scores else 0.0
    return {
        "success": True,
        "error": None,
        "filters": filters,
        "summary": {
            "total": len(rows),
            "risk_counts": risk_counts,
            "status_counts": status_counts,
            "max_score": max(scores) if scores else 0,
            "avg_score": avg_score,
            "latest_validated_at": latest_validated_at,
            "latest_validation_run_id": latest_run_id,
            "model_version": model_version,
        },
    }


def get_validation_ranking(
    *,
    client: Any = None,
    database: str = "log_analysis",
    start_time: str,
    end_time: str,
    model_version: str,
    log_type: str = "vpn",
    validation_run_id: str | None = None,
    risk_level: str | None = None,
    validation_status: str | None = None,
    limit: int = DEFAULT_LIMIT,
) -> dict[str, Any]:
    """返回按 max_score 降序的用户 UEBA validation 排行。

    在 Python 侧做用户级聚合：max_score / avg_score / event_count /
    high_risk_count / critical_count / latest_validated_at。
    排序：max_score DESC → critical_count DESC → high_risk_count DESC
         → event_count DESC → username ASC。

    不写库、不触发 validation、不重跑 baseline。
    """
    safe_limit = _clamp_limit(limit)
    filters: dict[str, Any] = {
        "start_time": start_time,
        "end_time": end_time,
        "model_version": model_version,
        "log_type": log_type,
        "validation_run_id": validation_run_id,
        "risk_level": risk_level,
        "validation_status": validation_status,
        "limit": safe_limit,
    }
    try:
        repo = _build_repository(client, database)
        rows = repo.query_validation_results(
            start_time=start_time,
            end_time=end_time,
            model_version=model_version,
            log_type=log_type,
            validation_run_id=validation_run_id,
            risk_level=risk_level,
            validation_status=validation_status,
            limit=safe_limit,
        )
    except Exception as exc:
        logger.exception("get_validation_ranking 查询失败")
        return _error("UEBA_DASHBOARD_QUERY_ERROR", f"{type(exc).__name__}: {exc}", filters)

    if not rows:
        return {"success": True, "error": None, "filters": filters, "ranking": []}

    # 按 username 聚合
    user_agg: dict[str, dict[str, Any]] = {}
    for row in rows:
        username = str(row.get("username") or "").strip()
        if not username:
            continue
        if username not in user_agg:
            user_agg[username] = {
                "scores": [],
                "risk_levels": [],
                "event_count": 0,
                "latest_validated_at": "",
                "latest_validation_run_id": None,
            }
        agg = user_agg[username]
        score_val = row.get("ueba_score", 0)
        if isinstance(score_val, (int, float)):
            agg["scores"].append(int(score_val))
        risk = str(row.get("ueba_risk_level") or "").strip().upper()
        agg["risk_levels"].append(risk)
        agg["event_count"] += 1
        validated = row.get("validated_at")
        if isinstance(validated, str) and validated > agg["latest_validated_at"]:
            agg["latest_validated_at"] = validated
            run_id = row.get("validation_run_id")
            if isinstance(run_id, str):
                agg["latest_validation_run_id"] = run_id

    ranking: list[dict[str, Any]] = []
    for username, agg in user_agg.items():
        scores = agg["scores"]
        max_score = max(scores) if scores else 0
        avg_score = round(sum(scores) / len(scores), 2) if scores else 0.0
        high_count = sum(1 for r in agg["risk_levels"] if r == "HIGH")
        critical_count = sum(1 for r in agg["risk_levels"] if r == "CRITICAL")
        overall_risk = "LOW"
        if critical_count > 0:
            overall_risk = "CRITICAL"
        elif high_count > 0:
            overall_risk = "HIGH"
        elif any(r == "MEDIUM" for r in agg["risk_levels"]):
            overall_risk = "MEDIUM"

        ranking.append({
            "username": username,
            "max_score": max_score,
            "avg_score": avg_score,
            "event_count": agg["event_count"],
            "high_risk_count": high_count,
            "critical_count": critical_count,
            "latest_validated_at": agg["latest_validated_at"] or None,
            "latest_validation_run_id": agg["latest_validation_run_id"],
            "risk_level": overall_risk,
        })

    ranking.sort(
        key=lambda r: (
            -r["max_score"],
            -r["critical_count"],
            -r["high_risk_count"],
            -r["event_count"],
            r["username"],
        )
    )
    return {"success": True, "error": None, "filters": filters, "ranking": ranking}


def get_user_validation_detail(
    *,
    client: Any = None,
    database: str = "log_analysis",
    start_time: str,
    end_time: str,
    model_version: str,
    username: str,
    log_type: str = "vpn",
    validation_run_id: str | None = None,
    source_identity: str | None = None,
    limit: int = DEFAULT_LIMIT,
) -> dict[str, Any]:
    """返回单个用户的 validation 结果事件列表。

    ueba_anomaly_reasons 从 JSON 字符串解析为 list。
    解析失败时返回空 list 并保留原始值。

    不写库、不触发 validation、不重跑 baseline。
    """
    safe_limit = _clamp_limit(limit)
    filters: dict[str, Any] = {
        "start_time": start_time,
        "end_time": end_time,
        "model_version": model_version,
        "username": username,
        "log_type": log_type,
        "validation_run_id": validation_run_id,
        "source_identity": source_identity,
        "limit": safe_limit,
    }
    try:
        repo = _build_repository(client, database)
        rows = repo.query_validation_results(
            start_time=start_time,
            end_time=end_time,
            model_version=model_version,
            log_type=log_type,
            username=username,
            validation_run_id=validation_run_id,
            source_identity=source_identity,
            limit=safe_limit,
        )
    except Exception as exc:
        logger.exception("get_user_validation_detail 查询失败")
        return _error("UEBA_DASHBOARD_QUERY_ERROR", f"{type(exc).__name__}: {exc}", filters)

    events: list[dict[str, Any]] = []
    for row in rows:
        reason_raw = row.get("ueba_anomaly_reasons")
        reason_parsed = _parse_reasons(reason_raw)
        events.append({
            "validation_id": row.get("validation_id"),
            "validation_run_id": row.get("validation_run_id"),
            "source_identity": row.get("source_identity"),
            "source_log_id": row.get("source_log_id"),
            "timestamp": row.get("timestamp"),
            "username": row.get("username"),
            "log_type": row.get("log_type"),
            "baseline_model_version": row.get("baseline_model_version"),
            "ueba_score": row.get("ueba_score"),
            "ueba_risk_level": row.get("ueba_risk_level"),
            "validation_status": row.get("validation_status"),
            "validated_at": row.get("validated_at"),
            "reason_count": len(reason_parsed),
            "ueba_anomaly_reasons": reason_parsed,
            "error": row.get("error"),
        })

    return {
        "success": True,
        "error": None,
        "filters": filters,
        "username": username,
        "events": events,
    }


def _empty_summary(model_version: str) -> dict[str, Any]:
    """空结果时的稳定摘要结构。"""
    return {
        "total": 0,
        "risk_counts": {"LOW": 0, "MEDIUM": 0, "HIGH": 0, "CRITICAL": 0, "UNKNOWN": 0},
        "status_counts": {"VALIDATED": 0, "NO_BASELINE": 0, "UNRELIABLE_BASELINE": 0, "ERROR": 0, "UNKNOWN": 0},
        "max_score": 0,
        "avg_score": 0.0,
        "latest_validated_at": None,
        "latest_validation_run_id": None,
        "model_version": model_version,
    }


__all__ = [
    "get_validation_summary",
    "get_validation_ranking",
    "get_user_validation_detail",
]
