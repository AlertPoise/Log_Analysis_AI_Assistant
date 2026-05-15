"""
Flume 采集器（批量消费者）
与 FilebeatCollector 类似，但从不同 topic 或批量获取。
"""
import json
from typing import Any, Dict, Generator, Optional
from kafka import KafkaConsumer
from .base import BaseCollector
from ..utils.logger import get_logger

logger = get_logger(__name__)

class FlumeCollector(BaseCollector):
    """从 Kafka 消费 Flume 推送的日志，支持批量处理"""
    
    def __init__(self, name: str = "flume", config: Optional[Dict[str, Any]] = None):
        super().__init__(name, config or {})
        self.consumer = None
        self.topic = self.config.get('kafka_topic', 'logs_raw')  # 可与 Filebeat 共用 topic
        self.bootstrap_servers = self.config.get('bootstrap_servers', 'localhost:9092')
        self.group_id = self.config.get('group_id', 'flume_collector')
        self.batch_size = self.config.get('batch_size', 100)   # 批量拉取大小
        
    def start(self):
        try:
            self.consumer = KafkaConsumer(
                self.topic,
                bootstrap_servers=self.bootstrap_servers.split(','),
                group_id=self.group_id,
                auto_offset_reset='earliest',
                enable_auto_commit=True,
                max_poll_records=self.batch_size,
                value_deserializer=lambda m: json.loads(m.decode('utf-8'))
            )
            self.is_running = True
            logger.info(f"FlumeCollector [{self.name}] 启动，topic: {self.topic}")
        except Exception as e:
            logger.error(f"启动 Flume 消费者失败: {e}")
            raise
    
    def stop(self):
        self.is_running = False
        if self.consumer:
            self.consumer.close()
        logger.info(f"FlumeCollector [{self.name}] 已停止")
    
    def collect(self) -> Generator[Dict[str, Any], None, None]:
        if not self.consumer:
            raise RuntimeError("采集器未启动")
        
        for msg in self.consumer:
            if not self.is_running:
                break
            raw = msg.value
            log_record = {
                'timestamp': raw.get('timestamp', raw.get('@timestamp')),
                'log_type': raw.get('log_type', 'unknown'),
                'source': raw.get('source', 'flume'),
                'message': raw.get('message', ''),
                'offset': msg.offset,
            }
            # 合并额外字段
            for k, v in raw.items():
                if k not in log_record:
                    log_record[k] = v
            if self.validate_log(log_record):
                yield self.enrich_log(log_record)
            else:
                logger.debug(f"跳过无效日志: {log_record}")
    
    def process_batch(self, logs: list):
        """
        批量处理（可选），在这里可以实现批量入库等操作
        但本采集器的 collect 已支持逐条 yield，此方法保留供外部调用
        """
        if not logs:
            return
        logger.info(f"批量处理 {len(logs)} 条日志")
        # 示例：批量发送到下游（如写入 ClickHouse）
        # ...