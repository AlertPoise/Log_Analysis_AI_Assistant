"""UEBA 正式管理服务。

提供 build_baseline / update_training_and_rebuild / run_validation 三个写操作入口。
统一参数校验、同步编排、进程内并发拒绝、异常脱敏和 client 生命周期管理。
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
import logging
import os
import re
import threading
import time
from typing import Any
from uuid import uuid4

from ..utils.config import settings as app_settings
from .aggregate_merger import AggregateMerger
from .baseline_builder import BaselineBuilder
from .baseline_store import BaselineStore
from .config import UebaBaselineConfig
from .repository import UebaRepository
from .schemas import BaselineBuildResult
from .service import UebaService
from .training_log_store import TrainingLogStore
from .validation_repository import UebaValidationRepository
from .validation_service import UebaValidationService

logger = logging.getLogger(__name__)

_WRITE_LOCK = threading.Lock()


def _default_client_factory(database: str) -> Any:
    """使用环境变量创建正式 ClickHouse client。"""
    import clickhouse_connect

    return clickhouse_connect.get_client(
        host=app_settings.clickhouse_host,
        port=app_settings.clickhouse_port,
        username=app_settings.clickhouse_user,
        password=app_settings.clickhouse_password,
        database=database,
        secure=getattr(app_settings, 'clickhouse_secure', False),
    )


def _parse_datetime(value: str | datetime, *, field_name: str) -> datetime:
    """将字符串或 datetime 统一解析为 datetime。"""
    if isinstance(value, datetime):
        return value
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field_name} 不能为空")
    value = value.strip()
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d"):
        try:
            return datetime.strptime(value, fmt)
        except ValueError:
            continue
    raise ValueError(f"{field_name} 格式无效，支持 YYYY-MM-DD HH:MM:SS / YYYY-MM-DDTHH:MM:SS / YYYY-MM-DD")


def _redact_sensitive_text(value: object) -> str:
    """脱敏：移除 ClickHouse 密码和常见凭证形态。"""
    s = str(value)
    pw = os.getenv("CLICKHOUSE_PASSWORD", "")
    if pw:
        s = s.replace(pw, "***")
    s = re.sub(r"password[=:]\s*\S+", "password=***", s, flags=re.IGNORECASE)
    s = re.sub(r"--clickhouse-password\s+\S+", "--clickhouse-password ***", s, flags=re.IGNORECASE)
    s = re.sub(r"://[^@:]+:[^@]+@", "://***:***@", s)
    return s


def _safe_short_error(msg: str) -> str:
    """将内部错误文本截断脱敏。"""
    cleaned = _redact_sensitive_text(msg)
    if len(cleaned) > 200:
        cleaned = cleaned[:197] + "..."
    return cleaned


def _sanitize_result(value: Any) -> Any:
    """递归脱敏：对 str/dict/list 中的敏感信息做安全处理。"""
    if isinstance(value, str):
        return _redact_sensitive_text(value)
    if isinstance(value, dict):
        return {str(k): _sanitize_result(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_sanitize_result(i) for i in value]
    if isinstance(value, tuple):
        return tuple(_sanitize_result(i) for i in value)
    return value


def _error_result(
    operation: str,
    stage: str,
    code: str,
    message: str,
    details: dict[str, Any] | None = None,
) -> dict[str, Any]:
    safe_details = _sanitize_result(details or {})
    safe_message = _redact_sensitive_text(message)
    return {
        "success": False,
        "operation": operation,
        "stage": stage,
        "message": safe_message,
        "error": {"code": code, "message": safe_message},
        "details": safe_details,
    }


def _ok_result(
    operation: str,
    message: str,
    details: dict[str, Any] | None = None,
) -> dict[str, Any]:
    safe_details = _sanitize_result(details or {})
    return {
        "success": True,
        "operation": operation,
        "stage": "COMPLETED",
        "message": message,
        "error": None,
        "details": safe_details,
    }


def _close_client(client: Any) -> None:
    """安全关闭 ClickHouse client。"""
    if client is not None and hasattr(client, "close"):
        try:
            client.close()
        except Exception:
            logger.exception("Failed to close ClickHouse client")


def _build_duration(self_begin: float) -> float:
    return round(time.time() - self_begin, 2)


class UebaManagementService:
    """UEBA 正式管理服务 — 同步编排写操作。"""

    def __init__(
        self,
        *,
        client_factory: Callable[[], Any] | None = None,
        database: str | None = None,
    ) -> None:
        self._database = database or os.getenv("CLICKHOUSE_DATABASE", "log_analysis")
        self._client_factory = client_factory or (lambda: _default_client_factory(self._database))

    # ------------------------------------------------------------------
    # build_baseline
    # ------------------------------------------------------------------

    def build_baseline(
        self,
        *,
        baseline_start_time: str | datetime,
        baseline_end_time: str | datetime,
        model_version: str,
        confirmed: bool = False,
    ) -> dict[str, Any]:
        operation = "build_baseline"
        if not confirmed:
            return _error_result(operation, "VALIDATING_PARAMETERS", "CONFIRMATION_REQUIRED",
                                 "操作需要二次确认。")

        try:
            start_dt = _parse_datetime(baseline_start_time, field_name="baseline_start_time")
            end_dt = _parse_datetime(baseline_end_time, field_name="baseline_end_time")
        except ValueError as exc:
            return _error_result(operation, "VALIDATING_PARAMETERS", "INVALID_PARAMETERS", str(exc))

        if start_dt >= end_dt:
            return _error_result(operation, "VALIDATING_PARAMETERS", "INVALID_PARAMETERS",
                                 "baseline_start_time 必须早于 baseline_end_time")

        if not isinstance(model_version, str) or not model_version.strip():
            return _error_result(operation, "VALIDATING_PARAMETERS", "INVALID_PARAMETERS",
                                 "model_version 不能为空")
        model_version = model_version.strip()

        if not _WRITE_LOCK.acquire(blocking=False):
            return _error_result(operation, "BUSY", "WRITE_OPERATION_BUSY",
                                 "已有 UEBA 管理操作正在执行，请稍后重试。")

        client = None
        begin = time.time()
        details: dict[str, Any] = {
            "model_version": model_version,
            "baseline_start_time": baseline_start_time,
            "baseline_end_time": baseline_end_time,
            "log_type": "vpn",
        }
        try:
            client = self._client_factory()
            client.command("SELECT 1")
            store = BaselineStore(client=client, database=self._database)
            store.ensure_table()

            if store.get_baseline_summary(model_version) is not None:
                return _error_result(operation, "CHECKING_MODEL_VERSION",
                                     "MODEL_VERSION_ALREADY_EXISTS",
                                     f"模型版本 {model_version} 已存在，请使用新的版本名称。",
                                     details=details)

            config = UebaBaselineConfig(model_version=model_version)
            repository = UebaRepository(client=client, database=self._database)
            service = UebaService(
                repository=repository,
                aggregate_merger=AggregateMerger(),
                baseline_builder=BaselineBuilder(config=config),
                baseline_store=store,
                config=config,
            )
            result: BaselineBuildResult = service.build_baseline_once(start_dt, end_dt, log_type="vpn")

            details.update({
                "total_user_count": result.total_user_count,
                "reliable_user_count": result.reliable_user_count,
                "unreliable_user_count": result.unreliable_user_count,
                "total_log_count": result.total_log_count,
                "duration_seconds": _build_duration(begin),
            })

            if result.success:
                return _ok_result(operation, "baseline 构建成功。", details=details)
            return _error_result(operation, "BUILDING_BASELINE", "BUILD_BASELINE_FAILED",
                                 "baseline 构建失败，请查看后端日志。", details=details)

        except Exception as exc:
            logger.exception("build_baseline failed")
            return _error_result(operation, "FAILED", "BUILD_BASELINE_FAILED",
                                 "baseline 构建失败，请查看后端日志。", details=details)
        finally:
            _close_client(client)
            _WRITE_LOCK.release()

    # ------------------------------------------------------------------
    # update_training_and_rebuild
    # ------------------------------------------------------------------

    def update_training_and_rebuild(
        self,
        *,
        training_start_time: str | datetime,
        training_end_time: str | datetime,
        dataset_id: str,
        baseline_purpose: str,
        mode: str = "replace",
        rebuild_model_version: str,
        confirmed: bool = False,
        remark: str | None = None,
        created_by: str | None = None,
    ) -> dict[str, Any]:
        operation = "update_training_and_rebuild"
        if not confirmed:
            return _error_result(operation, "VALIDATING_PARAMETERS", "CONFIRMATION_REQUIRED",
                                 "操作需要二次确认。")

        # 参数校验
        try:
            start_dt = _parse_datetime(training_start_time, field_name="training_start_time")
            end_dt = _parse_datetime(training_end_time, field_name="training_end_time")
        except ValueError as exc:
            return _error_result(operation, "VALIDATING_PARAMETERS", "INVALID_PARAMETERS", str(exc))

        if start_dt >= end_dt:
            return _error_result(operation, "VALIDATING_PARAMETERS", "INVALID_PARAMETERS",
                                 "training_start_time 必须早于 training_end_time")

        if not isinstance(dataset_id, str) or not dataset_id.strip():
            return _error_result(operation, "VALIDATING_PARAMETERS", "INVALID_PARAMETERS",
                                 "dataset_id 不能为空")
        if not isinstance(baseline_purpose, str) or not baseline_purpose.strip():
            return _error_result(operation, "VALIDATING_PARAMETERS", "INVALID_PARAMETERS",
                                 "baseline_purpose 不能为空")
        if mode not in ("replace", "append"):
            return _error_result(operation, "VALIDATING_PARAMETERS", "INVALID_PARAMETERS",
                                 "mode 只允许 replace 或 append")
        if not isinstance(rebuild_model_version, str) or not rebuild_model_version.strip():
            return _error_result(operation, "VALIDATING_PARAMETERS", "INVALID_PARAMETERS",
                                 "rebuild_model_version 不能为空")

        dataset_id = dataset_id.strip()
        baseline_purpose = baseline_purpose.strip()
        rebuild_model_version = rebuild_model_version.strip()
        import_batch_id = uuid4().hex

        if not _WRITE_LOCK.acquire(blocking=False):
            return _error_result(operation, "BUSY", "WRITE_OPERATION_BUSY",
                                 "已有 UEBA 管理操作正在执行，请稍后重试。")

        client = None
        begin = time.time()
        details: dict[str, Any] = {
            "dataset_id": dataset_id,
            "import_batch_id": import_batch_id,
            "mode": mode,
            "rebuild_model_version": rebuild_model_version,
            "training_start_time": training_start_time,
            "training_end_time": training_end_time,
            "log_type": "vpn",
        }
        training_updated = False
        try:
            client = self._client_factory()
            client.command("SELECT 1")
            store = BaselineStore(client=client, database=self._database)
            store.ensure_table()
            training_store = TrainingLogStore(client=client, database=self._database)

            if store.get_baseline_summary(rebuild_model_version) is not None:
                return _error_result(operation, "CHECKING_MODEL_VERSION",
                                     "MODEL_VERSION_ALREADY_EXISTS",
                                     f"模型版本 {rebuild_model_version} 已存在，请使用新的版本名称。",
                                     details=details)

            training_store.ensure_table()

            start_str = start_dt.strftime("%Y-%m-%d %H:%M:%S")
            end_str = end_dt.strftime("%Y-%m-%d %H:%M:%S")

            if mode == "replace":
                training_store.replace_from_logs_structured(
                    start_time=start_str,
                    end_time=end_str,
                    dataset_id=dataset_id,
                    baseline_purpose=baseline_purpose,
                    import_batch_id=import_batch_id,
                    log_type="vpn",
                    is_active=1,
                    remark=remark,
                    created_by=created_by,
                )
            else:
                training_store.append_from_logs_structured(
                    start_time=start_str,
                    end_time=end_str,
                    dataset_id=dataset_id,
                    baseline_purpose=baseline_purpose,
                    import_batch_id=import_batch_id,
                    log_type="vpn",
                    is_active=1,
                    remark=remark,
                    created_by=created_by,
                )
            training_updated = True

            # 构造通过训练表读取的 repository
            config = UebaBaselineConfig(model_version=rebuild_model_version)
            training_repo = UebaRepository(
                client=client,
                database=self._database,
                source_table=TrainingLogStore.TARGET_TABLE,
                dataset_id=dataset_id,
                active_only=True,
            )
            service = UebaService(
                repository=training_repo,
                aggregate_merger=AggregateMerger(),
                baseline_builder=BaselineBuilder(config=config),
                baseline_store=store,
                config=config,
            )
            result: BaselineBuildResult = service.build_baseline_once(start_dt, end_dt, log_type="vpn")

            details.update({
                "total_user_count": result.total_user_count,
                "reliable_user_count": result.reliable_user_count,
                "unreliable_user_count": result.unreliable_user_count,
                "total_log_count": result.total_log_count,
                "duration_seconds": _build_duration(begin),
            })

            if result.success:
                return _ok_result(operation, "训练日志更新并重建 baseline 成功。", details=details)
            return _error_result(operation, "BUILDING_BASELINE", "REBUILD_BASELINE_FAILED",
                                 "训练日志更新成功，但重建 baseline 失败，请查看后端日志。",
                                 details=details)

        except Exception as exc:
            logger.exception("update_training_and_rebuild failed")
            if not training_updated:
                return _error_result(operation, "FAILED", "TRAINING_UPDATE_FAILED",
                                     "训练日志更新失败，未继续重建 baseline。", details=details)
            return _error_result(operation, "BUILDING_BASELINE", "REBUILD_BASELINE_FAILED",
                                 "训练日志更新成功，但重建 baseline 失败，请查看后端日志。", details=details)
        finally:
            _close_client(client)
            _WRITE_LOCK.release()

    # ------------------------------------------------------------------
    # run_validation
    # ------------------------------------------------------------------

    def run_validation(
        self,
        *,
        validation_start_time: str | datetime,
        validation_end_time: str | datetime,
        baseline_model_version: str,
        validation_run_id: str | None = None,
        confirmed: bool = False,
    ) -> dict[str, Any]:
        operation = "run_validation"
        if not confirmed:
            return _error_result(operation, "VALIDATING_PARAMETERS", "CONFIRMATION_REQUIRED",
                                 "操作需要二次确认。")

        try:
            start_dt = _parse_datetime(validation_start_time, field_name="validation_start_time")
            end_dt = _parse_datetime(validation_end_time, field_name="validation_end_time")
        except ValueError as exc:
            return _error_result(operation, "VALIDATING_PARAMETERS", "INVALID_PARAMETERS", str(exc))

        if start_dt >= end_dt:
            return _error_result(operation, "VALIDATING_PARAMETERS", "INVALID_PARAMETERS",
                                 "validation_start_time 必须早于 validation_end_time")

        if not isinstance(baseline_model_version, str) or not baseline_model_version.strip():
            return _error_result(operation, "VALIDATING_PARAMETERS", "INVALID_PARAMETERS",
                                 "baseline_model_version 不能为空")
        baseline_model_version = baseline_model_version.strip()

        if validation_run_id is not None:
            if not isinstance(validation_run_id, str) or not validation_run_id.strip():
                return _error_result(operation, "VALIDATING_PARAMETERS", "INVALID_PARAMETERS",
                                     "validation_run_id 填写时不能为空")

        if not _WRITE_LOCK.acquire(blocking=False):
            return _error_result(operation, "BUSY", "WRITE_OPERATION_BUSY",
                                 "已有 UEBA 管理操作正在执行，请稍后重试。")

        client = None
        begin = time.time()
        details: dict[str, Any] = {
            "baseline_model_version": baseline_model_version,
            "validation_start_time": validation_start_time,
            "validation_end_time": validation_end_time,
            "log_type": "vpn",
        }
        try:
            client = self._client_factory()
            client.command("SELECT 1")
            store = BaselineStore(client=client, database=self._database)
            store.ensure_table()

            if store.get_baseline_summary(baseline_model_version) is None:
                return _error_result(operation, "CHECKING_MODEL_VERSION",
                                     "BASELINE_MODEL_NOT_FOUND",
                                     f"baseline 版本 {baseline_model_version} 不存在。",
                                     details=details)

            start_str = start_dt.strftime("%Y-%m-%d %H:%M:%S")
            end_str = end_dt.strftime("%Y-%m-%d %H:%M:%S")

            validation_repo = UebaValidationRepository(client=client, database=self._database)
            vs = UebaValidationService(
                validation_repository=validation_repo,
                baseline_store=store,
            )

            result = vs.run(
                start_time=start_str,
                end_time=end_str,
                log_type="vpn",
                model_version=baseline_model_version,
                limit=1000,
                dry_run=False,
                validation_run_id=validation_run_id.strip() if validation_run_id else None,
            )

            details.update({
                "validation_run_id": result.get("validation_run_id"),
                "processed_count": result.get("processed_count", 0),
                "written_count": result.get("written_count", 0),
                "no_baseline_count": result.get("no_baseline_count", 0),
                "duration_seconds": _build_duration(begin),
            })

            if result.get("success"):
                return _ok_result(operation, "validation 执行完成。", details=details)
            return _error_result(operation, "RUNNING_VALIDATION", "VALIDATION_FAILED",
                                 "validation 执行失败，请查看后端日志。", details=details)

        except Exception as exc:
            logger.exception("run_validation failed")
            return _error_result(operation, "FAILED", "VALIDATION_FAILED",
                                 "validation 执行失败，请查看后端日志。", details=details)
        finally:
            _close_client(client)
            _WRITE_LOCK.release()


__all__ = ["UebaManagementService"]
