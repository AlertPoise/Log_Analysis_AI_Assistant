"""UEBA AI 强化用户基线模块。

本模块负责：
1. 将用户当前行为基线 (Baseline) 与近期高风险异常事件打包为 AI 上下文
2. 调用 AI API 生成结构化基线强化建议（阈值调优、特征关注、模式识别）
3. 将强化建议持久化到 ClickHouse baseline_ai_refinements 表

编排流程：
    reinforce_user() → 单用户强化
    reinforce_all_users() → 批量强化有高风险事件的用户

本模块独立于 UebaManagementService，Phase 2 再嵌入 run_validation 闭环。
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any

from .baseline_store import BaselineStore
from ..ai.client import AIClient, create_ai_client
from ..ai.prompt_templates import (
    BASELINE_REINFORCEMENT_PROMPT,
    BASELINE_REINFORCEMENT_SYSTEM_PROMPT,
)
from ..utils.logger import get_logger

logger = get_logger(__name__)

# ---------------------------------------------------------------------------
# 内部异常上下文构建器
# ---------------------------------------------------------------------------


class AbnormalContextBuilder:
    """将用户 baseline 快照与异常事件打包为 AI 友好的结构化上下文。"""

    @staticmethod
    def build(baseline: dict, anomaly_events: list[dict]) -> dict:
        """构建 AI 输入上下文。

        Args:
            baseline: BaselineStore.get_user_baseline() 返回的行字典。
            anomaly_events: 高风险异常事件列表（已含来源日志丰富字段）。

        Returns:
            {
                "current_baseline": { ... 精简 baseline 特征 },
                "recent_anomalies": [ { ... 事件详情 }, ... ]
            }
        """
        # -- baseline 特征（JSON 字段反序列化 + 数值精简） --
        current_baseline = {
            "username": str(baseline.get("username", "")),
            "sample_count": int(baseline.get("sample_count", 0)),
            "is_reliable": bool(baseline.get("is_reliable")),
            "failed_rate": float(baseline.get("failed_rate", 0)),
            "off_hours_rate": float(baseline.get("off_hours_rate", 0)),
            "unusual_ip_rate": float(baseline.get("unusual_ip_rate", 0)),
            "avg_daily_events": float(baseline.get("avg_daily_events", 0)),
            "active_day_avg_events": float(baseline.get("active_day_avg_events", 0)),
            "common_active_hours": AbnormalContextBuilder._safe_parse_list(
                baseline.get("common_active_hours")
            ),
            "common_source_ips": AbnormalContextBuilder._safe_parse_list(
                baseline.get("common_source_ips")
            ),
            "common_source_cities": AbnormalContextBuilder._safe_parse_list(
                baseline.get("common_source_cities")
            ),
            "common_source_countries": AbnormalContextBuilder._safe_parse_list(
                baseline.get("common_source_countries")
            ),
            "common_vpn_gateways": AbnormalContextBuilder._safe_parse_list(
                baseline.get("common_vpn_gateways")
            ),
            "action_distribution": AbnormalContextBuilder._safe_parse_dict(
                baseline.get("action_distribution")
            ),
            "auth_method_distribution": AbnormalContextBuilder._safe_parse_dict(
                baseline.get("auth_method_distribution")
            ),
            "baseline_start_time": str(baseline.get("baseline_start_time", "")),
            "baseline_end_time": str(baseline.get("baseline_end_time", "")),
            "model_version": str(baseline.get("model_version", "")),
        }

        # -- 异常事件列表（精简关键字段） --
        recent_anomalies = []
        for ev in anomaly_events:
            reasons_raw = ev.get("ueba_anomaly_reasons")
            if isinstance(reasons_raw, str):
                try:
                    reasons = json.loads(reasons_raw)
                except (json.JSONDecodeError, TypeError):
                    reasons = []
            elif isinstance(reasons_raw, list):
                reasons = reasons_raw
            else:
                reasons = []

            recent_anomalies.append({
                "timestamp": str(ev.get("timestamp", "")),
                "ueba_score": int(ev.get("ueba_score", 0)),
                "ueba_risk_level": str(ev.get("ueba_risk_level", "")),
                "validation_status": str(ev.get("validation_status", "")),
                "source_ip": str(ev.get("source_ip", "")),
                "source_country": str(ev.get("source_country", "")),
                "source_city": str(ev.get("source_city", "")),
                "destination_ip": str(ev.get("destination_ip", "")),
                "vpn_gateway": str(ev.get("vpn_gateway", "")),
                "auth_method": str(ev.get("auth_method", "")),
                "client_software": str(ev.get("client_software", "")),
                "protocol": str(ev.get("protocol", "")),
                "anomaly_reason_codes": [
                    r.get("code", "") for r in reasons if isinstance(r, dict)
                ],
                "anomaly_reason_scores": sum(
                    r.get("score_delta", 0) for r in reasons if isinstance(r, dict)
                ),
            })

        return {
            "current_baseline": current_baseline,
            "recent_anomalies": recent_anomalies,
        }

    @staticmethod
    def _safe_parse_list(value: Any) -> list:
        """安全解析 JSON 数组字段。"""
        if isinstance(value, list):
            return value
        if isinstance(value, str):
            try:
                parsed = json.loads(value)
                return parsed if isinstance(parsed, list) else []
            except (json.JSONDecodeError, TypeError):
                return []
        return []

    @staticmethod
    def _safe_parse_dict(value: Any) -> dict:
        """安全解析 JSON 对象字段。"""
        if isinstance(value, dict):
            return value
        if isinstance(value, str):
            try:
                parsed = json.loads(value)
                return parsed if isinstance(parsed, dict) else {}
            except (json.JSONDecodeError, TypeError):
                return {}
        return {}


# ---------------------------------------------------------------------------
# AI 响应解析
# ---------------------------------------------------------------------------


def _parse_ai_json_response(response: str) -> dict[str, Any] | None:
    """从 AI 响应中提取并解析 JSON 对象。

    兼容 ```json ... ``` 代码块、裸 JSON 对象等多种返回格式。
    """
    if not response:
        return None

    text = response.strip()

    # 尝试提取 ```json ... ``` 代码块
    for marker in ("```json", "```"):
        if marker in text:
            start = text.find(marker) + len(marker)
            end = text.find("```", start)
            if end != -1:
                text = text[start:end].strip()
            else:
                text = text[start:].strip()
            break

    # 尝试直接解析
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    # 尝试查找 {...} 顶层对象
    brace_start = text.find("{")
    brace_end = text.rfind("}")
    if brace_start != -1 and brace_end > brace_start:
        try:
            return json.loads(text[brace_start : brace_end + 1])
        except json.JSONDecodeError:
            pass

    logger.warning("AI 响应无法解析为 JSON: %.200s", response)
    return None


# ---------------------------------------------------------------------------
# 编排服务
# ---------------------------------------------------------------------------

_HIGH_RISK_LEVELS = ("HIGH", "CRITICAL")
_MAX_REFINEMENT_EVENTS = 50
_DEFAULT_REFINEMENT_BATCH_SIZE = 20


class BaselineReinforcementService:
    """AI 强化用户基线的主编排服务。

    职责：
    - 读取用户当前 Baseline（通过 BaselineStore）
    - 查询近期高风险异常事件（直接查 ClickHouse ueba_validation_results + logs_structured）
    - 调用 AI API 生成强化建议
    - 将建议写入 baseline_ai_refinements 表

    使用方式：
        service = BaselineReinforcementService(
            clickhouse_client=ch_client,
            ai_client=ai_client,
            baseline_store=baseline_store,
        )
        result = service.reinforce_user("zhangsan", "ueba_baseline_v1", "2026-06-01 00:00:00", "2026-06-07 23:59:59")
    """

    def __init__(
        self,
        clickhouse_client: Any,
        ai_client: AIClient,
        baseline_store: BaselineStore,
        database: str = "log_analysis",
        refinements_table: str = "baseline_ai_refinements",
    ) -> None:
        """初始化 BaselineReinforcementService。

        Args:
            clickhouse_client: ClickHouse 连接客户端。
            ai_client: 已配置的 AI 客户端实例。
            baseline_store: Baseline 存储查询组件。
            database: ClickHouse 数据库名。
            refinements_table: 强化建议存储表名。
        """
        self._client = clickhouse_client
        self._ai = ai_client
        self._baseline_store = baseline_store
        self._database = database
        self._table = refinements_table
        self._context_builder = AbnormalContextBuilder()

    # ------------------------------------------------------------------
    # 公开入口
    # ------------------------------------------------------------------

    def reinforce_user(
        self,
        username: str,
        model_version: str,
        start_time: str,
        end_time: str,
        max_events: int = _MAX_REFINEMENT_EVENTS,
    ) -> dict[str, Any]:
        """强化单个用户的基线。

        Args:
            username: 目标用户名。
            model_version: Baseline 模型版本号。
            start_time: 异常事件查询起始时间（含）。
            end_time: 异常事件查询结束时间（不含）。
            max_events: 最多纳入的异常事件数。

        Returns:
            {
                "success": True/False,
                "username": str,
                "model_version": str,
                "reason": "no_baseline" / "no_high_risk_events" / None,
                "suggestions": dict | None,
            }
        """
        # 1. 读取 baseline
        baseline = self._baseline_store.get_user_baseline(
            username, model_version=model_version
        )
        if baseline is None:
            logger.info("reinforce_user[%s]: baseline 不存在（%s）", username, model_version)
            return {
                "success": False,
                "username": username,
                "model_version": model_version,
                "reason": "no_baseline",
                "suggestions": None,
            }

        # 2. 查询高风险异常事件
        anomalies = self._query_user_high_risk_events(
            username=username,
            model_version=model_version,
            start_time=start_time,
            end_time=end_time,
            limit=max_events,
        )

        if not anomalies:
            logger.info("reinforce_user[%s]: 窗口内无高风险事件", username)
            return {
                "success": True,
                "username": username,
                "model_version": model_version,
                "reason": "no_high_risk_events",
                "suggestions": None,
            }

        # 3. 打包上下文
        context = self._context_builder.build(baseline, anomalies)

        # 4. 调用 AI API
        prompt = BASELINE_REINFORCEMENT_PROMPT.format(
            baseline_json=json.dumps(
                context["current_baseline"], ensure_ascii=False, indent=2
            ),
            anomaly_events_json=json.dumps(
                context["recent_anomalies"], ensure_ascii=False, indent=2
            ),
        )
        try:
            ai_response = self._ai.analyze(
                system_prompt=BASELINE_REINFORCEMENT_SYSTEM_PROMPT,
                user_message=prompt,
                temperature=0.2,
            )
        except Exception as exc:
            logger.exception("reinforce_user[%s]: AI API 调用失败", username)
            return {
                "success": False,
                "username": username,
                "model_version": model_version,
                "reason": f"ai_api_error: {type(exc).__name__}",
                "suggestions": None,
            }

        suggestions = _parse_ai_json_response(ai_response)
        if suggestions is None:
            return {
                "success": False,
                "username": username,
                "model_version": model_version,
                "reason": "ai_response_parse_failed",
                "raw_response": ai_response,
                "suggestions": None,
            }

        # 5. 持久化
        self._save_refinement(username, model_version, suggestions)

        logger.info(
            "reinforce_user[%s]: 强化完成 — pattern=%s, confidence=%.2f",
            username,
            suggestions.get("pattern_type", "UNKNOWN"),
            suggestions.get("confidence", 0),
        )

        return {
            "success": True,
            "username": username,
            "model_version": model_version,
            "reason": None,
            "anomaly_count": len(anomalies),
            "suggestions": suggestions,
        }

    def reinforce_all_users(
        self,
        model_version: str,
        start_time: str,
        end_time: str,
        max_users: int = 50,
        max_events_per_user: int = _MAX_REFINEMENT_EVENTS,
    ) -> list[dict[str, Any]]:
        """批量强化所有有高风险事件（HIGH/CRITICAL）的用户基线。

        Args:
            model_version: Baseline 模型版本号。
            start_time: 事件查询起始时间（含）。
            end_time: 事件查询结束时间（不含）。
            max_users: 最多强化用户数。
            max_events_per_user: 每个用户最多纳入的异常事件数。

        Returns:
            每个强化结果的列表。
        """
        users = self._fetch_high_risk_users(
            model_version=model_version,
            start_time=start_time,
            end_time=end_time,
            limit=max_users,
        )

        if not users:
            logger.info("reinforce_all_users: 窗口内无高风险用户")
            return []

        logger.info("reinforce_all_users: 发现 %d 个高风险用户，开始强化", len(users))

        results: list[dict[str, Any]] = []
        for username in users:
            result = self.reinforce_user(
                username=username,
                model_version=model_version,
                start_time=start_time,
                end_time=end_time,
                max_events=max_events_per_user,
            )
            results.append(result)

        success_count = sum(1 for r in results if r.get("success"))
        logger.info(
            "reinforce_all_users: %d/%d 完成", success_count, len(results)
        )
        return results

    # ------------------------------------------------------------------
    # 内部查询
    # ------------------------------------------------------------------

    def _query_user_high_risk_events(
        self,
        username: str,
        model_version: str,
        start_time: str,
        end_time: str,
        limit: int = _MAX_REFINEMENT_EVENTS,
    ) -> list[dict[str, Any]]:
        """查询单个用户的高风险异常事件（JOIN logs_structured 获取丰富字段）。"""
        sql = f"""
        SELECT
            v.validation_id,
            v.validation_run_id,
            v.source_log_id,
            v.timestamp,
            v.username,
            v.ueba_score,
            v.ueba_risk_level,
            v.ueba_anomaly_reasons,
            v.validation_status,
            v.validated_at,
            l.source_ip,
            l.destination_ip,
            l.src_country,
            l.src_city,
            l.vpn_gateway,
            l.action,
            l.event_type,
            l.result,
            l.auth_method,
            l.client_software,
            l.protocol
        FROM {self._database}.ueba_validation_results AS v
        LEFT JOIN {self._database}.logs_structured AS l
            ON v.source_log_id = l.id
        WHERE v.baseline_model_version = %(model_version)s
            AND v.username = %(username)s
            AND v.ueba_risk_level IN %(risk_levels)s
            AND v.timestamp >= %(start_time)s
            AND v.timestamp < %(end_time)s
            AND v.validation_status != 'NO_BASELINE'
        ORDER BY v.timestamp DESC, v.source_log_id DESC
        LIMIT %(limit)s
        """
        parameters = {
            "model_version": model_version,
            "username": username,
            "risk_levels": _HIGH_RISK_LEVELS,
            "start_time": start_time,
            "end_time": end_time,
            "limit": min(limit, 200),
        }
        return self._query(sql, parameters)

    def _fetch_high_risk_users(
        self,
        model_version: str,
        start_time: str,
        end_time: str,
        limit: int = 50,
    ) -> list[str]:
        """查询在时间窗口内有 HIGH/CRITICAL 事件的所有用户名。"""
        sql = f"""
        SELECT DISTINCT username
        FROM {self._database}.ueba_validation_results
        WHERE baseline_model_version = %(model_version)s
            AND ueba_risk_level IN %(risk_levels)s
            AND timestamp >= %(start_time)s
            AND timestamp < %(end_time)s
            AND validation_status != 'NO_BASELINE'
            AND username != ''
        ORDER BY username ASC
        LIMIT %(limit)s
        """
        parameters = {
            "model_version": model_version,
            "risk_levels": _HIGH_RISK_LEVELS,
            "start_time": start_time,
            "end_time": end_time,
            "limit": min(limit, 200),
        }
        rows = self._query(sql, parameters)
        return [str(row["username"]) for row in rows if row.get("username")]

    # ------------------------------------------------------------------
    # 持久化
    # ------------------------------------------------------------------

    def ensure_refinements_table(self) -> None:
        """确保 baseline_ai_refinements 表存在。"""
        sql = f"""
        CREATE TABLE IF NOT EXISTS {self._database}.{self._table}
        (
            username String,
            model_version String,
            analysis_summary String,
            pattern_type String,
            is_baseline_stale UInt8,
            stale_features String,
            suggested_adjustments String,
            new_watch_features String,
            reinforced_baseline_delta String,
            confidence Float64,
            ai_platform String,
            validated_at DateTime,
            anomaly_event_count UInt32,
            raw_response String,
            created_at DateTime DEFAULT now()
        )
        ENGINE = ReplacingMergeTree(created_at)
        ORDER BY (username, model_version, validated_at)
        """
        self._command(sql)

    def _save_refinement(
        self,
        username: str,
        model_version: str,
        suggestions: dict[str, Any],
    ) -> None:
        """将 AI 强化建议写入 baseline_ai_refinements 表。"""
        self.ensure_refinements_table()

        now_str = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
        row = {
            "username": username,
            "model_version": model_version,
            "analysis_summary": str(suggestions.get("analysis_summary", "")),
            "pattern_type": str(suggestions.get("pattern_type", "UNKNOWN")),
            "is_baseline_stale": 1 if suggestions.get("is_baseline_stale") else 0,
            "stale_features": json.dumps(
                suggestions.get("stale_features", []), ensure_ascii=False
            ),
            "suggested_adjustments": json.dumps(
                suggestions.get("suggested_adjustments", []), ensure_ascii=False
            ),
            "new_watch_features": json.dumps(
                suggestions.get("new_watch_features", []), ensure_ascii=False
            ),
            "reinforced_baseline_delta": json.dumps(
                suggestions.get("reinforced_baseline_delta", {}), ensure_ascii=False
            ),
            "confidence": float(suggestions.get("confidence", 0)),
            "ai_platform": self._ai.platform if hasattr(self._ai, "platform") else "unknown",
            "validated_at": now_str,
            "anomaly_event_count": len(
                suggestions.get("anomaly_event_count") or 0
            ),
            "raw_response": json.dumps(suggestions, ensure_ascii=False),
        }

        columns = [
            "username", "model_version", "analysis_summary", "pattern_type",
            "is_baseline_stale", "stale_features", "suggested_adjustments",
            "new_watch_features", "reinforced_baseline_delta", "confidence",
            "ai_platform", "validated_at", "anomaly_event_count", "raw_response",
        ]
        insert_row = [row[col] for col in columns]
        self._client.insert(
            self._table,
            [insert_row],
            column_names=columns,
            database=self._database,
        )

    # ------------------------------------------------------------------
    # ClickHouse 底层封装
    # ------------------------------------------------------------------

    def _query(
        self, sql: str, parameters: dict[str, Any]
    ) -> list[dict[str, Any]]:
        """执行参数化查询并返回字典列表。"""
        if hasattr(self._client, "query"):
            result = self._client.query(sql, parameters=parameters)
            return self._normalize_result(result)
        if hasattr(self._client, "execute"):
            return self._client.execute(sql, parameters)
        raise TypeError("client must provide query(...) or execute(...)")

    def _command(self, sql: str) -> None:
        """执行不返回结果的 SQL。"""
        if hasattr(self._client, "command"):
            self._client.command(sql)
            return
        if hasattr(self._client, "execute"):
            self._client.execute(sql)
            return
        raise TypeError("client must provide command(...) or execute(...)")

    @staticmethod
    def _normalize_result(result: Any) -> list[dict[str, Any]]:
        """兼容 clickhouse-connect 的 query 返回格式。"""
        if hasattr(result, "named_results"):
            nr = result.named_results
            rows = nr() if callable(nr) else nr
            return [dict(row) for row in rows]
        if hasattr(result, "result_rows") and hasattr(result, "column_names"):
            return [dict(zip(result.column_names, row)) for row in result.result_rows]
        if isinstance(result, list):
            return [dict(row) for row in result if isinstance(row, dict)]
        return []


__all__ = [
    "AbnormalContextBuilder",
    "BaselineReinforcementService",
    "reinforce_baseline",  # 便捷函数
]


# ---------------------------------------------------------------------------
# 便捷函数
# ---------------------------------------------------------------------------


def reinforce_baseline(
    clickhouse_client: Any,
    ai_client: AIClient,
    baseline_store: BaselineStore,
    username: str,
    model_version: str,
    start_time: str,
    end_time: str,
) -> dict[str, Any]:
    """便捷函数：强化单个用户基线。

    等价于：
        service = BaselineReinforcementService(ch, ai, bs)
        return service.reinforce_user(username, model_version, start_time, end_time)
    """
    service = BaselineReinforcementService(
        clickhouse_client=clickhouse_client,
        ai_client=ai_client,
        baseline_store=baseline_store,
    )
    return service.reinforce_user(
        username=username,
        model_version=model_version,
        start_time=start_time,
        end_time=end_time,
    )
