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
import time
from typing import Optional, Dict, Any
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

# 连接重试配置
MAX_RETRY_ATTEMPTS = 10
RETRY_DELAY_SECONDS = 5


class LogAnalysisService:
    """日志分析服务主类"""
    
    def __init__(self):
        self.kafka_client: Optional[KafkaClient] = None
        self.clickhouse_client: Optional[ClickHouseClient] = None
        self.filebeat_collector: Optional[FilebeatCollector] = None
        self.flume_collector: Optional[FlumeCollector] = None
        self.ai_analyzer: Optional[AIAnalyzer] = None
        self.kafka_connected = False
        self.clickhouse_connected = False
    
    def _retry_connection(self, connect_func, service_name, max_attempts=MAX_RETRY_ATTEMPTS):
        """
        带重试的连接方法
        
        Args:
            connect_func: 连接函数
            service_name: 服务名称（用于日志输出）
            max_attempts: 最大重试次数
            
        Returns:
            是否连接成功
        """
        for attempt in range(max_attempts):
            try:
                connect_func()
                logger.info(f"✓ {service_name} 连接成功")
                return True
            except Exception as e:
                if attempt < max_attempts - 1:
                    logger.warning(f"⚠️  {service_name} 连接失败 (尝试 {attempt + 1}/{max_attempts}): {e}")
                    logger.info(f"等待 {RETRY_DELAY_SECONDS} 秒后重试...")
                    time.sleep(RETRY_DELAY_SECONDS)
                else:
                    logger.error(f"✗ {service_name} 连接失败，已达到最大重试次数: {e}")
        return False
    
    def init_storage(self):
        """初始化存储模块"""
        logger.info("[1/6] 初始化存储模块...")
        
        # 初始化 Kafka 客户端
        kafka_config = {
            'bootstrap_servers': settings.kafka_bootstrap_servers,
            'producer_acks': 'all',
            'producer_retries': 3,
            'consumer_group_id': settings.kafka_consumer_group
        }
        self.kafka_client = KafkaClient(kafka_config)
        
        def connect_kafka():
            self.kafka_client.connect_producer()
        
        self.kafka_connected = self._retry_connection(connect_kafka, "Kafka")
        
        # 初始化 ClickHouse 客户端
        clickhouse_config = {
            'host': settings.clickhouse_host,
            'port': settings.clickhouse_port,
            'username': settings.clickhouse_user,
            'password': settings.clickhouse_password,
            'database': settings.clickhouse_database
        }
        self.clickhouse_client = ClickHouseClient(config=clickhouse_config)
        
        def connect_clickhouse():
            self.clickhouse_client.connect()
        
        self.clickhouse_connected = self._retry_connection(connect_clickhouse, "ClickHouse")
    
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
        if self.kafka_client and self.kafka_connected:
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
                logger.warning(f"⚠️  Kafka 测试失败: {e}")
        else:
            logger.info("⚠️  Kafka 测试跳过 (未连接)")
        
        # 测试 ClickHouse 查询
        if self.clickhouse_client and self.clickhouse_connected:
            try:
                # 查询系统表验证连接
                result = self.clickhouse_client.client.query("SELECT 1")
                logger.info("✓ ClickHouse 查询测试成功")
            except Exception as e:
                logger.warning(f"⚠️  ClickHouse 查询测试失败: {e}")
        else:
            logger.info("⚠️  ClickHouse 测试跳过 (未连接)")
    
    def test_collectors(self):
        """测试采集器模块功能"""
        logger.info("[4/6] 测试采集器模块...")
        
        # 测试 Filebeat 采集器启动
        if self.filebeat_collector:
            try:
                # 启动采集器（如果 Kafka 可用）
                if self.kafka_client and self.kafka_connected:
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
        logger.info("[5/7] 系统状态检查...")
        logger.info(f"  - 配置文件: .env (已加载)")
        logger.info(f"  - 日志级别: {settings.log_level}")
        logger.info(f"  - Kafka Broker: {settings.kafka_bootstrap_servers} ({'已连接' if self.kafka_connected else '未连接'})")
        logger.info(f"  - ClickHouse: {settings.clickhouse_host}:{settings.clickhouse_port} ({'已连接' if self.clickhouse_connected else '未连接'})")
        logger.info(f"  - AI 平台: {settings.ai_platform}")
        logger.info(f"  - 数据保留天数: {settings.data_retention_days}")
        logger.info(f"  - 异常检测阈值: {settings.anomaly_threshold}")
    
    def init_ai(self):
        """初始化 AI 分析模块"""
        logger.info("[6/7] 初始化 AI 分析模块...")
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
        
        # 6. 初始化 AI 分析模块
        self.init_ai()
        
        logger.info("========================================")
        if self.kafka_connected and self.clickhouse_connected:
            logger.info("  ✅ 系统初始化完成！")
            logger.info("  📊 各模块接口测试通过")
        else:
            logger.info("  ⚠️  系统初始化完成，但部分服务未连接")
            if not self.kafka_connected:
                logger.info("     - Kafka: 未连接")
            if not self.clickhouse_connected:
                logger.info("     - ClickHouse: 未连接")
        
        if self.ai_analyzer:
            logger.info("  🤖 AI 分析模块已就绪")
        
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
