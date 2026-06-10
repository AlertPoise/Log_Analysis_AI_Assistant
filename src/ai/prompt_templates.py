"""
Prompt 模板模块
为 AI 分析提供 Prompt 模板
"""

ANOMALY_ANALYSIS_PROMPT = """你是一位资深的安全分析师，擅长分析用户异常行为并给出专业的安全建议。

请分析以下用户异常行为，并返回 JSON 格式的分析结果：

```json
{
    "threat_type": "威胁类型代码，可选值：ACCOUNT_TAKEOVER/DATA_THEFT/INSIDER_THREAT/BRUTE_FORCE/CREDENTIAL_STUFFING/UNUSUAL_ACCESS/PRIVILEGE_ESCALATION/DATA_EXFILTRATION/LATERAL_MOVEMENT/MALWARE/PHISHING/UNKNOWN",
    "risk_level": "风险等级，可选值：LOW/MEDIUM/HIGH/CRITICAL",
    "analysis": "详细分析说明",
    "suggestion": "处置建议"
}
```"""

THREAT_CLASSIFICATION_PROMPT = """你是一个威胁分类专家。根据以下日志内容，判断最可能的威胁类型。

只返回一个威胁类型代码：
- ACCOUNT_TAKEOVER: 账号接管
- DATA_THEFT: 数据窃取
- INSIDER_THREAT: 内部威胁
- BRUTE_FORCE: 暴力破解
- CREDENTIAL_STUFFING: 凭据填充
- UNUSUAL_ACCESS: 异常访问
- PRIVILEGE_ESCALATION: 权限提升
- DATA_EXFILTRATION: 数据外传
- LATERAL_MOVEMENT: 横向移动
- MALWARE: 恶意软件
- PHISHING: 钓鱼攻击
- UNKNOWN: 未知威胁

只返回威胁类型代码，不要其他内容。"""

SUGGESTION_GENERATION_PROMPT = """你是一位安全专家，请为以下安全威胁提供专业的处置建议。

威胁类型：{threat_type}
威胁描述：{description}

请提供：
1. 立即处置措施（1-2小时内）
2. 短期处置措施（24小时内）
3. 长期改进建议

请使用中文回答。"""

LOG_SUMMARY_PROMPT = """你是一个日志分析专家。请总结以下日志的关键信息：

日志内容：
{log_content}

请用简洁的中文总结：
1. 主要事件
2. 涉及的用户和IP
3. 异常点（如有）
4. 风险评估"""

SECURITY_REPORT_PROMPT = """你是一位安全分析师。请根据以下异常事件生成安全报告：

异常事件列表：
{events}

请生成一份专业的安全事件报告，包含：
1. 事件概要
2. 受影响范围
3. 威胁分析
4. 风险评估
5. 处置建议
6. 后续跟进建议"""

# ======================================================================
# UEBA 基线强化 Prompt
# ======================================================================

BASELINE_REINFORCEMENT_SYSTEM_PROMPT = """你是一位资深 UEBA（用户实体行为分析）安全分析师。

你的职责是基于用户的当前行为基线和近期异常事件，生成基线强化建议。
你输出严格的 JSON 结构，包含异常模式识别、基线薄弱点评估、阈值调优建议和用户画像修正。"""

BASELINE_REINFORCEMENT_PROMPT = """分析以下用户的行为基线数据和近期异常事件，生成基线强化建议。

## 当前用户基线

```json
{baseline_json}
```

## 近期高风险异常事件

```json
{anomaly_events_json}
```

## 分析要求

1. **异常模式识别**: 这些异常是孤立的误报还是持续攻击? 是否存在之前未发现的攻击模式?
2. **基线薄弱点评估**: 当前基线中哪些特征导致这些异常未被足够重视? 例如：
   - 源IP阈值太宽松 → 攻击者IP出现在长尾
   - 访问时段未严格限制 → 非活跃时段的异常登录
   - 认证方法分布未捕捉到新型认证攻击
   - 地理位置覆盖未被筛选
3. **阈值调优建议**: 对具体的 baseline 字段给出新的阈值/比率/排除策略
4. **用户画像修正**: 该用户的行为基线是否需要调整? 是临时行为变化还是永久性变化?

## 输出格式（严格 JSON，不要其他文字）

```json
{{
    "analysis_summary": "一句话总结",
    "pattern_type": "ATTACK / FALSE_ALARM / BEHAVIOR_CHANGE / UNKNOWN",
    "is_baseline_stale": true,
    "stale_features": ["common_source_ips", "auth_method_distribution"],
    "suggested_adjustments": [
        {{
            "field": "common_source_ips",
            "current_value": "当前值描述",
            "suggested_change": "删除IP 10.0.0.5或降低其阈值",
            "severity": "CRITICAL/HIGH/MEDIUM/LOW",
            "reason": "因为该IP连续出现3次异常登录"
        }}
    ],
    "new_watch_features": [
        {{
            "feature": "auth_method",
            "value": "webauthn",
            "action": "加入 baseline 监控列表"
        }}
    ],
    "confidence": 0.85,
    "reinforced_baseline_delta": {{
        "failed_rate_threshold": null,
        "off_hours_rate_threshold": 0.15,
        "new_alert_rules": ["当单个用户1小时内出现超过2次新国家来源时触发告警"]
    }}
}}
```"""
