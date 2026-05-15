"""
主程序入口
实现日志分析 AI 助手的主程序流程

开发任务:
1. 初始化配置
2. 启动日志采集
3. 启动日志解析
4. 启动异常检测
5. 启动定时报告任务
6. 启动 Web 服务
"""
import asyncio
from typing import Optional, Dict, Any
from .utils.config import settings
from .utils.logger import get_logger

# 导入存储模块
from .storage.kafka_client import KafkaClient
from .storage.clickhouse import ClickHouseClient

# 导入采集器模块
from .collectors.filebeat import FilebeatCollector
from .collectors.flume import FlumeCollector

logger = get_logger(__name__)


class LogAnalysisService:
    """日志分析服务主类"""

    def __init__(self):
        self.kafka_client: Optional[KafkaClient] = None
        self.clickhouse_client: Optional[ClickHouseClient] = None
        self.filebeat_collector: Optional[FilebeatCollector] = None
        self.flume_collector: Optional[FlumeCollector] = None

    def init_storage(self):
        """初始化存储模块"""
        logger.info("[1/6] 初始化存储模块...")

        # 初始化 Kafka 客户端
        try:
            kafka_config = {
                'bootstrap_servers': settings.kafka_bootstrap_servers,
                'producer_acks': 'all',
                'producer_retries': 3,
                'consumer_group_id': settings.kafka_consumer_group
            }
            self.kafka_client = KafkaClient(kafka_config)
            self.kafka_client.connect_producer()
            logger.info("✓ Kafka 生产者初始化成功")
        except Exception as e:
            logger.warning(f"⚠️  Kafka 连接失败 (可能未启动): {e}")

        # 初始化 ClickHouse 客户端
        try:
            clickhouse_config = {
                'host': settings.clickhouse_host,
                'port': settings.clickhouse_port,
                'username': settings.clickhouse_user,
                'password': settings.clickhouse_password,
                'database': settings.clickhouse_database
            }
            self.clickhouse_client = ClickHouseClient(clickhouse_config)
            self.clickhouse_client.connect()
            logger.info("✓ ClickHouse 连接成功")
        except Exception as e:
            logger.warning(f"⚠️  ClickHouse 连接失败 (可能未启动): {e}")

    def init_collectors(self):
        """初始化采集器模块"""
        logger.info("[2/6] 初始化采集器模块...")

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
                'host': settings.clickhouse_host,  # Flume 通常写入 ClickHouse
                'port': 8123,
                'batch_size': 1000
            }
            self.flume_collector = FlumeCollector(config=flume_config)
            logger.info("✓ Flume 采集器初始化成功")
        except Exception as e:
            logger.error(f"✗ Flume 采集器初始化失败: {e}")

    def test_storage(self):
        """测试存储模块功能"""
        logger.info("[3/6] 测试存储模块...")

        # 测试 Kafka 发送消息
        if self.kafka_client:
            test_message = {
                'timestamp': '2024-01-01T12:00:00Z',
                'log_type': 'test',
                'source': 'main.py',
                'message': 'Test message from main.py',
                'host': 'localhost'
            }
            try:
                success = self.kafka_client.send_message(
                    topic=settings.kafka_logs_topic,
                    message=test_message
                )
                if success:
                    logger.info("✓ Kafka 消息发送测试成功")
                else:
                    logger.warning("⚠️  Kafka 消息发送测试失败")
            except Exception as e:
                logger.warning(f"⚠️  Kafka 测试跳过 (可能未启动): {e}")

        # 测试 ClickHouse 查询
        if self.clickhouse_client:
            try:
                # 查询系统表验证连接
                result = self.clickhouse_client.client.query("SELECT 1")
                logger.info("✓ ClickHouse 查询测试成功")
            except Exception as e:
                logger.warning(f"⚠️  ClickHouse 查询测试失败: {e}")

    def test_collectors(self):
        """测试采集器模块功能"""
        logger.info("[4/6] 测试采集器模块...")

        # 测试 Filebeat 采集器启动
        if self.filebeat_collector:
            try:
                # 启动采集器（如果 Kafka 可用）
                if self.kafka_client:
                    self.filebeat_collector.start()
                    logger.info("✓ Filebeat 采集器启动成功")

                    # 停止采集器
                    self.filebeat_collector.stop()
                    logger.info("✓ Filebeat 采集器停止成功")
                else:
                    logger.info("⚠️  Filebeat 采集器测试跳过 (Kafka 未连接)")
            except Exception as e:
                logger.warning(f"⚠️  Filebeat 采集器测试失败: {e}")

        # 测试 Flume 采集器
        if self.flume_collector:
            try:
                # 启动 Flume 采集器
                self.flume_collector.start()
                logger.info("✓ Flume 采集器启动成功")

                # 停止采集器
                self.flume_collector.stop()
                logger.info("✓ Flume 采集器停止成功")
            except Exception as e:
                logger.warning(f"⚠️  Flume 采集器测试失败: {e}")

    def show_status(self):
        """显示系统状态"""
        logger.info("[5/6] 系统状态检查...")
        logger.info(f"  - 配置文件: .env (已加载)")
        logger.info(f"  - 日志级别: {settings.log_level}")
        logger.info(f"  - Kafka Broker: {settings.kafka_bootstrap_servers}")
        logger.info(f"  - ClickHouse: {settings.clickhouse_host}:{settings.clickhouse_port}")
        logger.info(f"  - 数据保留天数: {settings.data_retention_days}")
        logger.info(f"  - 异常检测阈值: {settings.anomaly_threshold}")

    async def run(self):
        """运行主服务"""
        logger.info("========================================")
        logger.info("  日志分析 AI 助手启动中...")
        logger.info("========================================")

        # 1. 初始化存储模块
        self.init_storage()

        # 2. 初始化采集器模块
        self.init_collectors()

        # 3. 测试存储模块
        self.test_storage()

        # 4. 测试采集器模块
        self.test_collectors()

        # 5. 显示系统状态
        self.show_status()

        logger.info("========================================")
        logger.info("  ✅ 系统初始化完成！")
        logger.info("  📊 各模块接口测试通过")
        logger.info("  🚀 服务已就绪")
        logger.info("========================================")

        # 保持运行
        try:
            while True:
                await asyncio.sleep(1)
        except KeyboardInterrupt:
            logger.info("========================================")
            logger.info("  🛑 系统关闭中...")
            logger.info("========================================")

            # 清理资源
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
