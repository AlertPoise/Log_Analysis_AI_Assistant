"""
Kafka 客户端

Kafka 生产者和消费者的统一封装。
"""

import json
import time
from typing import Any, Callable, Dict, List, Optional

from kafka import KafkaConsumer, KafkaProducer
from kafka.errors import KafkaError, NoBrokersAvailable

from ..utils.logger import get_logger

logger = get_logger(__name__)


class KafkaClient:
    """Kafka 客户端，封装生产者和消费者操作"""

    def __init__(self, config: Dict[str, Any]):
        """
        初始化 Kafka 客户端
        Args:
            config: 配置字典，包含：
                - bootstrap_servers: Kafka broker 地址，多个用逗号分隔
                - (可选)security_protocol, sasl_mechanism, sasl_plain_username, sasl_plain_password, producer_acks, producer_retries, consumer_group_id, consumer_auto_offset_reset
        """
        self.config = config
        self.bootstrap_servers = config.get('bootstrap_servers', 'localhost:9092').split(',')
        self.producer: Optional[KafkaProducer] = None
        self.consumer: Optional[KafkaConsumer] = None
        self._connected = False

    def connect_producer(self) -> None:
        """初始化 Kafka 生产者"""
        try:
            producer_config = {
                'bootstrap_servers': self.bootstrap_servers,
                'value_serializer': lambda v: json.dumps(v).encode('utf-8'),
                'key_serializer': lambda k: k.encode('utf-8') if k else None,
                'acks': self.config.get('producer_acks', 'all'),
                'retries': self.config.get('producer_retries', 3),
                'max_in_flight_requests_per_connection': 1,  # 保证顺序
            }
            # 安全配置（可选）
            if 'security_protocol' in self.config:
                producer_config['security_protocol'] = self.config['security_protocol']
                if self.config['security_protocol'] in ('SASL_PLAINTEXT', 'SASL_SSL'):
                    producer_config['sasl_mechanism'] = self.config.get('sasl_mechanism', 'PLAIN')
                    producer_config['sasl_plain_username'] = self.config.get('sasl_plain_username')
                    producer_config['sasl_plain_password'] = self.config.get('sasl_plain_password')

            self.producer = KafkaProducer(**producer_config)
            logger.info(f"Kafka 生产者已连接: {self.bootstrap_servers}")
        except NoBrokersAvailable as e:
            logger.error(f"无可用 Kafka broker: {e}")
            raise
        except Exception as e:
            logger.error(f"初始化 Kafka 生产者失败: {e}")
            raise

    def connect_consumer(self, topics: List[str], group_id: Optional[str] = None) -> None:
        """
        初始化 Kafka 消费者
        Args:
            topics: 订阅的主题列表
            group_id: 消费者组 ID，不提供则使用配置中的默认值
        """
        try:
            consumer_config = {
                'bootstrap_servers': self.bootstrap_servers,
                'group_id': group_id or self.config.get('consumer_group_id', 'default_group'),
                'auto_offset_reset': self.config.get('consumer_auto_offset_reset', 'earliest'),
                'enable_auto_commit': self.config.get('consumer_enable_auto_commit', True),
                'auto_commit_interval_ms': self.config.get('consumer_auto_commit_interval_ms', 5000),
                'value_deserializer': lambda m: json.loads(m.decode('utf-8')),
                'key_deserializer': lambda m: m.decode('utf-8') if m else None,
            }
            # 安全配置（可选）
            if 'security_protocol' in self.config:
                consumer_config['security_protocol'] = self.config['security_protocol']
                if self.config['security_protocol'] in ('SASL_PLAINTEXT', 'SASL_SSL'):
                    consumer_config['sasl_mechanism'] = self.config.get('sasl_mechanism', 'PLAIN')
                    consumer_config['sasl_plain_username'] = self.config.get('sasl_plain_username')
                    consumer_config['sasl_plain_password'] = self.config.get('sasl_plain_password')

            self.consumer = KafkaConsumer(*topics, **consumer_config)
            logger.info(f"Kafka 消费者已连接，订阅主题: {topics}，组 ID: {consumer_config['group_id']}")
        except NoBrokersAvailable as e:
            logger.error(f"无可用 Kafka broker: {e}")
            raise
        except Exception as e:
            logger.error(f"初始化 Kafka 消费者失败: {e}")
            raise

    def send_message(self, topic: str, message: Dict[str, Any], key: Optional[str] = None,
                     retries: int = 3) -> bool:
        """
        发送单条消息到 Kafka
        Args:
            topic: 目标主题
            message: 消息字典
            key: 消息键（用于分区）
            retries: 重试次数
        Returns:
            是否发送成功
        """
        if not self.producer:
            raise RuntimeError("生产者未初始化，请先调用 connect_producer()")

        for attempt in range(retries):
            try:
                future = self.producer.send(topic, value=message, key=key)
                # 同步等待结果
                record_metadata = future.get(timeout=10)
                logger.debug(f"消息发送成功: topic={record_metadata.topic}, "
                             f"partition={record_metadata.partition}, offset={record_metadata.offset}")
                return True
            except KafkaError as e:
                logger.warning(f"发送消息失败 (尝试 {attempt + 1}/{retries}): {e}")
                if attempt == retries - 1:
                    logger.error(f"发送消息最终失败: {e}")
                    return False
                time.sleep(1)
            except Exception as e:
                logger.error(f"发送消息时发生未知错误: {e}")
                return False
        return False

    def send_batch(self, topic: str, messages: List[Dict[str, Any]],
                   keys: Optional[List[str]] = None) -> int:
        """
        批量发送消息
        Args:
            topic: 目标主题
            messages: 消息字典列表
            keys: 对应的键列表，长度应与 messages 相同
        Returns:
            成功发送的消息数量
        """
        if not self.producer:
            raise RuntimeError("生产者未初始化，请先调用 connect_producer()")

        if not messages:
            return 0

        success_count = 0
        futures = []
        for i, msg in enumerate(messages):
            key = keys[i] if keys and i < len(keys) else None
            try:
                future = self.producer.send(topic, value=msg, key=key)
                futures.append(future)
            except Exception as e:
                logger.warning(f"准备发送消息时出错: {e}")

        # 等待所有发送完成并统计成功数
        for future in futures:
            try:
                future.get(timeout=10)
                success_count += 1
            except Exception as e:
                logger.warning(f"批量发送中一条消息失败: {e}")

        logger.info(f"批量发送完成: {success_count}/{len(messages)} 条成功")
        return success_count

    def consume(self, topics: Optional[List[str]] = None, callback: Optional[Callable] = None,
                max_messages: Optional[int] = None, timeout_ms: int = 1000) -> List[Dict[str, Any]]:
        """
        消费消息
        Args:
            topics: 要订阅的主题列表，若已初始化消费者则忽略
            callback: 每条消息的回调函数，接收 (message_dict, raw_message) 作为参数
            max_messages: 最大消费消息数，None 表示无限
            timeout_ms: poll 超时时间
        Returns:
            如果未提供回调，返回消费到的消息列表；否则返回空列表
        """
        if not self.consumer:
            if not topics:
                raise ValueError("未初始化消费者且未提供 topics")
            self.connect_consumer(topics)

        if topics and self.consumer:
            self.consumer.subscribe(topics)

        messages = []
        count = 0
        try:
            for msg in self.consumer:
                if msg.value is None:
                    continue
                if callback:
                    callback(msg.value, msg)
                else:
                    messages.append(msg.value)

                count += 1
                if max_messages and count >= max_messages:
                    break
        except Exception as e:
            logger.error(f"消费消息时出错: {e}")
            raise

        logger.debug(f"本次消费了 {count} 条消息")
        return messages

    def commit_offsets(self) -> None:
        """手动提交消费者偏移量"""
        if self.consumer:
            self.consumer.commit()
            logger.debug("消费者偏移量已手动提交")

    def close(self) -> None:
        """关闭生产者和消费者连接"""
        if self.producer:
            self.producer.flush()
            self.producer.close()
            logger.info("Kafka 生产者已关闭")
        if self.consumer:
            self.consumer.close()
            logger.info("Kafka 消费者已关闭")
        self._connected = False