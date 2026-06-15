"""
主程序入口
实现日志分析 AI 助手的主程序流程

开发任务:
1. 初始化配置
2. 启动日志采集
3. 启动日志解析
4. 启动异常检测
5. 启动定时报告任务
6. 启动 Web 服务（Streamlit Dashboard）
"""
import asyncio
import subprocess
import os
import sys
import threading
import time
from typing import Optional, Dict, Any
from datetime import datetime, timedelta
from dataclasses import asdict
from .utils.config import settings
from .utils.logger import get_logger
import clickhouse_connect

# 导入存储模块
from .storage.kafka_client import KafkaClient
from .storage.clickhouse import ClickHouseClient

# 导入采集器模块
from .collectors.filebeat import FilebeatCollector
from .collectors.flume import FlumeCollector

# 导入 AI 模块
from .ai.analyzer import AIAnalyzer

# 导入 AI 基线强化
from .ai.client import AIClient as ReinforcementAIClient
from .behavior.baseline_store import BaselineStore as ReinforcementBaselineStore
from .behavior.baseline_reinforcement import BaselineReinforcementService

logger = get_logger(__name__)


class LogAnalysisService:
    """日志分析服务主类"""

    def __init__(self):
        self.kafka_client: Optional[KafkaClient] = None
        self.clickhouse_client: Optional[ClickHouseClient] = None
        self.filebeat_collector: Optional[FilebeatCollector] = None
        self.flume_collector: Optional[FlumeCollector] = None
        self.ai_analyzer: Optional[AIAnalyzer] = None
        self.streamlit_process: Optional[subprocess.Popen] = None
        self.ueba_generator = None
        self.ueba_writer = None
        self.ueba_writer_client = None
        self.ueba_validation_stop_event: Optional[threading.Event] = None
        self.ueba_validation_thread: Optional[threading.Thread] = None
        self.ueba_model_version: Optional[str] = None

    def init_storage(self):
        """初始化存储模块"""
        logger.info("[1/4] 初始化存储模块...")

        # 初始化 Kafka 客户端
        kafka_config = {
            'bootstrap_servers': settings.kafka_bootstrap_servers,
            'producer_acks': 'all',
            'producer_retries': 3,
            'consumer_group_id': settings.kafka_consumer_group
        }
        self.kafka_client = KafkaClient(kafka_config)

        try:
            self.kafka_client.connect_producer()
            logger.info("✓ Kafka 连接成功")
        except Exception as e:
            logger.warning(f"⚠️  Kafka 连接失败: {e}")

        # 初始化 ClickHouse 客户端
        clickhouse_config = {
            'host': settings.clickhouse_host,
            'port': settings.clickhouse_port,
            'username': settings.clickhouse_user,
            'password': settings.clickhouse_password,
            'database': settings.clickhouse_database
        }
        self.clickhouse_client = ClickHouseClient(config=clickhouse_config)

        try:
            self.clickhouse_client.connect()
            logger.info("✓ ClickHouse 连接成功")

            # 初始化表结构（执行 clickhouse.sql）
            self._init_clickhouse_tables()

            # 插入测试数据（使用 gen_vpn_logs.py 生成）
            self._insert_test_data()

            # 初始化 UEBA 演示数据、baseline、validation 和持续流量
            self._init_ueba_demo_pipeline()

        except Exception as e:
            logger.warning(f"⚠️  ClickHouse 连接失败: {e}")

    def _init_clickhouse_tables(self):
        """执行 config/clickhouse.sql 初始化所有表"""
        logger.info("  初始化 ClickHouse 表结构...")
        try:
            project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
            sql_file = os.path.join(project_root, "config", "clickhouse.sql")

            if not os.path.exists(sql_file):
                logger.error(f"  ✗ SQL 文件不存在: {sql_file}")
                return

            with open(sql_file, 'r', encoding='utf-8') as f:
                sql_content = f.read()

            # 替换占位符
            sql_content = sql_content.replace("{CLICKHOUSE_DATABASE}", settings.clickhouse_database)
            sql_content = sql_content.replace("{CLICKHOUSE_USER}", settings.clickhouse_user)
            sql_content = sql_content.replace("{CLICKHOUSE_PASSWORD}", settings.clickhouse_password)
            sql_content = sql_content.replace("{CLICKHOUSE_TABLE}", settings.clickhouse_table)

            # 按分号分割，过滤空语句和纯注释
            statements = []
            for stmt in sql_content.split(';'):
                stmt = stmt.strip()
                if not stmt:
                    continue
                # 跳过纯注释行
                lines = [line for line in stmt.split('\n') if line.strip() and not line.strip().startswith('--')]
                if lines:
                    statements.append(stmt)

            logger.info(f"  共解析到 {len(statements)} 条 SQL 语句")

            success_count = 0
            for idx, stmt in enumerate(statements, 1):
                try:
                    # 跳过用户管理语句（CREATE USER / GRANT / FLUSH PRIVILEGES）
                    # 这些应由 Docker 初始化或管理员手动执行
                    # 先去掉开头的注释行，再判断是否为用户管理语句
                    stmt_lines = stmt.split('\n')
                    first_sql_line = ''
                    for line in stmt_lines:
                        stripped = line.strip()
                        if stripped and not stripped.startswith('--'):
                            first_sql_line = stripped.upper()
                            break
                    if any(first_sql_line.startswith(kw) for kw in (
                        'CREATE USER', 'GRANT ALL', 'FLUSH PRIVILEGES'
                    )):
                        logger.info(f"  [{idx}] 跳过用户管理语句（应由 Docker 初始化执行）")
                        success_count += 1
                        continue

                    self.clickhouse_client.client.command(stmt)
                    success_count += 1
                except Exception as e:
                    # Kafka 引擎表可能在非 Kafka 环境下创建失败，跳过
                    if "Kafka" in stmt and "ENGINE = Kafka" in stmt:
                        logger.warning(f"  ⚠️  [{idx}] Kafka 引擎表跳过（需要 Kafka 环境）: {e}")
                    else:
                        logger.warning(f"  ⚠️  [{idx}] SQL 执行失败: {str(e)[:100]}")

            logger.info(f"  ✓ 表初始化完成: {success_count}/{len(statements)} 条成功")

        except Exception as e:
            logger.warning(f"  ⚠️  表初始化失败: {e}")

    def _insert_test_data(self):
        """使用 gen_vpn_logs.py 生成测试数据并插入 ClickHouse"""
        logger.info("  插入测试数据...")
        try:
            # 检查表中是否已有数据
            result = self.clickhouse_client.client.query(
                f"SELECT count(*) FROM {settings.clickhouse_table}"
            )
            count = int(result.result_rows[0][0]) if result.result_rows else 0

            if count > 0:
                logger.info(f"  ✓ 表中已有 {count} 条数据，跳过插入")
                return

            # 导入 gen_vpn_logs 模块
            project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
            tests_collectors_dir = os.path.join(project_root, "tests", "collectors")
            if tests_collectors_dir not in sys.path:
                sys.path.insert(0, tests_collectors_dir)

            try:
                from gen_vpn_logs import generate_logs
            except ImportError as e:
                logger.warning(f"  ⚠️  无法导入 gen_vpn_logs: {e}")
                logger.info("  使用内置简单数据...")
                self._insert_simple_test_data()
                return

            # 生成 7 天测试数据，每天约 50 条
            logger.info("  使用 gen_vpn_logs 生成 VPN 日志数据...")
            logs = generate_logs(
                start_date=datetime(2026, 6, 1),
                days=7,
                normal_per_day=50,
                fail_ratio=0.08,
                anomaly_ratio=0.03,
            )

            # 将 VPNLogEntry 转换为 logs_structured 表格式的行数据
            # clickhouse.sql 中 logs_structured 的列顺序
            columns = [
                'id', 'timestamp', 'log_type', 'source', 'username',
                'user_id', 'dept', 'role', 'action', 'event_type',
                'result', 'fail_reason', 'source_ip', 'destination_ip',
                'vpn_gateway', 'src_country', 'src_city', 'protocol',
                'auth_method', 'client_software', 'user_agent', 'session_id',
                'is_off_hours', 'is_unusual_ip', 'session_duration_sec',
                'bytes_sent', 'bytes_recv', 'risk_score', 'risk_tags',
                'uri', 'method', 'status_code', 'response_time', 'detail',
                'severity_level', 'device_info', 'location', 'request_id',
                'collected_at', 'parsed_at', 'indexed_at', 'raw_log',
                'parser', 'parse_status',
            ]

            rows = []
            for idx, log in enumerate(logs, 1):
                d = asdict(log)
                row = []
                for col in columns:
                    if col == 'id':
                        val = idx
                    elif col == 'log_type':
                        val = 'vpn'
                    elif col == 'source':
                        val = d.get('vpn_gateway', '')
                    elif col == 'action':
                        # 从 event_type 映射 action
                        event = d.get('event_type', '')
                        if 'LOGIN_SUCCESS' in event:
                            val = 'LOGIN'
                        elif 'LOGIN_FAIL' in event:
                            val = 'LOGIN'
                        elif 'LOGOUT' in event:
                            val = 'LOGOUT'
                        elif 'SESSION_TIMEOUT' in event:
                            val = 'SESSION_TIMEOUT'
                        else:
                            val = event
                    elif col == 'destination_ip':
                        val = d.get('dst_internal_ip', '')
                    elif col == 'source_ip':
                        val = d.get('src_ip', '')
                    elif col == 'collected_at':
                        val = datetime.now()
                    elif col == 'parsed_at':
                        val = datetime.now()
                    elif col == 'indexed_at':
                        val = datetime.now()
                    elif col == 'raw_log':
                        val = f"{d['timestamp']} {d['vpn_gateway']} vpnd: event={d['event_type']} user={d['username']}"
                    elif col == 'parser':
                        val = 'gen_vpn_logs'
                    elif col == 'parse_status':
                        val = 'success'
                    elif col == 'timestamp':
                        ts = d.get('timestamp', '')
                        if isinstance(ts, str):
                            try:
                                val = datetime.strptime(ts, "%Y-%m-%d %H:%M:%S")
                            except ValueError:
                                val = datetime.now()
                        else:
                            val = datetime.now()
                    elif col in ('user_id', 'user_agent', 'uri', 'method',
                                 'status_code', 'response_time', 'detail',
                                 'severity_level', 'device_info', 'location',
                                 'request_id'):
                        val = None
                    else:
                        val = d.get(col)

                    # 类型转换
                    if col in ('session_duration_sec', 'bytes_sent', 'bytes_recv', 'risk_score', 'status_code'):
                        if val is None or val == '':
                            val = None
                        else:
                            try:
                                val = int(val)
                            except (ValueError, TypeError):
                                val = None
                    elif col in ('is_off_hours', 'is_unusual_ip'):
                        if val is not None:
                            val = bool(val)

                    row.append(val)
                rows.append(row)

            # 批量插入
            batch_size = 500
            total_inserted = 0
            for i in range(0, len(rows), batch_size):
                batch = rows[i:i + batch_size]
                self.clickhouse_client.client.insert(
                    settings.clickhouse_table,
                    batch,
                    column_names=columns,
                    database=settings.clickhouse_database,
                )
                total_inserted += len(batch)

            logger.info(f"  ✓ 已插入 {total_inserted} 条 VPN 日志测试数据")

        except Exception as e:
            logger.warning(f"  ⚠️  插入测试数据失败: {e}")

    def _insert_simple_test_data(self):
        """内置简单测试数据（gen_vpn_logs 不可用时的后备方案）"""
        columns = [
            'id', 'timestamp', 'log_type', 'source', 'username',
            'action', 'event_type', 'result', 'source_ip',
            'risk_score', 'collected_at',
        ]
        rows = []
        for i in range(1, 11):
            rows.append([
                i, datetime.now(), 'vpn', f'vpn_gateway_{(i % 3) + 1}',
                f'user{i}', 'LOGIN', 'AUTH', 'SUCCESS' if i != 5 else 'FAIL',
                f'192.168.1.{i * 10}', 85 if i != 5 else 95, datetime.now(),
            ])

        try:
            self.clickhouse_client.client.insert(
                settings.clickhouse_table,
                rows,
                column_names=columns,
                database=settings.clickhouse_database,
            )
            logger.info(f"  ✓ 已插入 {len(rows)} 条简单测试数据")
        except Exception as e:
            logger.warning(f"  ⚠️  简单数据插入失败: {e}")

    def _init_ueba_demo_pipeline(self):
        """加载 UEBA fixture、构建 baseline，并启动持续数据与周期 validation。"""
        enabled = os.getenv("UEBA_MAIN_BOOTSTRAP_ENABLED", "true").lower() not in {"0", "false", "no"}
        if not enabled:
            logger.info("  UEBA main 启动数据生成已关闭：UEBA_MAIN_BOOTSTRAP_ENABLED=false")
            return

        logger.info("  初始化 UEBA main 启动数据生成与评分链路...")
        try:
            config = self._build_ueba_acceptance_config()
            self.ueba_model_version = config.model_version
            if config.clean_before_load:
                self._cleanup_ueba_fixture_artifacts(config)
            self._load_ueba_fixture_data(config)
            self._ensure_ueba_baseline(config)
            self._run_ueba_recent_validation(
                config.model_version,
                run_id_prefix="main_bootstrap_initial",
            )
            self._start_ueba_continuous_generation(config)
        except Exception as e:
            logger.warning(f"  ⚠️  UEBA main 启动链路初始化失败: {e}", exc_info=True)

    def _build_ueba_acceptance_config(self):
        """使用应用 ClickHouse 配置构造 tests/behavior acceptance 配置。"""
        from tests.behavior.ueba_baseline_acceptance.config import AcceptanceConfig

        return AcceptanceConfig(
            clickhouse_host=settings.clickhouse_host,
            clickhouse_port=settings.clickhouse_port,
            clickhouse_user=settings.clickhouse_user,
            clickhouse_password=settings.clickhouse_password,
            clickhouse_database=settings.clickhouse_database,
            clickhouse_batch_size=int(os.getenv("UEBA_MAIN_BOOTSTRAP_BATCH_SIZE", "2000")),
            clean_before_load=os.getenv("UEBA_MAIN_CLEAN_FIXTURE", "true").lower() not in {"0", "false", "no"},
        )

    def _create_native_clickhouse_client(self):
        """创建 clickhouse-connect 原生 client，供 UEBA 组件复用。"""
        import clickhouse_connect

        client = clickhouse_connect.get_client(
            host=settings.clickhouse_host,
            port=settings.clickhouse_port,
            username=settings.clickhouse_user,
            password=settings.clickhouse_password,
            database=settings.clickhouse_database,
        )
        client.command("SELECT 1")
        return client

    def _cleanup_ueba_fixture_artifacts(self, config):
        """清理 main 生成的旧 UEBA fixture 日志、baseline 和 validation。"""
        logger.info(f"  清理旧 UEBA fixture 数据: model_version={config.model_version}")
        client = None
        try:
            client = self._create_native_clickhouse_client()
            users = config.fixture_usernames
            placeholders = ", ".join(f"%(u{i})s" for i in range(len(users)))
            params = {f"u{i}": username for i, username in enumerate(users)}
            params.update({
                "model_version": config.model_version,
                "log_type": config.log_type,
            })

            commands = [
                (
                    f"""
                    ALTER TABLE {settings.clickhouse_database}.ueba_validation_results
                    DELETE WHERE baseline_model_version = %(model_version)s
                       OR username IN ({placeholders})
                    SETTINGS mutations_sync = 1
                    """,
                    "validation",
                ),
                (
                    f"""
                    ALTER TABLE {settings.clickhouse_database}.user_behavior_baselines
                    DELETE WHERE model_version = %(model_version)s
                    SETTINGS mutations_sync = 1
                    """,
                    "baseline",
                ),
                (
                    f"""
                    ALTER TABLE {settings.clickhouse_database}.{settings.clickhouse_table}
                    DELETE WHERE log_type = %(log_type)s
                      AND (
                        username IN ({placeholders})
                        OR position(ifNull(raw_log, ''), 'ueba fixture generated log') > 0
                        OR position(ifNull(raw_log, ''), 'ueba_continuous_fixture') > 0
                      )
                    SETTINGS mutations_sync = 1
                    """,
                    "logs_structured",
                ),
            ]

            for sql, label in commands:
                try:
                    client.command(sql, parameters=params)
                    logger.info(f"  ✓ 已清理旧 UEBA fixture {label}")
                except Exception as exc:
                    logger.warning(f"  ⚠️  清理旧 UEBA fixture {label} 失败: {exc}")
        finally:
            if client is not None:
                client.close()

    def _ueba_clickhouse_window(self, start_time: str, end_time: str) -> tuple[str, str]:
        """把前端展示窗口转换为 ClickHouse 原始 timestamp 查询窗口。"""
        local_offset = datetime.now().astimezone().utcoffset()
        start_dt = datetime.strptime(start_time, "%Y-%m-%d %H:%M:%S")
        end_dt = datetime.strptime(end_time, "%Y-%m-%d %H:%M:%S")
        if local_offset:
            start_dt -= local_offset
            end_dt -= local_offset
        return (
            start_dt.strftime("%Y-%m-%d %H:%M:%S"),
            end_dt.strftime("%Y-%m-%d %H:%M:%S"),
        )

    def _load_ueba_fixture_data(self, config):
        """把 tests/behavior fixture 大批量写入 logs_structured。"""
        from tests.behavior.ueba_baseline_acceptance.clickhouse_writer import load_fixture_to_clickhouse

        logger.info(
            f"  加载 UEBA fixture 日志: fixture_id={config.fixture_id}, "
            f"expected_logs={config.total_expected_logs}"
        )
        result = load_fixture_to_clickhouse(config)
        if not result.get("success"):
            raise RuntimeError(f"UEBA fixture 加载失败: {result.get('error')}")
        logger.info(
            f"  ✓ UEBA fixture 已写入 logs_structured: inserted={result.get('inserted_rows')}, "
            f"database_rows={result.get('database_rows')}"
        )

    def _ensure_ueba_baseline(self, config):
        """确保指定 fixture model_version 的 baseline 已存在。"""
        from src.behavior.baseline_store import BaselineStore
        from src.behavior.ueba_management_service import UebaManagementService

        client = None
        try:
            client = self._create_native_clickhouse_client()
            store = BaselineStore(client=client, database=settings.clickhouse_database)
            store.ensure_table()
            existing = store.get_baseline_summary(config.model_version)
            if existing is not None:
                logger.info(f"  ✓ UEBA baseline 已存在，跳过构建: {config.model_version}")
                return
        finally:
            if client is not None:
                client.close()

        logger.info(f"  构建 UEBA baseline: model_version={config.model_version}")
        service = UebaManagementService(
            client_factory=self._create_native_clickhouse_client,
            database=settings.clickhouse_database,
        )
        baseline_start_time, baseline_end_time = self._ueba_clickhouse_window(
            config.start_time,
            config.end_time,
        )
        result = service.build_baseline(
            baseline_start_time=baseline_start_time,
            baseline_end_time=baseline_end_time,
            model_version=config.model_version,
            confirmed=True,
        )
        if not result.get("success"):
            raise RuntimeError(f"UEBA baseline 构建失败: {result.get('message')}")
        details = result.get("details", {})
        logger.info(
            f"  ✓ UEBA baseline 构建完成: users={details.get('total_user_count')}, "
            f"reliable={details.get('reliable_user_count')}, logs={details.get('total_log_count')}"
        )

    def _run_ueba_recent_validation(self, model_version: str, *, run_id_prefix: str) -> dict[str, Any]:
        """对最近 24 小时日志执行一次 UEBA validation 并写入结果表。"""
        from src.behavior.baseline_store import BaselineStore
        from src.behavior.validation_repository import UebaValidationRepository
        from src.behavior.validation_service import UebaValidationService

        end_dt = datetime.now() + timedelta(seconds=5)
        start_dt = end_dt - timedelta(hours=24)
        start_time = start_dt.strftime("%Y-%m-%d %H:%M:%S")
        end_time = end_dt.strftime("%Y-%m-%d %H:%M:%S")
        validation_limit = int(os.getenv("UEBA_MAIN_VALIDATION_LIMIT", "5000"))
        validation_run_id = f"{run_id_prefix}_{int(time.time())}"

        client = None
        try:
            client = self._create_native_clickhouse_client()
            store = BaselineStore(client=client, database=settings.clickhouse_database)
            repository = UebaValidationRepository(client=client, database=settings.clickhouse_database)
            service = UebaValidationService(
                validation_repository=repository,
                baseline_store=store,
            )
            result = service.run(
                start_time=start_time,
                end_time=end_time,
                log_type="vpn",
                model_version=model_version,
                limit=validation_limit,
                dry_run=False,
                validation_run_id=validation_run_id,
                require_baseline=True,
            )
            logger.info(
                f"  UEBA validation: run_id={result.get('validation_run_id')}, "
                f"processed={result.get('processed_count')}, written={result.get('written_count')}, "
                f"no_baseline={result.get('no_baseline_count')}"
            )
            if not result.get("success"):
                logger.warning(f"  ⚠️  UEBA validation 失败: {result.get('error')}")
            return result
        finally:
            if client is not None:
                client.close()

    def _start_ueba_continuous_generation(self, config):
        """启动持续登录数据生成器和周期 validation 线程。"""
        if self.ueba_generator is not None:
            return

        from tests.behavior.ueba_baseline_acceptance.clickhouse_writer import FixtureClickHouseWriter
        from tests.behavior.ueba_baseline_acceptance.continuous_login_generator import ContinuousLoginGenerator

        logs_per_second = int(os.getenv("UEBA_MAIN_CONTINUOUS_LPS", "20"))
        mode = os.getenv("UEBA_MAIN_CONTINUOUS_MODE", "risk_mix")
        if "UEBA_MAIN_CONTINUOUS_USERS" in os.environ:
            usernames = [
                value.strip()
                for value in os.getenv("UEBA_MAIN_CONTINUOUS_USERS", "").split(",")
                if value.strip()
            ]
        elif "UEBA_MAIN_CONTINUOUS_USER" in os.environ:
            usernames = [os.getenv("UEBA_MAIN_CONTINUOUS_USER", "fixture_user_stable_0001")]
        else:
            usernames = [
                "fixture_user_stable_0001",
                "fixture_user_stable_0002",
                "fixture_user_stable_0003",
                "fixture_user_stable_0004",
            ]
        if not usernames:
            usernames = ["fixture_user_stable_0001"]

        self.ueba_writer_client = self._create_native_clickhouse_client()
        self.ueba_writer = FixtureClickHouseWriter(
            client=self.ueba_writer_client,
            database=settings.clickhouse_database,
            batch_size=config.clickhouse_batch_size,
        )
        self.ueba_generator = ContinuousLoginGenerator(
            writer_callback=self.ueba_writer.insert_logs,
            logs_per_second=logs_per_second,
            mode=mode,
            username=usernames[0],
            usernames=usernames,
        )
        self.ueba_generator.start()
        logger.info(
            f"  ✓ UEBA 持续日志生成已启动: users={','.join(usernames)}, mode={mode}, "
            f"logs_per_second={logs_per_second}"
        )

        interval = float(os.getenv("UEBA_MAIN_VALIDATION_INTERVAL_SEC", "15"))
        self.ueba_validation_stop_event = threading.Event()
        self.ueba_validation_thread = threading.Thread(
            target=self._ueba_validation_loop,
            args=(config.model_version, interval),
            name="ueba-main-validation-loop",
            daemon=True,
        )
        self.ueba_validation_thread.start()
        logger.info(f"  ✓ UEBA 周期 validation 已启动: interval={interval}s")

    def _ueba_validation_loop(self, model_version: str, interval: float):
        """后台周期评分持续生成的日志，让前端能持续读到新结果。"""
        stop_event = self.ueba_validation_stop_event
        if stop_event is None:
            return
        while not stop_event.is_set():
            try:
                self._run_ueba_recent_validation(
                    model_version,
                    run_id_prefix="main_continuous",
                )
            except Exception as e:
                logger.warning(f"  ⚠️  UEBA 周期 validation 失败: {e}", exc_info=True)
            stop_event.wait(interval)

    def _stop_ueba_background_tasks(self):
        """停止 UEBA 持续生成和周期 validation。"""
        if self.ueba_validation_stop_event is not None:
            self.ueba_validation_stop_event.set()
        if self.ueba_validation_thread is not None and self.ueba_validation_thread.is_alive():
            self.ueba_validation_thread.join(timeout=5)
        if self.ueba_generator is not None:
            try:
                self.ueba_generator.shutdown()
            except Exception as e:
                logger.warning(f"  ⚠️  UEBA 持续生成器停止失败: {e}")
        if self.ueba_writer is not None:
            try:
                self.ueba_writer.close()
            except Exception as e:
                logger.warning(f"  ⚠️  UEBA writer 关闭失败: {e}")
        elif self.ueba_writer_client is not None:
            try:
                self.ueba_writer_client.close()
            except Exception:
                pass
        self.ueba_generator = None
        self.ueba_writer = None
        self.ueba_writer_client = None

    def init_collectors(self):
        """初始化采集器模块"""
        logger.info("[2/4] 初始化采集器模块...")

        # 初始化 Filebeat 采集器
        try:
            filebeat_config = {
                'kafka_topic': settings.kafka_logs_topic,
                'bootstrap_servers': settings.kafka_bootstrap_servers,
                'group_id': 'filebeat_collector_main'
            }
            self.filebeat_collector = FilebeatCollector(config=filebeat_config)
            logger.info("✓ Filebeat 采集器初始化成功")
        except Exception as e:
            logger.error(f"✗ Filebeat 采集器初始化失败: {e}")

        # 初始化 Flume 采集器
        try:
            flume_config = {
                'host': settings.clickhouse_host,
                'port': 8123,
                'batch_size': 1000
            }
            self.flume_collector = FlumeCollector(config=flume_config)
            logger.info("✓ Flume 采集器初始化成功")
        except Exception as e:
            logger.error(f"✗ Flume 采集器初始化失败: {e}")

    def init_ai(self):
        """初始化 AI 分析模块"""
        logger.info("[3/4] 初始化 AI 分析模块...")
        try:
            config = settings.current_ai_config
            self.ai_analyzer = AIAnalyzer(
                api_key=config["api_key"],
                platform=config["platform"],
                model=config.get("model"),
                base_url=config.get("base_url"),
            )
            logger.info(f"✓ AI 分析器初始化成功: platform={config['platform']}, model={config.get('model')}")
        except Exception as e:
            logger.warning(f"⚠️  AI 分析器初始化失败: {e}")

    def _run_ai_reinforcement(self):
        """自动强化有 HIGH/CRITICAL 异常事件的用户基线。"""
        logger.info("[3.5/4] AI 基线强化...")
        try:
            ch = clickhouse_connect.get_client(
                host=settings.clickhouse_host, port=settings.clickhouse_port,
                username=settings.clickhouse_user, password=settings.clickhouse_password,
                database=settings.clickhouse_database,
            )
            # 检查是否有 validation 结果
            cnt = ch.query("SELECT count() FROM ueba_validation_results")
            if cnt.result_rows and cnt.result_rows[0][0] == 0:
                logger.info("  无 validation 结果，跳过 AI 强化")
                ch.close()
                return

            # 查有 HIGH/CRITICAL 的用户
            high_risk = ch.query("""
                SELECT DISTINCT username FROM ueba_validation_results
                WHERE ueba_risk_level IN ('HIGH','CRITICAL') AND username != ''
            """)
            users = [str(r[0]) for r in high_risk.result_rows if r[0]]
            if not users:
                logger.info("  无高风险用户，跳过 AI 强化")
                ch.close()
                return

            # 自动检测 model_version
            mv_row = ch.query("SELECT model_version FROM log_analysis.user_behavior_baselines ORDER BY created_at DESC LIMIT 1")
            model_version = str(mv_row.result_rows[0][0]) if mv_row.result_rows else "ueba_baseline_v1"
            logger.info(f"  检测到 model_version: {model_version}")

            config = settings.current_ai_config
            ai = ReinforcementAIClient(
                api_key=config["api_key"],
                platform=config["platform"],
                model=config.get("model"),
            )
            bs = ReinforcementBaselineStore(client=ch, database=settings.clickhouse_database)
            service = BaselineReinforcementService(
                clickhouse_client=ch, ai_client=ai, baseline_store=bs,
            )

            end = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            start = (datetime.now() - timedelta(days=30)).strftime("%Y-%m-%d %H:%M:%S")

            for user in users:
                try:
                    r = service.reinforce_user(
                        username=user,
                        model_version=model_version,
                        start_time=start,
                        end_time=end,
                    )
                    if r.get("success"):
                        logger.info(f"  ✓ {user}: 强化完成")
                    else:
                        logger.info(f"  - {user}: {r.get('reason')}")
                except Exception as e:
                    logger.warning(f"  ⚠️  {user}: 强化失败 - {e}")

            ch.close()
            logger.info(f"  ✓ AI 基线强化完成，处理 {len(users)} 个用户")
        except Exception as e:
            logger.warning(f"  ⚠️  AI 基线强化跳过: {e}")

    def start_dashboard(self):
        """启动 Streamlit Dashboard"""
        logger.info("[4/4] 启动 Streamlit Dashboard...")
        try:
            project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
            app_path = os.path.join(project_root, "src", "visualization", "dashboard.py")

            if not os.path.exists(app_path):
                logger.error(f"✗ Dashboard 文件不存在: {app_path}")
                return

            self.streamlit_process = subprocess.Popen([
                sys.executable, "-m", "streamlit", "run",
                str(app_path),
                "--server.port", str(settings.streamlit_server_port),
                "--server.address", settings.streamlit_server_address,
                "--browser.serverAddress", settings.streamlit_server_address
            ], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)

            logger.info(f"✓ Streamlit Dashboard 已启动: http://{settings.streamlit_server_address}:{settings.streamlit_server_port}")
        except Exception as e:
            logger.error(f"✗ Streamlit Dashboard 启动失败: {e}")

    async def run(self):
        """运行主服务"""
        logger.info("========================================")
        logger.info("  日志分析 AI 助手启动中...")
        logger.info("========================================")

        # 1. 初始化存储模块（含建表和测试数据）
        self.init_storage()

        # 2. 初始化采集器模块
        self.init_collectors()

        # 3. 初始化 AI 分析模块
        self.init_ai()

        # 3.5 AI 基线强化（自动强化有 HIGH/CRITICAL 事件的用户）
        self._run_ai_reinforcement()

        # 4. 启动 Streamlit Dashboard
        self.start_dashboard()

        logger.info("========================================")
        logger.info("  🚀 服务已启动")
        logger.info(f"  🌐 Dashboard: http://{settings.streamlit_server_address}:{settings.streamlit_server_port}")
        logger.info("========================================")

        # 保持运行
        try:
            while True:
                await asyncio.sleep(1)
        except KeyboardInterrupt:
            logger.info("========================================")
            logger.info("  🛑 系统关闭中...")
            logger.info("========================================")

            if self.streamlit_process:
                self.streamlit_process.terminate()
                self.streamlit_process.wait()
                logger.info("✓ Streamlit Dashboard 已停止")

            self._stop_ueba_background_tasks()

            if self.kafka_client:
                self.kafka_client.close()
            if self.clickhouse_client:
                self.clickhouse_client.close()

            logger.info("✓ 所有资源已释放")


async def main():
    """主函数"""
    service = LogAnalysisService()
    await service.run()


if __name__ == "__main__":
    asyncio.run(main())
