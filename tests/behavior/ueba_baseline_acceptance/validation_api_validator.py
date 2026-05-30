"""验收用 validation API validator —— 严格校验 API 结果与 DB 一致。

不启动 Streamlit，只验证 3 个正式只读 API 函数。
"""

from __future__ import annotations

from typing import Any

from .config import AcceptanceConfig
from .report_writer import ensure_output_dir, write_json

VALIDATION_API_REPORT_FILE = "validation_api_report.json"


def validate_validation_api(
    client: Any,
    config: AcceptanceConfig,
    validation_run_id: str,
    start_time: str,
    end_time: str,
    *,
    db_distributions: dict[str, Any] | None = None,
    all_result_count: int = 0,
    expected_high_risk_user: str | None = None,
) -> dict[str, Any]:
    from src.behavior.api import get_validation_summary, get_validation_ranking, get_user_validation_detail

    errors: list[str] = []
    results: dict[str, Any] = {"summary": None, "ranking": None, "detail": None}
    db = db_distributions or {}
    database = config.clickhouse_database
    model_version = config.model_version
    log_type = config.log_type

    # -- summary --
    try:
        summary = get_validation_summary(
            client=client, database=database, start_time=start_time, end_time=end_time,
            model_version=model_version, log_type=log_type, validation_run_id=validation_run_id,
        )
        s = summary.get("summary", {})
        results["summary"] = {
            "success": summary.get("success"),
            "total": s.get("total", 0),
            "latest_validation_run_id": s.get("latest_validation_run_id"),
            "risk_counts": s.get("risk_counts", {}),
            "status_counts": s.get("status_counts", {}),
        }
        if not summary.get("success"):
            errors.append("get_validation_summary 返回 success=false")
        if all_result_count > 0 and s.get("total", 0) != all_result_count:
            errors.append(f"summary.total({s.get('total')}) != DB all({all_result_count})")
        if s.get("latest_validation_run_id") != validation_run_id:
            errors.append(f"summary run_id 不匹配")
    except Exception as exc:
        errors.append(f"get_validation_summary 异常: {type(exc).__name__}: {exc}")

    # -- ranking --
    try:
        ranking = get_validation_ranking(
            client=client, database=database, start_time=start_time, end_time=end_time,
            model_version=model_version, log_type=log_type, validation_run_id=validation_run_id,
        )
        ranking_list = ranking.get("ranking", [])
        results["ranking"] = {"success": ranking.get("success"), "ranking_count": len(ranking_list)}
        if not ranking.get("success"):
            errors.append("get_validation_ranking 返回 success=false")
        if not ranking_list:
            errors.append("ranking 为空")
        if expected_high_risk_user:
            found = [r for r in ranking_list if r.get("username") == expected_high_risk_user]
            if not found:
                errors.append(f"ranking 不含 {expected_high_risk_user}")
            else:
                rl = str(found[0].get("risk_level", "")).upper()
                if rl not in ("HIGH", "CRITICAL"):
                    errors.append(f"{expected_high_risk_user} ranking 等级={rl}, 预期 HIGH/CRITICAL")
        # 检查非 fixture 用户
        non_fixture = [r for r in ranking_list if not str(r.get("username", "")).startswith("fixture_user_")]
        if non_fixture:
            errors.append(f"API ranking 含 {len(non_fixture)} 个非 fixture 用户")
    except Exception as exc:
        errors.append(f"get_validation_ranking 异常: {type(exc).__name__}: {exc}")

    # -- detail --
    if expected_high_risk_user:
        try:
            detail = get_user_validation_detail(
                client=client, database=database, start_time=start_time, end_time=end_time,
                model_version=model_version, username=expected_high_risk_user, log_type=log_type,
                validation_run_id=validation_run_id,
            )
            events = detail.get("events", [])
            results["detail"] = {"success": detail.get("success"), "event_count": len(events)}
            if not detail.get("success"):
                errors.append("get_user_validation_detail 返回 success=false")
            if not events:
                errors.append(f"{expected_high_risk_user} detail.events 为空")
            for ev in events:
                if ev.get("validation_run_id") != validation_run_id:
                    errors.append("detail event run_id 不匹配")
                    break
                if ev.get("baseline_model_version") != model_version:
                    errors.append("detail event model_version 不匹配")
                    break
            # anomaly reasons 非空
            for ev in events:
                reasons = ev.get("ueba_anomaly_reasons", [])
                if isinstance(reasons, list) and len(reasons) > 0:
                    break
            else:
                if events:
                    errors.append(f"{expected_high_risk_user} anomaly reasons 全部为空")
        except Exception as exc:
            errors.append(f"get_user_validation_detail 异常: {type(exc).__name__}: {exc}")

    return {"success": len(errors) == 0, "errors": errors, "results": results}


def write_validation_api_report(config: AcceptanceConfig, payload: dict[str, Any]) -> str:
    path = ensure_output_dir(config) / VALIDATION_API_REPORT_FILE
    write_json(path, payload)
    return str(path)


__all__ = ["validate_validation_api", "write_validation_api_report"]
