"""UEBA dashboard 只读 API 适配层。

本模块为 Streamlit dashboard 提供稳定的只读查询函数。
只读 ueba_validation_results 和 user_behavior_baselines，
不触发 validation、不写库、不重跑 baseline、
不修改 logs_structured / user_behavior_baselines / ueba_baseline_training_logs。

提供 baseline 摘要、默认参数、近期风险事件、增强字段回查。
"""

from __future__ import annotations

import json
import logging
from typing import Any

from .config import UebaBaselineConfig
from .baseline_store import BaselineStore
from .validation_repository import UebaValidationRepository

logger = logging.getLogger(__name__)

# 冻结默认值
DEFAULT_RECENT_RISK_LIMIT = 20
DEFAULT_RANKING_LIMIT = 20
DEFAULT_USER_DETAIL_LIMIT = 50
MAX_QUERY_LIMIT = 1000


def _clamp_limit(limit: int, default: int = 20) -> int:
    """安全归一化 limit：bool/非整数/负数/零回退到默认值，超上限截断。"""
    if isinstance(limit, bool) or not isinstance(limit, int) or limit <= 0:
        return default
    if limit > MAX_QUERY_LIMIT:
        return MAX_QUERY_LIMIT
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
    repo = UebaValidationRepository(client=client, database=database)
    repo.ensure_table()
    return repo


def _build_baseline_store(client: Any, database: str) -> BaselineStore:
    """延迟创建 BaselineStore。"""
    if client is None:
        try:
            import clickhouse_connect  # noqa: F401
        except ImportError as exc:
            raise RuntimeError(
                "缺少 clickhouse_connect 依赖，请确认 requirements.txt 已安装 clickhouse-connect。"
            ) from exc
        client = clickhouse_connect.get_client(database=database)
    store = BaselineStore(client=client, database=database)
    store.ensure_table()
    return store


def _error(code: str, message: str, filters: dict[str, Any]) -> dict[str, Any]:
    """生成统一错误结构。"""
    return {"success": False, "error": {"code": code, "message": message}, "filters": filters}


def _fail(
    code: str,
    filters: dict[str, Any],
    exc: Exception,
) -> dict[str, Any]:
    """记录完整异常后返回脱敏错误结构。"""
    logger.exception("UEBA dashboard API error")
    return _error(code, "UEBA dashboard query failed", filters)


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


def _safe_parse_json(value: Any) -> dict[str, Any] | list[Any]:
    """安全解析 JSON 字段，失败时返回空结构。"""
    if value is None:
        return {}
    if isinstance(value, (dict, list)):
        return value
    if isinstance(value, str):
        try:
            return json.loads(value)
        except (json.JSONDecodeError, TypeError):
            pass
    return {}


# ---------------------------------------------------------------------------
# 内部上下文解析
# ---------------------------------------------------------------------------


def _resolve_validation_context(
    client: Any,
    database: str,
    model_version: str | None,
    validation_run_id: str | None,
    log_type: str,
) -> dict[str, Any]:
    """根据优先规则解析 model_version 和 validation_run_id。

    优先级：
    1. 显式参数 → 直接使用
    2. 根据 run_id 推导 model_version
    3. 双缺省 → 最新 validation batch
    4. 无 validation → 回退到最新 baseline

    本函数捕获自身所有异常，通过返回 dict 中的 _error 键通知调用方。
    """
    resolved = {
        "model_version": model_version,
        "validation_run_id": validation_run_id,
        "resolved_from": "explicit",
    }

    try:
        repo = _build_repository(client, database)

        if validation_run_id is not None and model_version is None:
            ctx = repo.get_latest_validation_context(
                log_type=log_type,
                validation_run_id=validation_run_id,
            )
            if ctx is not None:
                resolved["model_version"] = ctx["baseline_model_version"]
                resolved["resolved_from"] = "run_id"

        if resolved["model_version"] is None and validation_run_id is None:
            ctx = repo.get_latest_validation_context(log_type=log_type)
            if ctx is not None:
                resolved["model_version"] = ctx["baseline_model_version"]
                resolved["validation_run_id"] = ctx["validation_run_id"]
                resolved["resolved_from"] = "latest_validation"

        if resolved["model_version"] is None:
            store = _build_baseline_store(client, database)
            resolved["model_version"] = store.get_latest_model_version()
            resolved["resolved_from"] = "latest_baseline"

    except Exception as exc:
        logger.exception("_resolve_validation_context 失败")
        resolved["_error"] = _fail("UEBA_DASHBOARD_QUERY_ERROR", {
            "model_version": model_version,
            "validation_run_id": validation_run_id,
            "log_type": log_type,
        }, exc)

    return resolved


# ---------------------------------------------------------------------------
# source_log_id 增强字段回查
# ---------------------------------------------------------------------------


def _enrich_events_with_source_logs(
    events: list[dict[str, Any]],
    repo: UebaValidationRepository,
) -> list[dict[str, Any]]:
    """为事件列表批量回查增强字段。"""
    if not events:
        return events

    source_log_ids: list[int] = []
    for ev in events:
        sl = ev.get("source_log_id")
        if isinstance(sl, int) and sl > 0:
            source_log_ids.append(sl)
        elif isinstance(sl, str):
            try:
                sl_int = int(sl)
                if sl_int > 0:
                    source_log_ids.append(sl_int)
            except (ValueError, TypeError):
                pass

    details = repo.fetch_source_log_details(source_log_ids)

    _PLACEHOLDER = "--"
    _LOCATION_UNAVAILABLE = "原始日志不可用"

    for ev in events:
        sl = ev.get("source_log_id")
        sl_int = 0
        if isinstance(sl, int):
            sl_int = sl
        elif isinstance(sl, str):
            try:
                sl_int = int(sl)
            except (ValueError, TypeError):
                sl_int = 0

        matched_rows = details.get(sl_int, []) if sl_int > 0 else []
        detail = matched_rows[0] if len(matched_rows) == 1 else None

        if detail is not None:
            ev["source_ip"] = detail.get("source_ip") or _PLACEHOLDER
            ev["destination_ip"] = detail.get("destination_ip") or _PLACEHOLDER
            ev["source_country"] = detail.get("src_country") or _PLACEHOLDER
            ev["source_city"] = detail.get("src_city") or _PLACEHOLDER
            ev["location"] = detail.get("src_city") or detail.get("src_country") or _PLACEHOLDER
            ev["vpn_gateway"] = detail.get("vpn_gateway") or _PLACEHOLDER
            ev["auth_method"] = detail.get("auth_method") or _PLACEHOLDER
            ev["client_software"] = detail.get("client_software") or _PLACEHOLDER
            ev["protocol"] = detail.get("protocol") or _PLACEHOLDER
            ev["raw_log_available"] = bool(detail.get("raw_log_available"))
        else:
            ev["source_ip"] = _PLACEHOLDER
            ev["destination_ip"] = _PLACEHOLDER
            ev["source_country"] = _PLACEHOLDER
            ev["source_city"] = _PLACEHOLDER
            ev["location"] = _PLACEHOLDER if sl_int > 0 else _LOCATION_UNAVAILABLE
            ev["vpn_gateway"] = _PLACEHOLDER
            ev["auth_method"] = _PLACEHOLDER
            ev["client_software"] = _PLACEHOLDER
            ev["protocol"] = _PLACEHOLDER
            ev["raw_log_available"] = False

    return events


# ---------------------------------------------------------------------------
# 公共只读查询函数
# ---------------------------------------------------------------------------


def get_validation_summary(
    *,
    client: Any = None,
    database: str = "log_analysis",
    start_time: str,
    end_time: str,
    model_version: str | None = None,
    log_type: str = "vpn",
    validation_run_id: str | None = None,
) -> dict[str, Any]:
    """返回指定窗口内 validation 结果的聚合摘要（数据库侧聚合，不截断）。"""
    filters: dict[str, Any] = {
        "start_time": start_time,
        "end_time": end_time,
        "model_version": model_version,
        "log_type": log_type,
        "validation_run_id": validation_run_id,
    }

    resolved = _resolve_validation_context(
        client, database, model_version, validation_run_id, log_type,
    )
    if resolved.get("_error") is not None:
        return resolved["_error"]

    effective_model = resolved["model_version"]
    effective_run_id = resolved.get("validation_run_id") or validation_run_id
    filters["model_version_resolved"] = effective_model
    if resolved["resolved_from"] != "explicit":
        filters["validation_run_id_resolved"] = effective_run_id

    if effective_model is None:
        return {
            "success": True,
            "error": None,
            "filters": filters,
            "summary": _empty_summary(None),
        }

    try:
        repo = _build_repository(client, database)
        agg = repo.fetch_validation_summary(
            start_time=start_time,
            end_time=end_time,
            model_version=effective_model,
            log_type=log_type,
            validation_run_id=effective_run_id,
        )

        if agg is None:
            return {
                "success": True,
                "error": None,
                "filters": filters,
                "summary": _empty_summary(effective_model),
            }

        total = int(agg.get("total", 0))

        if total == 0:
            return {
                "success": True,
                "error": None,
                "filters": filters,
                "summary": _empty_summary(effective_model),
            }

        risk_low = int(agg.get("risk_low", 0))
        risk_medium = int(agg.get("risk_medium", 0))
        risk_high = int(agg.get("risk_high", 0))
        risk_critical = int(agg.get("risk_critical", 0))
        risk_unknown = max(0, total - risk_low - risk_medium - risk_high - risk_critical)

        status_validated = int(agg.get("status_validated", 0))
        status_no_baseline = int(agg.get("status_no_baseline", 0))
        status_unreliable = int(agg.get("status_unreliable", 0))
        status_error = int(agg.get("status_error", 0))
        status_unknown = max(0, total - status_validated - status_no_baseline - status_unreliable - status_error)

        risk_counts = {
            "LOW": risk_low,
            "MEDIUM": risk_medium,
            "HIGH": risk_high,
            "CRITICAL": risk_critical,
            "UNKNOWN": risk_unknown,
        }
        status_counts = {
            "VALIDATED": status_validated,
            "NO_BASELINE": status_no_baseline,
            "UNRELIABLE_BASELINE": status_unreliable,
            "ERROR": status_error,
            "UNKNOWN": status_unknown,
        }

        max_score = int(agg.get("max_score", 0))
        avg_score = round(float(agg.get("avg_score", 0)), 2)
        latest_validated_at = agg.get("latest_validated_at")
        latest_run_id = agg.get("latest_validation_run_id")

    except Exception as exc:
        logger.exception("get_validation_summary 查询或映射失败")
        return _fail("UEBA_DASHBOARD_QUERY_ERROR", filters, exc)

    return {
        "success": True,
        "error": None,
        "filters": filters,
        "summary": {
            "total": total,
            "risk_counts": risk_counts,
            "status_counts": status_counts,
            "no_baseline_count": status_counts.get("NO_BASELINE", 0),
            "max_score": max_score,
            "avg_score": avg_score,
            "latest_validated_at": latest_validated_at,
            "latest_validation_run_id": latest_run_id,
            "model_version": effective_model or model_version or "",
        },
    }


def get_validation_ranking(
    *,
    client: Any = None,
    database: str = "log_analysis",
    start_time: str,
    end_time: str,
    model_version: str | None = None,
    log_type: str = "vpn",
    validation_run_id: str | None = None,
    risk_level: str | None = None,
    validation_status: str | None = None,
    username: str | None = None,
    limit: int = DEFAULT_RANKING_LIMIT,
) -> dict[str, Any]:
    """返回按 max_score 降序的用户 UEBA validation 排行。

    对完整筛选窗口聚合，按用户维度计算 max_score 后排序，
    最后截断到前 limit 个用户。不在用户聚合前按事件数截断。
    """
    safe_limit = _clamp_limit(limit, default=DEFAULT_RANKING_LIMIT)
    filters: dict[str, Any] = {
        "start_time": start_time,
        "end_time": end_time,
        "model_version": model_version,
        "log_type": log_type,
        "validation_run_id": validation_run_id,
        "risk_level": risk_level,
        "validation_status": validation_status,
        "username": username,
        "limit": safe_limit,
    }

    resolved = _resolve_validation_context(
        client, database, model_version, validation_run_id, log_type,
    )
    if resolved.get("_error") is not None:
        return resolved["_error"]

    effective_model = resolved["model_version"]
    effective_run_id = resolved.get("validation_run_id") or validation_run_id

    if effective_model is None:
        return {"success": True, "error": None, "filters": filters, "ranking": []}

    try:
        repo = _build_repository(client, database)
        ranking_rows = repo.fetch_validation_ranking(
            start_time=start_time,
            end_time=end_time,
            model_version=effective_model,
            log_type=log_type,
            validation_run_id=effective_run_id,
            risk_level=risk_level,
            validation_status=validation_status,
            username=username,
            limit=safe_limit,
        )

        ranking: list[dict[str, Any]] = []
        for row in ranking_rows:
            ranking.append({
                "username": str(row.get("username", "")),
                "max_score": int(row.get("max_score", 0)),
                "avg_score": round(float(row.get("avg_score", 0)), 2),
                "event_count": int(row.get("event_count", 0)),
                "high_risk_count": int(row.get("high_risk_count", 0)),
                "critical_count": int(row.get("critical_count", 0)),
                "latest_validated_at": row.get("latest_validated_at"),
                "latest_validation_run_id": row.get("latest_validation_run_id"),
                "risk_level": str(row.get("overall_risk", "LOW")),
            })
    except Exception as exc:
        logger.exception("get_validation_ranking 查询或映射失败")
        return _fail("UEBA_DASHBOARD_QUERY_ERROR", filters, exc)

    return {"success": True, "error": None, "filters": filters, "ranking": ranking}


def get_user_validation_detail(
    *,
    client: Any = None,
    database: str = "log_analysis",
    start_time: str,
    end_time: str,
    model_version: str | None = None,
    username: str,
    log_type: str = "vpn",
    validation_run_id: str | None = None,
    source_identity: str | None = None,
    limit: int = DEFAULT_USER_DETAIL_LIMIT,
) -> dict[str, Any]:
    """返回单个用户的 validation 结果事件列表（含增强字段）。"""
    safe_limit = _clamp_limit(limit, default=DEFAULT_USER_DETAIL_LIMIT)
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

    resolved = _resolve_validation_context(
        client, database, model_version, validation_run_id, log_type,
    )
    if resolved.get("_error") is not None:
        return resolved["_error"]

    effective_model = resolved["model_version"]
    effective_run_id = resolved.get("validation_run_id") or validation_run_id

    if effective_model is None:
        return {
            "success": True,
            "error": None,
            "filters": filters,
            "username": username,
            "events": [],
        }

    try:
        repo = _build_repository(client, database)
        rows = repo.query_validation_results(
            start_time=start_time,
            end_time=end_time,
            model_version=effective_model,
            log_type=log_type,
            username=username,
            validation_run_id=effective_run_id,
            source_identity=source_identity,
            limit=safe_limit,
        )
    except Exception as exc:
        logger.exception("get_user_validation_detail 查询失败")
        return _fail("UEBA_DASHBOARD_QUERY_ERROR", filters, exc)

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
            "baseline_is_reliable": bool(row.get("baseline_is_reliable")) if row.get("baseline_is_reliable") is not None else None,
            "ueba_score": row.get("ueba_score"),
            "ueba_risk_level": row.get("ueba_risk_level"),
            "validation_status": row.get("validation_status"),
            "validated_at": row.get("validated_at"),
            "reason_count": len(reason_parsed),
            "ueba_anomaly_reasons": reason_parsed,
            "error": row.get("error"),
        })

    try:
        events = _enrich_events_with_source_logs(events, repo)
    except Exception as exc:
        logger.exception("get_user_validation_detail 增强字段回查失败")
        return _fail("UEBA_DASHBOARD_QUERY_ERROR", filters, exc)

    return {
        "success": True,
        "error": None,
        "filters": filters,
        "username": username,
        "events": events,
    }


# ---------------------------------------------------------------------------
# baseline 相关只读查询
# ---------------------------------------------------------------------------


def get_baseline_summary(
    *,
    client: Any = None,
    database: str = "log_analysis",
    model_version: str | None = None,
) -> dict[str, Any]:
    """返回当前 baseline 聚合摘要。"""
    filters: dict[str, Any] = {"model_version": model_version}
    try:
        store = _build_baseline_store(client, database)
        repo = _build_repository(client, database)

        effective_model = model_version
        resolved_from = "explicit"

        if effective_model is None:
            ctx = repo.get_latest_validation_context()
            if ctx is not None:
                effective_model = ctx["baseline_model_version"]
                resolved_from = "latest_validation"
            else:
                effective_model = store.get_latest_model_version()
                resolved_from = "latest_baseline"

        filters["model_version_resolved"] = effective_model
        filters["resolved_from"] = resolved_from

        if effective_model is None:
            return {"success": True, "error": None, "filters": filters, "baseline": None}

        summary = store.get_baseline_summary(effective_model)
        if summary is None:
            return {"success": True, "error": None, "filters": filters, "baseline": None}

        summary["log_type"] = "vpn"
        summary["log_type_source"] = "ueba_v1_fixed"

    except Exception as exc:
        logger.exception("get_baseline_summary 查询失败")
        return _fail("UEBA_DASHBOARD_QUERY_ERROR", filters, exc)

    return {"success": True, "error": None, "filters": filters, "baseline": summary}


def get_baseline_default_parameters() -> dict[str, Any]:
    """返回当前运行默认参数（从 UebaBaselineConfig 读取）。"""
    try:
        config = UebaBaselineConfig()
    except Exception as exc:
        logger.exception("读取默认参数失败")
        return {
            "success": False,
            "error": {"code": "CONFIG_ERROR", "message": "无法读取 UEBA 准线默认参数"},
        }

    return {
        "success": True,
        "error": None,
        "parameters": {
            "min_sample_count": config.min_sample_count,
            "common_hour_min_ratio": config.common_hour_min_ratio,
            "top_source_ip_limit": config.top_source_ip_limit,
            "top_source_city_limit": config.top_source_city_limit,
        },
        "display_labels": {
            "min_sample_count": "最低样本数（可靠性判定）",
            "common_hour_min_ratio": "活跃时段阈值",
            "top_source_ip_limit": "常用来源 IP TopN",
            "top_source_city_limit": "常用地点 TopN",
        },
    }


def get_baseline_detail(
    *,
    username: str,
    client: Any = None,
    database: str = "log_analysis",
    model_version: str | None = None,
) -> dict[str, Any]:
    """返回单个用户的 baseline 详情。"""
    filters: dict[str, Any] = {"username": username, "model_version": model_version}
    try:
        store = _build_baseline_store(client, database)
        repo = _build_repository(client, database)
        effective_model = model_version

        if effective_model is None:
            ctx = repo.get_latest_validation_context()
            if ctx is not None:
                effective_model = ctx["baseline_model_version"]
            else:
                effective_model = store.get_latest_model_version()

        filters["model_version_resolved"] = effective_model

        if effective_model is None:
            return {"success": True, "error": None, "filters": filters, "baseline": None}

        row = store.get_user_baseline(username, model_version=effective_model)
        if row is None:
            return {"success": True, "error": None, "filters": filters, "baseline": None}

        baseline_info: dict[str, Any] = {
            "username": str(row.get("username", "")),
            "sample_count": int(row.get("sample_count", 0)),
            "is_reliable": bool(row.get("is_reliable")),
            "baseline_start_time": row.get("baseline_start_time"),
            "baseline_end_time": row.get("baseline_end_time"),
            "model_version": str(row.get("model_version", "")),
            "created_at": row.get("created_at"),
            "failed_rate": float(row.get("failed_rate", 0)),
            "off_hours_rate": float(row.get("off_hours_rate", 0)),
            "unusual_ip_rate": float(row.get("unusual_ip_rate", 0)),
            "avg_daily_events": float(row.get("avg_daily_events", 0)),
            "common_source_ips": _safe_parse_json(row.get("common_source_ips")),
            "common_source_cities": _safe_parse_json(row.get("common_source_cities")),
            "common_source_countries": _safe_parse_json(row.get("common_source_countries")),
            "common_vpn_gateways": _safe_parse_json(row.get("common_vpn_gateways")),
        }
    except Exception as exc:
        logger.exception("get_baseline_detail 查询失败")
        return _fail("UEBA_DASHBOARD_QUERY_ERROR", filters, exc)

    return {"success": True, "error": None, "filters": filters, "baseline": baseline_info}


# ---------------------------------------------------------------------------
# 近期风险行为与通用查询
# ---------------------------------------------------------------------------


def get_recent_risk_events(
    *,
    start_time: str,
    end_time: str,
    client: Any = None,
    database: str = "log_analysis",
    model_version: str | None = None,
    log_type: str = "vpn",
    validation_run_id: str | None = None,
    username: str | None = None,
    risk_level: str | None = None,
    validation_status: str | None = None,
    limit: int = DEFAULT_RECENT_RISK_LIMIT,
) -> dict[str, Any]:
    """返回近期风险事件（默认排除 NO_BASELINE 和完全正常事件）。"""
    safe_limit = _clamp_limit(limit, default=DEFAULT_RECENT_RISK_LIMIT)
    filters: dict[str, Any] = {
        "start_time": start_time,
        "end_time": end_time,
        "model_version": model_version,
        "log_type": log_type,
        "validation_run_id": validation_run_id,
        "username": username,
        "risk_level": risk_level,
        "validation_status": validation_status,
        "limit": safe_limit,
    }

    resolved = _resolve_validation_context(
        client, database, model_version, validation_run_id, log_type,
    )
    if resolved.get("_error") is not None:
        return resolved["_error"]

    effective_model = resolved["model_version"]
    effective_run_id = resolved.get("validation_run_id") or validation_run_id

    if effective_model is None:
        return {"success": True, "error": None, "filters": filters, "events": []}

    try:
        repo = _build_repository(client, database)
        rows = repo.query_recent_risk_events(
            start_time=start_time,
            end_time=end_time,
            model_version=effective_model,
            log_type=log_type,
            validation_run_id=effective_run_id,
            username=username,
            risk_level=risk_level,
            validation_status=validation_status,
            limit=safe_limit,
        )
    except Exception as exc:
        logger.exception("get_recent_risk_events 查询失败")
        return _fail("UEBA_DASHBOARD_QUERY_ERROR", filters, exc)

    events: list[dict[str, Any]] = []
    for row in rows:
        reason_parsed = _parse_reasons(row.get("ueba_anomaly_reasons"))
        events.append({
            "validation_id": row.get("validation_id"),
            "validation_run_id": row.get("validation_run_id"),
            "timestamp": row.get("timestamp"),
            "username": row.get("username"),
            "log_type": row.get("log_type"),
            "source_identity": row.get("source_identity"),
            "source_log_id": row.get("source_log_id"),
            "ueba_score": row.get("ueba_score"),
            "ueba_risk_level": row.get("ueba_risk_level"),
            "validation_status": row.get("validation_status"),
            "validated_at": row.get("validated_at"),
            "reason_count": len(reason_parsed),
            "ueba_anomaly_reasons": reason_parsed,
        })

    try:
        events = _enrich_events_with_source_logs(events, repo)
    except Exception as exc:
        logger.exception("get_recent_risk_events 增强字段回查失败")
        return _fail("UEBA_DASHBOARD_QUERY_ERROR", filters, exc)

    return {"success": True, "error": None, "filters": filters, "events": events}


def query_validation_events(
    *,
    start_time: str,
    end_time: str,
    client: Any = None,
    database: str = "log_analysis",
    model_version: str | None = None,
    log_type: str = "vpn",
    validation_run_id: str | None = None,
    username: str | None = None,
    risk_level: str | None = None,
    validation_status: str | None = None,
    limit: int = DEFAULT_USER_DETAIL_LIMIT,
) -> dict[str, Any]:
    """通用 validation 事件查询（不自动排除 NO_BASELINE 或 normal）。"""
    safe_limit = _clamp_limit(limit, default=DEFAULT_USER_DETAIL_LIMIT)
    filters: dict[str, Any] = {
        "start_time": start_time,
        "end_time": end_time,
        "model_version": model_version,
        "log_type": log_type,
        "validation_run_id": validation_run_id,
        "username": username,
        "risk_level": risk_level,
        "validation_status": validation_status,
        "limit": safe_limit,
    }

    resolved = _resolve_validation_context(
        client, database, model_version, validation_run_id, log_type,
    )
    if resolved.get("_error") is not None:
        return resolved["_error"]

    effective_model = resolved["model_version"]
    effective_run_id = resolved.get("validation_run_id") or validation_run_id

    if effective_model is None:
        return {"success": True, "error": None, "filters": filters, "events": []}

    try:
        repo = _build_repository(client, database)
        rows = repo.query_validation_events_ordered(
            start_time=start_time,
            end_time=end_time,
            model_version=effective_model,
            log_type=log_type,
            validation_run_id=effective_run_id,
            username=username,
            risk_level=risk_level,
            validation_status=validation_status,
            limit=safe_limit,
        )
    except Exception as exc:
        logger.exception("query_validation_events 查询失败")
        return _fail("UEBA_DASHBOARD_QUERY_ERROR", filters, exc)

    events: list[dict[str, Any]] = []
    for row in rows:
        reason_parsed = _parse_reasons(row.get("ueba_anomaly_reasons"))
        events.append({
            "validation_id": row.get("validation_id"),
            "validation_run_id": row.get("validation_run_id"),
            "timestamp": row.get("timestamp"),
            "username": row.get("username"),
            "log_type": row.get("log_type"),
            "source_identity": row.get("source_identity"),
            "source_log_id": row.get("source_log_id"),
            "ueba_score": row.get("ueba_score"),
            "ueba_risk_level": row.get("ueba_risk_level"),
            "validation_status": row.get("validation_status"),
            "validated_at": row.get("validated_at"),
            "reason_count": len(reason_parsed),
            "ueba_anomaly_reasons": reason_parsed,
        })

    try:
        events = _enrich_events_with_source_logs(events, repo)
    except Exception as exc:
        logger.exception("query_validation_events 增强字段回查失败")
        return _fail("UEBA_DASHBOARD_QUERY_ERROR", filters, exc)

    return {"success": True, "error": None, "filters": filters, "events": events}


def _empty_summary(model_version: str | None) -> dict[str, Any]:
    """空结果时的稳定摘要结构。"""
    return {
        "total": 0,
        "risk_counts": {"LOW": 0, "MEDIUM": 0, "HIGH": 0, "CRITICAL": 0, "UNKNOWN": 0},
        "status_counts": {"VALIDATED": 0, "NO_BASELINE": 0, "UNRELIABLE_BASELINE": 0, "ERROR": 0, "UNKNOWN": 0},
        "no_baseline_count": 0,
        "max_score": 0,
        "avg_score": 0.0,
        "latest_validated_at": None,
        "latest_validation_run_id": None,
        "model_version": model_version or "",
    }


def analyze_behavior_for_frontend(
    *,
    start_time: str,
    end_time: str,
    client: Any = None,
    database: str = "log_analysis",
    model_version: str | None = None,
    log_type: str = "vpn",
    validation_run_id: str | None = None,
    username: str | None = None,
    risk_level: str | None = None,
    limit: int = 50,
) -> dict[str, Any]:
    """为前端提供统一的行为分析结果。
    
    组合多个查询，返回用户排行和详情数据。
    """
    filters: dict[str, Any] = {
        "start_time": start_time,
        "end_time": end_time,
        "model_version": model_version,
        "log_type": log_type,
        "validation_run_id": validation_run_id,
        "username": username,
        "risk_level": risk_level,
        "limit": limit,
    }
    
    try:
        # 获取排行数据
        ranking_result = get_validation_ranking(
            client=client,
            database=database,
            start_time=start_time,
            end_time=end_time,
            model_version=model_version,
            log_type=log_type,
            validation_run_id=validation_run_id,
            risk_level=risk_level,
            limit=limit,
        )
        
        if not ranking_result.get("success"):
            return ranking_result
        
        # 获取摘要数据
        summary_result = get_validation_summary(
            client=client,
            database=database,
            start_time=start_time,
            end_time=end_time,
            model_version=model_version,
            log_type=log_type,
            validation_run_id=validation_run_id,
        )
        
        return {
            "success": True,
            "error": None,
            "filters": filters,
            "ranking": ranking_result.get("ranking", []),
            "summary": summary_result.get("summary", {}),
        }
    
    except Exception as exc:
        logger.exception("analyze_behavior_for_frontend 失败")
        return _fail("UEBA_DASHBOARD_QUERY_ERROR", filters, exc)


def analyze_behavior_from_clickhouse(
    *,
    start_time: str,
    end_time: str,
    client: Any = None,
    database: str = "log_analysis",
    model_version: str | None = None,
    log_type: str = "vpn",
    validation_run_id: str | None = None,
    username: str | None = None,
    risk_level: str | None = None,
    validation_status: str | None = None,
    limit: int = 100,
) -> dict[str, Any]:
    """从 ClickHouse 读取并分析行为数据。
    
    返回验证事件列表，支持多种筛选条件。
    """
    filters: dict[str, Any] = {
        "start_time": start_time,
        "end_time": end_time,
        "model_version": model_version,
        "log_type": log_type,
        "validation_run_id": validation_run_id,
        "username": username,
        "risk_level": risk_level,
        "validation_status": validation_status,
        "limit": limit,
    }
    
    try:
        # 使用通用查询函数获取事件
        events_result = query_validation_events(
            client=client,
            database=database,
            start_time=start_time,
            end_time=end_time,
            model_version=model_version,
            log_type=log_type,
            validation_run_id=validation_run_id,
            username=username,
            risk_level=risk_level,
            validation_status=validation_status,
            limit=limit,
        )
        
        if not events_result.get("success"):
            return events_result
        
        # 统计分析
        events = events_result.get("events", [])
        risk_distribution = {}
        for event in events:
            level = event.get("ueba_risk_level", "UNKNOWN")
            risk_distribution[level] = risk_distribution.get(level, 0) + 1
        
        return {
            "success": True,
            "error": None,
            "filters": filters,
            "events": events,
            "count": len(events),
            "risk_distribution": risk_distribution,
        }
    
    except Exception as exc:
        logger.exception("analyze_behavior_from_clickhouse 失败")
        return _fail("UEBA_DASHBOARD_QUERY_ERROR", filters, exc)


__all__ = [
    "get_validation_summary",
    "get_validation_ranking",
    "get_user_validation_detail",
    "get_baseline_summary",
    "get_baseline_default_parameters",
    "get_baseline_detail",
    "get_recent_risk_events",
    "query_validation_events",
    "analyze_behavior_for_frontend",
    "analyze_behavior_from_clickhouse",
]
