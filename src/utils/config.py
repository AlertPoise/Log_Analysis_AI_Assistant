"""
配置管理模块
TODO: 完善配置类定义

开发任务:
1. 定义所有必要的配置项
2. 实现配置验证
3. 支持配置热更新
"""
import os
from typing import Optional, Dict, Any
from pydantic_settings import BaseSettings
from pydantic import Field


class Settings(BaseSettings):
    """应用配置"""

    # ========== 新增：行为分析详细配置 ==========
    analysis_window_minutes: int = Field(default=5, env="ANALYSIS_WINDOW_MINUTES")
    analysis_interval_sec: int = Field(default=60, env="ANALYSIS_INTERVAL_SEC")

    # ========== 新增：日志生成速度控制（用于测试） ==========
    log_gen_interval_sec: int = Field(default=1, env="LOG_GEN_INTERVAL_SEC")
    logs_per_batch: int = Field(default=10, env="LOGS_PER_BATCH")

    # ========== 新增：临时目录路径 ==========
    test_tmp_dir: str = Field(default="./test_tmp", env="TEST_TMP_DIR")

    # ========== 新增：Filebeat 相关 ==========
    filebeat_data_dir: str = Field(default="./test_tmp/filebeat_data", env="FILEBEAT_DATA_DIR")
    filebeat_logs_dir: str = Field(default="./test_tmp/filebeat_logs", env="FILEBEAT_LOGS_DIR")
    filebeat_config_dir: str = Field(default="./test_tmp/filebeat_config", env="FILEBEAT_CONFIG_DIR")

    # AI API 配置 - Kimi (Moonshot AI)
    moonshot_api_key: Optional[str] = Field(default=None, env="MOONSHOT_API_KEY")
    moonshot_model: str = Field(default="kimi-k2.6", env="MOONSHOT_MODEL")
    moonshot_base_url: str = Field(default="https://api.moonshot.cn/v1", env="MOONSHOT_BASE_URL")

    # AI API 配置 - 硅基流动
    siliconflow_api_key: Optional[str] = Field(default=None, env="SILICONFLOW_API_KEY")
    siliconflow_model: str = Field(default="Qwen/Qwen2.5-7B-Instruct", env="SILICONFLOW_MODEL")
    siliconflow_base_url: str = Field(default="https://api.siliconflow.cn/v1", env="SILICONFLOW_BASE_URL")

    # AI API 配置 - 阿里云百炼
    dashscope_api_key: Optional[str] = Field(default=None, env="DASHSCOPE_API_KEY")
    dashscope_model: str = Field(default="qwen-turbo", env="DASHSCOPE_MODEL")
    dashscope_base_url: str = Field(default="https://dashscope.aliyuncs.com/compatible-mode/v1", env="DASHSCOPE_BASE_URL")

    # AI API 配置 - OpenAI
    openai_api_key: Optional[str] = Field(default=None, env="OPENAI_API_KEY")
    openai_api_base_url: str = Field(default="https://api.openai.com/v1", env="OPENAI_API_BASE_URL")
    openai_model: str = Field(default="gpt-3.5-turbo", env="OPENAI_MODEL")

    # AI API 配置 - 智谱AI (BigModel)
    zhipu_api_key: Optional[str] = Field(default=None, env="ZHIPU_API_KEY")
    zhipu_model: str = Field(default="glm-4-flash", env="ZHIPU_MODEL")
    zhipu_base_url: str = Field(default="https://open.bigmodel.cn/api/paas/v4/", env="ZHIPU_BASE_URL")

    # 当前 AI 平台选择
    ai_platform: str = Field(default="kimi", env="AI_PLATFORM")
    
    # Kafka 配置
    kafka_bootstrap_servers: str = Field(default="localhost:9092", env="KAFKA_BOOTSTRAP_SERVERS")
    kafka_logs_topic: str = Field(default="logs_raw", env="KAFKA_LOGS_TOPIC")
    kafka_analyzed_topic: str = Field(default="logs_analyzed", env="KAFKA_ANALYZED_TOPIC")
    kafka_consumer_group: str = Field(default="log_analysis_group", env="KAFKA_CONSUMER_GROUP")
    
    # ClickHouse 配置
    clickhouse_host: str = Field(default="localhost", env="CLICKHOUSE_HOST")
    clickhouse_port: int = Field(default=8123, env="CLICKHOUSE_PORT")
    clickhouse_database: str = Field(default="log_analysis", env="CLICKHOUSE_DATABASE")
    clickhouse_user: str = Field(default="default", env="CLICKHOUSE_USER")
    clickhouse_password: str = Field(default="", env="CLICKHOUSE_PASSWORD")
    clickhouse_table: str = Field(default="logs_structured", env="CLICKHOUSE_TABLE")
    
    # Elasticsearch 配置
    es_host: Optional[str] = Field(default=None, env="ES_HOST")
    es_port: int = Field(default=9200, env="ES_PORT")
    es_index: str = Field(default="logs-*", env="ES_INDEX")
    es_user: Optional[str] = Field(default=None, env="ES_USER")
    es_password: Optional[str] = Field(default=None, env="ES_PASSWORD")
    
    # 日志采集配置
    log_sources_config: str = Field(default="config/log_sources.yml", env="LOG_SOURCES_CONFIG")
    
    # 应用配置
    log_level: str = Field(default="INFO", env="LOG_LEVEL")
    log_output_dir: str = Field(default="logs", env="LOG_OUTPUT_DIR")
    report_output_dir: str = Field(default="reports", env="REPORT_OUTPUT_DIR")
    data_retention_days: int = Field(default=90, env="DATA_RETENTION_DAYS")
    
    # 日志采集配置
    log_sources_config: str = Field(default="config/log_sources.yml", env="LOG_SOURCES_CONFIG")
    
    # 行为分析配置
    behavior_time_window_hours: int = Field(default=24, env="BEHAVIOR_TIME_WINDOW_HOURS")
    anomaly_threshold: float = Field(default=0.7, env="ANOMALY_THRESHOLD")
    min_samples_for_profile: int = Field(default=10, env="MIN_SAMPLES_FOR_PROFILE")
    
    # Streamlit 配置
    streamlit_server_port: int = Field(default=8501, env="STREAMLIT_SERVER_PORT")
    streamlit_server_address: str = Field(default="localhost", env="STREAMLIT_SERVER_ADDRESS")
    
    @property
    def clickhouse_url(self) -> str:
        """获取 ClickHouse 连接 URL"""
        return f"{self.clickhouse_host}:{self.clickhouse_port}"
    
    @property
    def es_url(self) -> Optional[str]:
        """获取 Elasticsearch 连接 URL"""
        if self.es_host:
            return f"{self.es_host}:{self.es_port}"
        return None
    
    @property
    def use_kimi(self) -> bool:
        """是否使用 Kimi"""
        return self.moonshot_api_key is not None

    @property
    def use_siliconflow(self) -> bool:
        """是否使用硅基流动"""
        return self.siliconflow_api_key is not None

    @property
    def use_openai(self) -> bool:
        """是否使用 OpenAI"""
        return self.openai_api_key is not None

    @property
    def use_dashscope(self) -> bool:
        """是否使用 DashScope (阿里云)"""
        return self.dashscope_api_key is not None

    @property
    def use_zhipu(self) -> bool:
        """是否使用智谱AI"""
        return self.zhipu_api_key is not None

    @property
    def current_ai_config(self) -> Dict[str, Any]:
        """获取当前 AI 平台的配置"""
        platform = self.ai_platform.lower()
        if platform == "kimi" and self.moonshot_api_key:
            return {
                "api_key": self.moonshot_api_key,
                "platform": "kimi",
                "model": self.moonshot_model,
                "base_url": self.moonshot_base_url,
            }
        elif platform == "siliconflow" and self.siliconflow_api_key:
            return {
                "api_key": self.siliconflow_api_key,
                "platform": "siliconflow",
                "model": self.siliconflow_model,
                "base_url": self.siliconflow_base_url,
            }
        elif platform == "dashscope" and self.dashscope_api_key:
            return {
                "api_key": self.dashscope_api_key,
                "platform": "dashscope",
                "model": self.dashscope_model,
                "base_url": self.dashscope_base_url,
            }
        elif platform == "openai" and self.openai_api_key:
            return {
                "api_key": self.openai_api_key,
                "platform": "openai",
                "model": self.openai_model,
                "base_url": self.openai_api_base_url,
            }
        elif platform == "zhipu" and self.zhipu_api_key:
            return {
                "api_key": self.zhipu_api_key,
                "platform": "zhipu",
                "model": self.zhipu_model,
                "base_url": self.zhipu_base_url,
            }
        if self.moonshot_api_key:
            return {
                "api_key": self.moonshot_api_key,
                "platform": "kimi",
                "model": self.moonshot_model,
                "base_url": self.moonshot_base_url,
            }
        if self.siliconflow_api_key:
            return {
                "api_key": self.siliconflow_api_key,
                "platform": "siliconflow",
                "model": self.siliconflow_model,
                "base_url": self.siliconflow_base_url,
            }
        if self.dashscope_api_key:
            return {
                "api_key": self.dashscope_api_key,
                "platform": "dashscope",
                "model": self.dashscope_model,
                "base_url": self.dashscope_base_url,
            }
        if self.openai_api_key:
            return {
                "api_key": self.openai_api_key,
                "platform": "openai",
                "model": self.openai_model,
                "base_url": self.openai_api_base_url,
            }
        raise ValueError("No AI API key configured")

    class Config:
        env_file = (".env", "config/behavior.env")
        env_file_encoding = "utf-8"
        case_sensitive = False
        extra = "ignore"


# 全局配置实例
settings = Settings()


def get_settings() -> Settings:
    """获取配置实例"""
    return settings
