#!/usr/bin/env bash
set -euo pipefail

# Behavior 前端接口联调测试脚本
# 使用方法：bash scripts/run_behavior_frontend_api_test.sh

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
cd "${PROJECT_ROOT}"

print_separator() {
    echo "======================================================================"
}

print_error() {
    echo "[ERROR] $1"
}

print_ok() {
    echo "[OK] $1"
}

print_info() {
    echo "[INFO] $1"
}

print_separator
echo "Behavior Frontend API Integration Test"
print_separator
echo ""

echo "[1/5] Checking project root..."
if [[ ! -d "src" ]] || [[ ! -d "src/behavior" ]]; then
    print_error "Please run this script from a valid project checkout."
    exit 1
fi
print_info "Project root: ${PROJECT_ROOT}"

echo "[2/5] Checking Python environment..."
if ! command -v python3 >/dev/null 2>&1; then
    print_error "python3 not found."
    exit 1
fi
python3 --version

echo "[3/5] Checking Behavior API file..."
if [[ ! -f "src/behavior/api.py" ]]; then
    print_error "src/behavior/api.py not found."
    exit 1
fi
print_info "Found src/behavior/api.py"

python3 - <<'PY'
from __future__ import annotations

import json
import os
import sys
from typing import Any, Dict, Iterable


REQUIRED_TOP_LEVEL_FIELDS = {
    "success",
    "target_user",
    "baseline",
    "profile",
    "anomalies",
    "summary",
    "error",
}

REQUIRED_SUMMARY_FIELDS = {
    "total_logs",
    "anomaly_count",
    "max_risk_score",
    "overall_risk_level",
}

REQUIRED_ANOMALY_FIELDS = {
    "timestamp",
    "username",
    "anomaly_type",
    "risk_score",
    "risk_level",
    "reason",
}


def fail(message: str) -> None:
    print(f"[ERROR] {message}")
    sys.exit(1)


def ensure_dict(value: Any, field_name: str) -> Dict[str, Any]:
    if not isinstance(value, dict):
        fail(f"{field_name} must be a dict, got {type(value).__name__}.")
    return value


def ensure_list(value: Any, field_name: str) -> list:
    if not isinstance(value, list):
        fail(f"{field_name} must be a list, got {type(value).__name__}.")
    return value


def ensure_fields(mapping: Dict[str, Any], required_fields: set[str], name: str) -> None:
    missing = sorted(required_fields.difference(mapping.keys()))
    if missing:
        fail(f"{name} missing required fields: {', '.join(missing)}")


def print_anomalies(anomalies: Iterable[Dict[str, Any]]) -> None:
    anomaly_list = list(anomalies)
    print(f"anomalies count: {len(anomaly_list)}")
    if not anomaly_list:
        print("anomalies detail: none")
        return

    for index, anomaly in enumerate(anomaly_list, start=1):
        print(
            f"  {index}. timestamp={anomaly.get('timestamp')}, "
            f"anomaly_type={anomaly.get('anomaly_type')}, "
            f"risk_level={anomaly.get('risk_level')}, "
            f"risk_score={anomaly.get('risk_score')}, "
            f"reason={anomaly.get('reason')}"
        )


project_root = os.getcwd()
if project_root not in sys.path:
    sys.path.insert(0, project_root)

try:
    from src.behavior.api import analyze_behavior_for_frontend
except ImportError:
    print("[ERROR] Failed to import src.behavior.api. Please run this script from project root.")
    sys.exit(1)

print("[4/5] Running frontend-style API call...")
payload = {
    "target_user": "zhangsan",
    "history_logs": [
        {
            "timestamp": "2026-04-01 09:00:00",
            "username": "zhangsan",
            "source_ip": "10.0.0.1",
            "location": "北京",
            "action": "LOGIN_SUCCESS",
            "endpoint": "/login",
            "status": "SUCCESS",
        },
        {
            "timestamp": "2026-04-01 10:00:00",
            "username": "zhangsan",
            "source_ip": "10.0.0.1",
            "location": "北京",
            "action": "LOGIN_SUCCESS",
            "endpoint": "/home",
            "status": "SUCCESS",
        },
    ],
    "detection_logs": [
        {
            "timestamp": "2026-04-02 23:30:00",
            "username": "zhangsan",
            "source_ip": "192.168.1.50",
            "location": "上海",
            "action": "LOGIN_FAILED",
            "endpoint": "/login",
            "status": "FAILED",
        }
    ],
}

result = analyze_behavior_for_frontend(payload)
result = ensure_dict(result, "success result")
ensure_fields(result, REQUIRED_TOP_LEVEL_FIELDS, "success result")

if result.get("success") is not True:
    fail("Expected success result['success'] to be True.")

summary = ensure_dict(result.get("summary"), "success result.summary")
ensure_fields(summary, REQUIRED_SUMMARY_FIELDS, "success result.summary")

anomalies = ensure_list(result.get("anomalies"), "success result.anomalies")
for index, anomaly in enumerate(anomalies, start=1):
    anomaly_mapping = ensure_dict(anomaly, f"success result.anomalies[{index}]")
    ensure_fields(
        anomaly_mapping,
        REQUIRED_ANOMALY_FIELDS,
        f"success result.anomalies[{index}]",
    )

print("Success result JSON:")
print(json.dumps(result, ensure_ascii=False, indent=2))
print(f"target_user: {result.get('target_user')}")
print("summary:")
print(json.dumps(summary, ensure_ascii=False, indent=2))
print_anomalies(anomalies)

print("[5/5] Running invalid input scenario...")
invalid_payload = {
    "history_logs": [],
    "detection_logs": [],
}

invalid_result = analyze_behavior_for_frontend(invalid_payload)
invalid_result = ensure_dict(invalid_result, "invalid result")
ensure_fields(invalid_result, REQUIRED_TOP_LEVEL_FIELDS, "invalid result")

if invalid_result.get("success") is not False:
    fail("Expected invalid result['success'] to be False.")

error = ensure_dict(invalid_result.get("error"), "invalid result.error")
if not error:
    fail("invalid result.error must not be empty.")
if error.get("code") != "INVALID_INPUT":
    fail(f"Expected invalid result.error.code to be INVALID_INPUT, got {error.get('code')}.")

print("Invalid result JSON:")
print(json.dumps(invalid_result, ensure_ascii=False, indent=2))
print(f"error.code: {error.get('code')}")
print(f"error.message: {error.get('message')}")

print("[OK] Behavior frontend API integration test passed.")
sys.exit(0)
PY

echo ""
print_ok "Behavior frontend API test completed."
