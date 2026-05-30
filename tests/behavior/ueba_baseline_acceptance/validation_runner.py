"""验收用 validation runner —— 调用正式 CLI 执行 UEBA validation。

本模块只供验收使用，不是正式应用入口。
"""

from __future__ import annotations

import json
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

from .config import AcceptanceConfig
from .report_writer import ensure_output_dir, write_json

VALIDATION_REPORT_FILE = "validation_run_result.json"


def _build_cli_args(
    config: AcceptanceConfig,
    validation_run_id: str,
    start_time: str,
    end_time: str,
    *,
    sample_size: int = 100,
) -> list[str]:
    """构造 run_ueba_validation.py CLI 参数列表。"""
    return [
        sys.executable,
        str(Path(__file__).resolve().parents[3] / "scripts" / "run_ueba_validation.py"),
        "--start-time", start_time,
        "--end-time", end_time,
        "--model-version", config.model_version,
        "--validation-run-id", validation_run_id,
        "--write",
        "--log-type", config.log_type,
        "--sample-size", str(sample_size),
        "--host", config.clickhouse_host,
        "--port", str(config.clickhouse_port),
        "--username", config.clickhouse_user,
        "--password", config.clickhouse_password,
        "--database", config.clickhouse_database,
    ]


def _validate_cli_output(payload: dict[str, Any], validation_run_id: str) -> dict[str, Any]:
    """验证 CLI 输出 JSON 并提取验收字段。"""
    errors = []
    if not payload.get("success"):
        errors.append("CLI 返回 success=false")
    if payload.get("dry_run") is not False:
        errors.append("--write 模式下 dry_run 应为 false")
    actual_run_id = payload.get("validation_run_id")
    if actual_run_id != validation_run_id:
        errors.append(f"validation_run_id 不匹配: {actual_run_id} != {validation_run_id}")
    written = payload.get("written_count", 0)
    if written <= 0:
        errors.append(f"written_count={written}, 预期 > 0")

    return {
        "success": len(errors) == 0,
        "errors": errors,
        "written_count": written,
        "processed_count": payload.get("processed_count"),
        "scored_count": payload.get("scored_count"),
        "risk_level_counts": payload.get("risk_level_counts", {}),
        "validation_status_counts": payload.get("validation_status_counts", {}),
    }


def run_validation_acceptance(
    config: AcceptanceConfig,
    validation_run_id: str,
    start_time: str,
    end_time: str,
    *,
    sample_size: int = 100,
) -> dict[str, Any]:
    """运行一轮完整 validation e2e 验收。

    Returns:
        dict 包含 success / validation_run_id / model_version / written_count /
        risk_counts / status_counts / raw_cli_output / error
    """
    result: dict[str, Any] = {
        "success": False,
        "validation_run_id": validation_run_id,
        "model_version": config.model_version,
        "start_time": start_time,
        "end_time": end_time,
        "written_count": 0,
        "risk_counts": {},
        "status_counts": {},
        "raw_cli_output": None,
        "error": None,
    }

    args = _build_cli_args(config, validation_run_id, start_time, end_time, sample_size=sample_size)

    try:
        proc = subprocess.run(
            args,
            capture_output=True,
            text=True,
            timeout=120,
        )
    except subprocess.TimeoutExpired:
        result["error"] = "CLI 执行超时 (120s)"
        return result
    except Exception as exc:
        result["error"] = f"CLI 执行异常: {type(exc).__name__}: {exc}"
        return result

    # 脱敏：移除 stderr 和 stdout 中可能的密码
    safe_stderr = proc.stderr
    if config.clickhouse_password:
        safe_stderr = safe_stderr.replace(config.clickhouse_password, "***")
    safe_stdout = proc.stdout
    if config.clickhouse_password:
        safe_stdout = safe_stdout.replace(config.clickhouse_password, "***")

    if proc.returncode != 0:
        result["error"] = f"CLI 返回非零退出码 {proc.returncode}: {safe_stderr[:500]}"
        result["stderr_summary"] = safe_stderr[:200]
        return result

    try:
        payload = json.loads(safe_stdout)
    except json.JSONDecodeError as exc:
        result["error"] = f"CLI stdout 不是合法 JSON: {exc}"
        result["stdout_snippet"] = safe_stdout[:300]
        return result

    result["raw_cli_output"] = payload
    validated = _validate_cli_output(payload, validation_run_id)

    result["success"] = validated["success"]
    result["errors"] = validated["errors"]
    result["written_count"] = validated["written_count"]
    result["risk_counts"] = validated["risk_level_counts"]
    result["status_counts"] = validated["validation_status_counts"]

    return result


def write_validation_report(config: AcceptanceConfig, result: dict[str, Any]) -> Path:
    """写入 validation 运行报告。"""
    output_dir = ensure_output_dir(config)
    report_path = output_dir / VALIDATION_REPORT_FILE
    write_json(report_path, result)
    return report_path


__all__ = ["run_validation_acceptance", "write_validation_report"]
