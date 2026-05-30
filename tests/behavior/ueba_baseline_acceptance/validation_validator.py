"""验收用 validation validator —— 严格校验批次完整性、污染和场景矩阵。

只读 ueba_validation_results，不写库。
"""

from __future__ import annotations

from typing import Any

from .config import AcceptanceConfig
from .report_writer import ensure_output_dir, write_json

VALIDATION_DB_REPORT_FILE = "validation_db_report.json"


def _query_rows(client: Any, database: str, sql: str, params: dict) -> list[dict]:
    return list(client.query(sql, parameters=params).named_results())


def _compute_distributions(rows: list[dict]) -> dict[str, Any]:
    risk_counts: dict[str, int] = {}
    status_counts: dict[str, int] = {}
    reason_counts: dict[str, int] = {}
    user_results: dict[str, list[dict]] = {}
    high_risk_users: list[str] = []

    import json
    for row in rows:
        risk = str(row.get("ueba_risk_level") or "UNKNOWN")
        risk_counts[risk] = risk_counts.get(risk, 0) + 1
        status = str(row.get("validation_status") or "UNKNOWN")
        status_counts[status] = status_counts.get(status, 0) + 1
        username = str(row.get("username") or "")
        user_results.setdefault(username, []).append(row)
        if risk in ("HIGH", "CRITICAL") and username not in high_risk_users:
            high_risk_users.append(username)
        reasons_raw = row.get("ueba_anomaly_reasons")
        if isinstance(reasons_raw, str):
            try:
                reasons_raw = json.loads(reasons_raw)
            except (json.JSONDecodeError, TypeError):
                reasons_raw = []
        if isinstance(reasons_raw, list):
            for r in reasons_raw:
                if isinstance(r, dict):
                    code = r.get("code", "UNKNOWN")
                    reason_counts[code] = reason_counts.get(code, 0) + 1

    return {
        "total": len(rows),
        "risk_counts": risk_counts,
        "status_counts": status_counts,
        "reason_counts": reason_counts,
        "high_risk_users": high_risk_users,
        "user_count": len(user_results),
    }


def validate_validation_results(
    client: Any,
    config: AcceptanceConfig,
    validation_run_id: str,
    start_time: str,
    end_time: str,
    *,
    expected_written_count: int = 0,
    expected_normal_user: str | None = None,
    expected_high_risk_user: str | None = None,
    expected_no_baseline_user: str | None = None,
) -> dict[str, Any]:
    errors: list[str] = []

    base_params = {
        "run_id": validation_run_id,
        "model_version": config.model_version,
        "start_time": start_time,
        "end_time": end_time,
        "log_type": config.log_type,
    }

    # ---- Layer 1: 全批次总量 ----
    sql_all = f"""
    SELECT username, ueba_score, ueba_risk_level, validation_status,
           validation_run_id, baseline_model_version, ueba_anomaly_reasons,
           source_identity, validated_at
    FROM {config.clickhouse_database}.ueba_validation_results
    WHERE validation_run_id = %(run_id)s
        AND baseline_model_version = %(model_version)s
        AND timestamp >= %(start_time)s
        AND timestamp < %(end_time)s
        AND log_type = %(log_type)s
    """
    all_rows = _query_rows(client, config.clickhouse_database, sql_all, base_params)
    all_count = len(all_rows)

    if all_count == 0:
        return {"success": False, "error": "本轮批次无任何结果", "all_result_count": 0}

    # ---- Layer 2: 非 fixture 污染 ----
    non_fixture_rows = [r for r in all_rows if not str(r.get("username", "")).startswith("fixture_user_")]
    non_fixture_count = len(non_fixture_rows)
    if non_fixture_count > 0:
        non_fixture_users = sorted(set(str(r.get("username")) for r in non_fixture_rows))
        errors.append(
            f"批次污染: {non_fixture_count} 条非 fixture 用户结果: {non_fixture_users[:10]}"
        )

    # ---- Layer 3: 数量一致性 ----
    fixture_rows = [r for r in all_rows if str(r.get("username", "")).startswith("fixture_user_")]
    fixture_count = len(fixture_rows)

    if expected_written_count != all_count:
        errors.append(
            f"CLI written_count({expected_written_count}) != DB all_count({all_count})"
        )
    if all_count <= 0:
        errors.append("all_result_count <= 0")

    # ---- Layer 4: 场景矩阵 ----
    dist = _compute_distributions(all_rows)

    if expected_normal_user:
        normal_rows = [r for r in fixture_rows if r.get("username") == expected_normal_user]
        if not normal_rows:
            errors.append(f"场景缺失: {expected_normal_user} 不存在")
        else:
            statuses = {str(r.get("validation_status", "")) for r in normal_rows}
            if "VALIDATED" not in statuses:
                errors.append(f"{expected_normal_user} 状态不是 VALIDATED: {statuses}")
            levels = {str(r.get("ueba_risk_level", "")).upper() for r in normal_rows}
            if levels != {"LOW"}:
                errors.append(f"{expected_normal_user} 风险等级不是 LOW: {levels}")

    if expected_high_risk_user:
        high_rows = [r for r in fixture_rows if r.get("username") == expected_high_risk_user]
        if not high_rows:
            errors.append(f"场景缺失: {expected_high_risk_user} 不存在")
        else:
            statuses = {str(r.get("validation_status", "")) for r in high_rows}
            if "VALIDATED" not in statuses:
                errors.append(f"{expected_high_risk_user} 状态不是 VALIDATED: {statuses}")
            levels = {str(r.get("ueba_risk_level", "")).upper() for r in high_rows}
            if not (levels & {"HIGH", "CRITICAL"}):
                errors.append(f"{expected_high_risk_user} 未达 HIGH/CRITICAL: {levels}")
            # anomaly reasons 非空
            for r in high_rows:
                reasons_raw = r.get("ueba_anomaly_reasons")
                import json
                if isinstance(reasons_raw, str):
                    try:
                        reasons_raw = json.loads(reasons_raw)
                    except Exception:
                        reasons_raw = []
                if not isinstance(reasons_raw, list) or len(reasons_raw) == 0:
                    errors.append(f"{expected_high_risk_user} anomaly reasons 为空")
                    break

        if not dist.get("high_risk_users"):
            errors.append("high_risk_users 为空")
        elif expected_high_risk_user not in dist["high_risk_users"]:
            errors.append(f"high_risk_users 不含 {expected_high_risk_user}")

    if expected_no_baseline_user:
        nobase_rows = [r for r in fixture_rows if r.get("username") == expected_no_baseline_user]
        if not nobase_rows:
            errors.append(f"场景缺失: {expected_no_baseline_user} 不存在")
        else:
            statuses = {str(r.get("validation_status", "")) for r in nobase_rows}
            if "NO_BASELINE" not in statuses:
                errors.append(f"{expected_no_baseline_user} 状态不是 NO_BASELINE: {statuses}")

    return {
        "success": len(errors) == 0,
        "errors": errors,
        "all_result_count": all_count,
        "fixture_result_count": fixture_count,
        "non_fixture_result_count": non_fixture_count,
        "distributions": dist,
    }


def write_validation_db_report(config: AcceptanceConfig, payload: dict[str, Any]) -> str:
    path = ensure_output_dir(config) / VALIDATION_DB_REPORT_FILE
    write_json(path, payload)
    return str(path)


__all__ = ["validate_validation_results", "write_validation_db_report"]
