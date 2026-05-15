"""
Filebeat 采集器（消费者）
从 Kafka 中拉取 Filebeat 推送的日志，并逐条 yield。
"""
import json
from typing import Any, Dict, Generator, Optional
from datetime import datetime
from kafka import KafkaConsumer
from .base import BaseCollector
from ..utils.logger import get_logger

logger = get_logger(__name__)

class FilebeatCollector(BaseCollector):
    """从 Kafka 消费 Filebeat 采集的日志"""

    def __init__(self, name: str = "filebeat", config: Optional[Dict[str, Any]] = None):
        super().__init__(name, config or {})
        self.consumer = None
        self.topic = self.config.get('kafka_topic', 'logs_raw')
        self.bootstrap_servers = self.config.get('bootstrap_servers', 'localhost:9092')
        self.group_id = self.config.get('group_id', 'filebeat_collector')

    def start(self):
        """初始化 Kafka 消费者，支持增量消费（自动提交 offset）"""
        try:
            self.consumer = KafkaConsumer(
                self.topic,
                bootstrap_servers=self.bootstrap_servers.split(','),
                group_id=self.group_id,
                auto_offset_reset='earliest',
                enable_auto_commit=True,
                auto_commit_interval_ms=5000,
                value_deserializer=lambda m: m.decode('utf-8'),   # 先解码为字符串
                key_deserializer=lambda m: m.decode('utf-8') if m else None
            )
            self.is_running = True
            logger.info(f"FilebeatCollector [{self.name}] 启动，订阅 topic: {self.topic}")
        except Exception as e:
            logger.error(f"启动 Kafka 消费者失败: {e}")
            raise

    def stop(self):
        """停止采集器，关闭消费者"""
        self.is_running = False
        if self.consumer:
            self.consumer.close()
        logger.info(f"FilebeatCollector [{self.name}] 已停止")

    def collect(self) -> Generator[Dict[str, Any], None, None]:
        """
        持续消费 Kafka 消息，每条消息转换为标准日志格式后 yield
        """
        if not self.consumer:
            raise RuntimeError("采集器未启动，请先调用 start()")

        logger.info("开始从 Kafka 拉取日志...")
        for msg in self.consumer:
            if not self.is_running:
                break
            raw_value = msg.value
            # 尝试解析为 JSON，失败则作为纯文本处理
            try:
                # 如果已经是字典，直接使用；否则尝试解析 JSON
                if isinstance(raw_value, dict):
                    parsed = raw_value
                else:
                    parsed = json.loads(raw_value)
            except (json.JSONDecodeError, TypeError):
                # 纯文本日志，构造一个简单的日志字典
                parsed = {
                    'message': raw_value,
                    'timestamp': datetime.now().isoformat(),
                    'log_type': 'unknown',
                    'source': 'filebeat'
                }
            # 统一提取字段
            log_record = {
                'timestamp': parsed.get('@timestamp', parsed.get('timestamp')),
                'log_type': parsed.get('fields', {}).get('log_type') or parsed.get('log_type', 'unknown'),
                'source': parsed.get('log', {}).get('file', {}).get('path') or parsed.get('source', 'filebeat'),
                'message': parsed.get('message', ''),
                'host': parsed.get('host', {}).get('name', 'unknown'),
                'offset': msg.offset,
                'partition': msg.partition
            }
            # 补充可能存在的字段（如 user, ip 等，供后续解析模块使用）
            for extra_field in ['user', 'src_ip', 'action', 'status', 'method', 'endpoint']:
                if extra_field in parsed:
                    log_record[extra_field] = parsed[extra_field]

            # 验证并丰富
            if self.validate_log(log_record):
                yield self.enrich_log(log_record)
            else:
                logger.warning(f"日志格式无效，已丢弃: {log_record}")