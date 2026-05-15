"""
配置管理模块
TODO: 完善配置类定义

开发任务:
1. 定义所有必要的配置项
2. 实现配置验证
3. 支持配置热更新
"""
import os
from typing import Optional
from pydantic_settings import BaseSettings
from pydantic import Field


class Settings(BaseSettings):
    """应用配置"""

    # AI API 配置
    openai_api_key: Optional[str] = Field(default=None, env="OPENAI_API_KEY")
    openai_api_base_url: str = Field(default="https://api.openai.com/v1", env="OPENAI_API_BASE_URL")
    openai_model: str = Field(default="gpt-3.5-turbo", env="OPENAI_MODEL")

    dashscope_api_key: Optional[str] = Field(default=None, env="DASHSCOPE_API_KEY")
    dashscope_model: str = Field(default="qwen-turbo", env="DASHSCOPE_MODEL")

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
    def use_openai(self) -> bool:
        """是否使用 OpenAI"""
        return self.openai_api_key is not None

    @property
    def use_dashscope(self) -> bool:
        """是否使用 DashScope (阿里云)"""
        return self.dashscope_api_key is not None

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
