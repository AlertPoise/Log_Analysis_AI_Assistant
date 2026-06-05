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
from typing import Optional, Dict, Any
from datetime import datetime
from dataclasses import asdict
from .utils.config import settings
from .utils.logger import get_logger

# 导入存储模块
from .storage.kafka_client import KafkaClient
from .storage.clickhouse import ClickHouseClient

# 导入采集器模块
from .collectors.filebeat import FilebeatCollector
from .collectors.flume import FlumeCollector

# 导入 AI 模块
from .ai.analyzer import AIAnalyzer

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
                    stmt_upper = stmt.upper().strip()
                    if any(stmt_upper.startswith(kw) for kw in (
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
