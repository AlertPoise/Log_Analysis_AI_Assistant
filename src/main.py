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
        except Exception as e:
            logger.warning(f"⚠️  ClickHouse 连接失败: {e}")
    
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
            # 获取项目根目录
            project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
            # 正确的 Dashboard 路径（参考 dashboard_continuous.py）
            app_path = os.path.join(project_root, "src", "visualization", "dashboard.py")
            
            # 检查文件是否存在
            if not os.path.exists(app_path):
                logger.error(f"✗ Dashboard 文件不存在: {app_path}")
                return
            
            # 启动 Streamlit 进程（使用 sys.executable 确保使用正确的 Python 解释器）
            import sys
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
        
        # 1. 初始化存储模块
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
            
            # 停止 Streamlit
            if self.streamlit_process:
                self.streamlit_process.terminate()
                self.streamlit_process.wait()
                logger.info("✓ Streamlit Dashboard 已停止")
            
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