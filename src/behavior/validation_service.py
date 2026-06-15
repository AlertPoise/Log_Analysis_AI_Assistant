"""UEBA 准线验证编排服务。

本模块串联目标日志读取、baseline 读取、纯 Python 评分和可选结果保存，
返回受控摘要。它不更新源日志，也不创建数据库连接。
"""

from datetime import datetime, timezone
import hashlib
from typing import Any

from .baseline_store import BaselineStore
from .schemas import CountRatioItem, UserBaseline
from .score_calculator import UebaScoreCalculator
from .validation_repository import UebaValidationRepository
from .validation_schemas import UebaValidationResult


class UebaValidationService:
    """UEBA 准线验证统一编排入口。"""

    DEFAULT_SAMPLE_RESULT_LIMIT = 10
    DEFAULT_SAMPLE_SIZE = 5

    def __init__(
        self,
        validation_repository: UebaValidationRepository,
        baseline_store: BaselineStore,
        score_calculator: UebaScoreCalculator | None = None,
    ) -> None:
        """初始化 validation service。

        Args:
            validation_repository: 目标日志读取和结果保存 repository。
            baseline_store: 只读 baseline 查询组件。
            score_calculator: 纯 Python 评分器。
        """
        self.validation_repository = validation_repository
        self.baseline_store = baseline_store
        self.score_calculator = score_calculator or UebaScoreCalculator()

    def dry_run(
        self,
        start_time: str,
        end_time: str,
        log_type: str = "vpn",
        limit: int = 1000,
        baseline_version: str | None = None,
        sample_result_limit: int = DEFAULT_SAMPLE_RESULT_LIMIT,
        validated_at: str | None = None,
        validation_run_id: str | None = None,
        require_baseline: bool = False,
    ) -> dict[str, Any]:
        """执行一次不保存结果的 UEBA 准线验证。"""
        return self.run(
            start_time=start_time,
            end_time=end_time,
            log_type=log_type,
            model_version=baseline_version,
            limit=limit,
            dry_run=True,
            sample_size=sample_result_limit,
            validated_at=validated_at,
            validation_run_id=validation_run_id,
            require_baseline=require_baseline,
        )

    def run(
        self,
        start_time: str,
        end_time: str,
        log_type: str = "vpn",
        model_version: str | None = None,
        limit: int = 1000,
        dry_run: bool = True,
        sample_size: int = DEFAULT_SAMPLE_SIZE,
        validated_at: str | None = None,
        validation_run_id: str | None = None,
        require_baseline: bool = False,
    ) -> dict[str, Any]:
        """执行一次 UEBA 准线验证，可选择保存评分结果。"""
        sample_limit = self._validate_sample_size(sample_size)
        effective_validation_run_id = self._validation_run_id(
            validation_run_id=validation_run_id,
            start_time=start_time,
            end_time=end_time,
            log_type=log_type,
            model_version=model_version,
            dry_run=dry_run,
        )
        try:
            target_logs = self.validation_repository.fetch_target_logs(
                start_time=start_time,
                end_time=end_time,
                log_type=log_type,
                limit=limit,
                exclude_already_validated=(not dry_run and model_version is not None),
                baseline_model_version=model_version,
                require_baseline=require_baseline,
            )
        except Exception as exc:
            return self._summary(
                success=False,
                start_time=start_time,
                end_time=end_time,
                log_type=log_type,
                limit=limit,
                model_version=model_version,
                validation_run_id=effective_validation_run_id,
                dry_run=dry_run,
                processed_count=0,
                results=[],
                written_count=0,
                no_baseline_count=0,
                unreliable_baseline_count=0,
                failed_count=0,
                sample_limit=sample_limit,
                message="validation target fetch failed",
                error=f"{type(exc).__name__}: {exc}",
            )

        results: list[UebaValidationResult] = []
        no_baseline_count = 0
        unreliable_baseline_count = 0
        failed_count = 0
        row_errors: list[str] = []
        # 缓存每个用户的强化建议，避免重复查询
        _refinements_cache: dict[str, list[dict[str, Any]]] = {}

        def _get_refinements(username: str) -> list[dict[str, Any]]:
            if username not in _refinements_cache:
                try:
                    _refinements_cache[username] = self.baseline_store.get_user_refinements(
                        username, model_version=model_version, limit=1
                    )
                except Exception:
                    _refinements_cache[username] = []
            return _refinements_cache[username]

        for target_log in target_logs:
            try:
                baseline = self._load_user_baseline(target_log.username, model_version)
            except Exception as exc:
                failed_count += 1
                row_errors.append(f"{target_log.username}:{type(exc).__name__}: {exc}")
                continue

            if baseline is None:
                no_baseline_count += 1
            elif not baseline.is_reliable:
                unreliable_baseline_count += 1

            refinements = _get_refinements(target_log.username)

            result = self.score_calculator.calculate(
                target_log,
                baseline,
                model_version=model_version or (baseline.model_version if baseline else None),
                validated_at=validated_at,
                validation_run_id=effective_validation_run_id,
                refinements=refinements,
            )
            results.append(result)

        if dry_run or not results:
            return self._summary(
                success=True,
                start_time=start_time,
                end_time=end_time,
                log_type=log_type,
                limit=limit,
                model_version=model_version,
                validation_run_id=effective_validation_run_id,
                dry_run=dry_run,
                processed_count=len(target_logs),
                results=results,
                written_count=0,
                no_baseline_count=no_baseline_count,
                unreliable_baseline_count=unreliable_baseline_count,
                failed_count=failed_count,
                sample_limit=sample_limit,
                message=self._message(len(target_logs), len(results), dry_run),
                error=self._row_error_text(row_errors),
            )

        try:
            written_count = self.validation_repository.save_validation_results(results)
        except Exception as exc:
            return self._summary(
                success=False,
                start_time=start_time,
                end_time=end_time,
                log_type=log_type,
                limit=limit,
                model_version=model_version,
                validation_run_id=effective_validation_run_id,
                dry_run=False,
                processed_count=len(target_logs),
                results=results,
                written_count=0,
                no_baseline_count=no_baseline_count,
                unreliable_baseline_count=unreliable_baseline_count,
                failed_count=failed_count,
                sample_limit=sample_limit,
                message="validation result save failed",
                error=f"{type(exc).__name__}: {exc}",
            )

        return self._summary(
            success=True,
            start_time=start_time,
            end_time=end_time,
            log_type=log_type,
            limit=limit,
            model_version=model_version,
            validation_run_id=effective_validation_run_id,
            dry_run=False,
            processed_count=len(target_logs),
            results=results,
            written_count=written_count,
            no_baseline_count=no_baseline_count,
            unreliable_baseline_count=unreliable_baseline_count,
            failed_count=failed_count,
            sample_limit=sample_limit,
            message=f"validation saved {written_count} of {len(results)} scored results",
            error=self._row_error_text(row_errors),
        )

    def _summary(
        self,
        *,
        success: bool,
        start_time: str,
        end_time: str,
        log_type: str,
        limit: int,
        model_version: str | None,
        validation_run_id: str,
        dry_run: bool,
        processed_count: int,
        results: list[UebaValidationResult],
        written_count: int,
        no_baseline_count: int,
        unreliable_baseline_count: int,
        failed_count: int,
        sample_limit: int,
        message: str,
        error: str | None,
    ) -> dict[str, Any]:
        """生成统一运行摘要。"""
        return {
            "success": success,
            "dry_run": dry_run,
            "start_time": start_time,
            "end_time": end_time,
            "log_type": log_type,
            "limit": limit,
            "model_version": model_version,
            "baseline_version": model_version,
            "validation_run_id": validation_run_id,
            "processed_count": processed_count,
            "selected_count": processed_count,
            "scored_count": len(results),
            "written_count": written_count,
            "skipped_count": no_baseline_count + failed_count,
            "no_baseline_count": no_baseline_count,
            "unreliable_baseline_count": unreliable_baseline_count,
            "failed_count": failed_count,
            "risk_level_counts": self._count_by(results, "ueba_risk_level"),
            "validation_status_counts": self._count_by(results, "validation_status"),
            "sample_results": [self._sample_result(result) for result in results[:sample_limit]],
            "message": message,
            "error": error,
        }

    def _load_user_baseline(
        self,
        username: str,
        model_version: str | None,
    ) -> UserBaseline | None:
        """读取并转换某个用户的最新 baseline。"""
        baseline_row = self.baseline_store.get_user_baseline(
            username,
            model_version=model_version,
        )
        return self._coerce_user_baseline(baseline_row)

    def _coerce_user_baseline(self, value: Any) -> UserBaseline | None:
        """兼容 BaselineStore 行字典、baseline_json 字典和测试对象。"""
        if value is None:
            return None
        if isinstance(value, UserBaseline):
            return value
        if not isinstance(value, dict):
            return None

        payload = value.get("baseline") if isinstance(value.get("baseline"), dict) else value
        if not isinstance(payload, dict):
            return None
        if not self._has_required_baseline_fields(payload):
            return None

        return UserBaseline(
            username=str(payload["username"]),
            sample_count=int(payload.get("sample_count") or 0),
            is_reliable=self._to_bool(payload.get("is_reliable")),
            common_active_hours=self._count_ratio_items(payload.get("common_active_hours")),
            common_source_ips=self._count_ratio_items(payload.get("common_source_ips")),
            common_destination_ips=self._count_ratio_items(payload.get("common_destination_ips")),
            common_source_countries=self._count_ratio_items(payload.get("common_source_countries")),
            common_source_cities=self._count_ratio_items(payload.get("common_source_cities")),
            common_vpn_gateways=self._count_ratio_items(payload.get("common_vpn_gateways")),
            action_distribution=self._to_distribution(payload.get("action_distribution")),
            event_type_distribution=self._to_distribution(payload.get("event_type_distribution")),
            result_distribution=self._to_distribution(payload.get("result_distribution")),
            fail_reason_distribution=self._to_distribution(payload.get("fail_reason_distribution")),
            auth_method_distribution=self._to_distribution(payload.get("auth_method_distribution")),
            client_software_distribution=self._to_distribution(payload.get("client_software_distribution")),
            protocol_distribution=self._to_distribution(payload.get("protocol_distribution")),
            failed_rate=self._to_float(payload.get("failed_rate")),
            off_hours_rate=self._to_float(payload.get("off_hours_rate")),
            unusual_ip_rate=self._to_float(payload.get("unusual_ip_rate")),
            avg_daily_events=self._to_float(payload.get("avg_daily_events")),
            session_duration_avg=self._to_float(payload.get("session_duration_avg")),
            session_duration_p50=self._to_float(payload.get("session_duration_p50")),
            session_duration_p95=self._to_float(payload.get("session_duration_p95")),
            bytes_sent_avg=self._to_float(payload.get("bytes_sent_avg")),
            bytes_recv_avg=self._to_float(payload.get("bytes_recv_avg")),
            active_day_avg_events=self._to_float(payload.get("active_day_avg_events")),
            max_daily_events=int(payload.get("max_daily_events") or 0),
            baseline_start_time=payload.get("baseline_start_time"),
            baseline_end_time=payload.get("baseline_end_time"),
            model_version=str(payload.get("model_version") or ""),
        )

    def _has_required_baseline_fields(self, payload: dict[str, Any]) -> bool:
        """确认 baseline 至少具备评分所需的核心字段。"""
        required = {
            "username",
            "sample_count",
            "is_reliable",
            "baseline_start_time",
            "baseline_end_time",
            "model_version",
        }
        return required.issubset(payload)

    def _count_ratio_items(self, value: Any) -> list[CountRatioItem]:
        """将 baseline JSON 中的 Top-N 项转换为 CountRatioItem。"""
        if not isinstance(value, list):
            return []

        items: list[CountRatioItem] = []
        for item in value:
            if isinstance(item, CountRatioItem):
                items.append(item)
                continue
            if not isinstance(item, dict):
                continue
            if "value" not in item:
                continue
            items.append(
                CountRatioItem(
                    value=item.get("value"),
                    count=int(item.get("count") or 0),
                    ratio=self._to_float(item.get("ratio")),
                )
            )
        return items

    def _to_distribution(self, value: Any) -> dict[str, float]:
        """规范化 baseline 分布字典。"""
        if not isinstance(value, dict):
            return {}
        return {str(key): self._to_float(raw) for key, raw in value.items()}

    def _to_bool(self, value: Any) -> bool:
        """兼容 ClickHouse UInt8、Bool 和 JSON 字符串。"""
        if isinstance(value, bool):
            return value
        if isinstance(value, int):
            return value != 0
        if isinstance(value, str):
            return value.strip().lower() in {"1", "true", "yes"}
        return bool(value)

    def _to_float(self, value: Any) -> float:
        """将缺失或空值安全转换为 float。"""
        if value in (None, ""):
            return 0.0
        return float(value)

    def _count_by(self, results: list[UebaValidationResult], field_name: str) -> dict[str, int]:
        """按结果字段统计分布。"""
        counts: dict[str, int] = {}
        for result in results:
            value = str(getattr(result, field_name))
            counts[value] = counts.get(value, 0) + 1
        return counts

    def _sample_result(self, result: UebaValidationResult) -> dict[str, Any]:
        """返回受控的样例结果，避免摘要过大。"""
        return {
            "log_id": result.source_log_id,
            "source_identity": result.source_identity,
            "username": result.username,
            "score": result.ueba_score,
            "risk_level": result.ueba_risk_level,
            "validation_status": result.validation_status,
            "reason_codes": [reason.code for reason in result.ueba_anomaly_reasons],
        }

    def _validation_run_id(
        self,
        *,
        validation_run_id: str | None,
        start_time: str,
        end_time: str,
        log_type: str,
        model_version: str | None,
        dry_run: bool,
    ) -> str:
        """Return a caller-provided or generated run identifier."""
        if validation_run_id is not None and validation_run_id.strip():
            return validation_run_id.strip()
        created_at = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
        raw = "|".join(
            [
                start_time,
                end_time,
                log_type,
                model_version or "",
                "dry" if dry_run else "write",
                created_at,
            ]
        )
        short_hash = hashlib.sha256(raw.encode("utf-8")).hexdigest()[:8]
        return f"ueba_validation_{created_at}_{short_hash}"

    def _validate_sample_size(self, sample_size: int) -> int:
        """限制样例结果数量。"""
        if not isinstance(sample_size, int) or sample_size < 0:
            raise ValueError("sample_size must be a non-negative integer")
        return sample_size

    def _message(self, selected_count: int, scored_count: int, dry_run: bool) -> str:
        """生成简短运行说明。"""
        mode = "dry-run" if dry_run else "validation"
        if selected_count == 0:
            return f"{mode} selected 0 target logs"
        return f"{mode} scored {scored_count} of {selected_count} target logs"

    def _row_error_text(self, row_errors: list[str]) -> str | None:
        """返回受控的单行错误摘要。"""
        if not row_errors:
            return None
        return "; ".join(row_errors[:5])


__all__ = ["UebaValidationService"]
