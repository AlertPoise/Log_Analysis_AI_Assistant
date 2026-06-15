"""UEBA risk attribution from anomaly reason codes."""

from __future__ import annotations

import json
from collections.abc import Iterable
from typing import Any


_RISK_NAMES = {
    "NORMAL": "未发现明确风险",
    "BASELINE_QUALITY_RISK": "基线可信度不足风险",
    "CREDENTIAL_ATTACK_RISK": "疑似凭据攻击风险",
    "ACCOUNT_TAKEOVER_RISK": "疑似账号接管风险",
    "REMOTE_ACCESS_RISK": "异常远程接入风险",
    "ACCESS_TARGET_RISK": "异常访问目标风险",
    "TOOL_PROTOCOL_RISK": "异常工具或协议使用风险",
    "GENERAL_ANOMALY_RISK": "一般异常行为风险",
}

_RISK_SUMMARIES = {
    "NORMAL": "当前记录未发现明确 UEBA 风险标签。",
    "BASELINE_QUALITY_RISK": "用户历史样本不足或缺少可用基线，当前风险判断可信度有限。",
    "CREDENTIAL_ATTACK_RISK": "当前记录存在登录失败或认证异常迹象，可能涉及凭据尝试或认证风险。",
    "ACCOUNT_TAKEOVER_RISK": "用户登录来源、地域或访问时间明显偏离历史行为，可能存在账号被异常使用的风险。",
    "REMOTE_ACCESS_RISK": "用户使用了历史上不常见的 VPN 网关或远程接入入口。",
    "ACCESS_TARGET_RISK": "用户访问了历史上不常见的目标地址，可能存在访问范围异常。",
    "TOOL_PROTOCOL_RISK": "用户使用了历史上不常见的客户端、协议或认证方式。",
    "GENERAL_ANOMALY_RISK": "检测到异常行为标签，但未命中明确风险场景。",
}


def classify_ueba_risk(reasons: Any) -> dict[str, Any]:
    """Classify UEBA risk from normalized anomaly reason codes."""

    reason_codes = _normalize_reason_codes(reasons)
    reason_set = set(reason_codes)

    if not reason_codes:
        risk_category = "NORMAL"
        evidence_codes: list[str] = []
    elif reason_set & {"NO_BASELINE", "UNRELIABLE_BASELINE"}:
        risk_category = "BASELINE_QUALITY_RISK"
        evidence_codes = [
            code for code in reason_codes if code in {"NO_BASELINE", "UNRELIABLE_BASELINE"}
        ]
    elif "LOGIN_FAILED" in reason_set:
        risk_category = "CREDENTIAL_ATTACK_RISK"
        evidence_codes = ["LOGIN_FAILED"]
    elif {"NEW_SOURCE_COUNTRY", "NEW_SOURCE_IP"} <= reason_set:
        risk_category = "ACCOUNT_TAKEOVER_RISK"
        evidence_codes = [
            code for code in reason_codes if code in {"NEW_SOURCE_COUNTRY", "NEW_SOURCE_IP"}
        ]
    elif {"NEW_SOURCE_CITY", "NEW_SOURCE_IP"} <= reason_set:
        risk_category = "ACCOUNT_TAKEOVER_RISK"
        evidence_codes = [
            code for code in reason_codes if code in {"NEW_SOURCE_CITY", "NEW_SOURCE_IP"}
        ]
    elif {"NEW_SOURCE_IP", "OFF_HOURS"} <= reason_set:
        risk_category = "ACCOUNT_TAKEOVER_RISK"
        evidence_codes = [
            code for code in reason_codes if code in {"NEW_SOURCE_IP", "OFF_HOURS"}
        ]
    elif {"NEW_SOURCE_COUNTRY", "NEW_VPN_GATEWAY"} <= reason_set:
        risk_category = "ACCOUNT_TAKEOVER_RISK"
        evidence_codes = [
            code for code in reason_codes if code in {"NEW_SOURCE_COUNTRY", "NEW_VPN_GATEWAY"}
        ]
    elif "NEW_VPN_GATEWAY" in reason_set:
        risk_category = "REMOTE_ACCESS_RISK"
        evidence_codes = ["NEW_VPN_GATEWAY"]
    elif "NEW_DESTINATION_IP" in reason_set:
        risk_category = "ACCESS_TARGET_RISK"
        evidence_codes = ["NEW_DESTINATION_IP"]
    elif reason_set & {"NEW_CLIENT_SOFTWARE", "NEW_PROTOCOL", "NEW_AUTH_METHOD"}:
        risk_category = "TOOL_PROTOCOL_RISK"
        evidence_codes = [
            code
            for code in reason_codes
            if code in {"NEW_CLIENT_SOFTWARE", "NEW_PROTOCOL", "NEW_AUTH_METHOD"}
        ]
    else:
        risk_category = "GENERAL_ANOMALY_RISK"
        evidence_codes = reason_codes

    return {
        "risk_category": risk_category,
        "risk_category_name": _RISK_NAMES[risk_category],
        "risk_summary": _RISK_SUMMARIES[risk_category],
        "risk_evidence_codes": evidence_codes,
    }


def _normalize_reason_codes(reasons: Any) -> list[str]:
    if reasons is None:
        reason_values: list[Any] = []
    elif isinstance(reasons, str):
        text = reasons.strip()
        if text.startswith("["):
            try:
                parsed = json.loads(text)
            except json.JSONDecodeError:
                reason_values = [text]
            else:
                reason_values = parsed if isinstance(parsed, list) else [text]
        elif "," in text:
            reason_values = text.split(",")
        else:
            reason_values = [text] if text else []
    elif isinstance(reasons, (list, tuple, set)):
        reason_values = list(reasons)
    elif isinstance(reasons, Iterable):
        reason_values = list(reasons)
    else:
        reason_values = [reasons]

    reason_codes = []
    for value in reason_values:
        code_value = value.get("code") if isinstance(value, dict) else value
        code = str(code_value).strip().upper() if code_value is not None else ""
        if code and code not in reason_codes:
            reason_codes.append(code)
    return reason_codes
