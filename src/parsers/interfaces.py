"""
数据输出接口定义
定义 parsers 模块与外部存储/消息系统的交互接口
"""
from abc import ABC, abstractmethod
from typing import Any, Dict, List, Optional
from datetime import datetime


class DataSink(ABC):
    """
    数据输出接口
    用于将处理后的日志数据输出到不同的存储系统
    """

    @abstractmethod
    def connect(self) -> bool:
        """
        连接到存储系统

        Returns:
            连接是否成功
        """
        pass

    @abstractmethod
    def insert(self, data: List[Dict[str, Any]], table: Optional[str] = None) -> bool:
        """
        批量插入数据

        Args:
            data: 数据列表
            table: 目标表名或集合名

        Returns:
            插入是否成功
        """
        pass

    @abstractmethod
    def close(self):
        """关闭连接"""
        pass


class DataSource(ABC):
    """
    数据源接口
    用于从不同的数据源读取原始日志数据
    """

    @abstractmethod
    def connect(self) -> bool:
        """
        连接到数据源

        Returns:
            连接是否成功
        """
        pass

    @abstractmethod
    def read_batch(self, batch_size: int = 100) -> List[Dict[str, Any]]:
        """
        批量读取数据

        Args:
            batch_size: 批次大小

        Returns:
            数据列表
        """
        pass

    @abstractmethod
    def close(self):
        """关闭连接"""
        pass


class StreamConsumer(ABC):
    """
    流式数据消费者接口
    用于从 Kafka 等消息队列消费数据
    """

    @abstractmethod
    def start(self, topic: str, consumer_group: str):
        """
        启动消费者

        Args:
            topic: 主题
            consumer_group: 消费者组
        """
        pass

    @abstractmethod
    def stop(self):
        """停止消费者"""
        pass

    @abstractmethod
    def poll(self, timeout: float = 1.0) -> Optional[Dict[str, Any]]:
        """
        轮询消息

        Args:
            timeout: 超时时间（秒）

        Returns:
            消息数据，无消息返回 None
        """
        pass


class StreamProducer(ABC):
    """
    流式数据生产者接口
    用于向 Kafka 等消息队列发送数据
    """

    @abstractmethod
    def connect(self) -> bool:
        """
        连接到消息队列

        Returns:
            连接是否成功
        """
        pass

    @abstractmethod
    def send(self, topic: str, message: Dict[str, Any], key: Optional[str] = None):
        """
        发送消息

        Args:
            topic: 主题
            message: 消息内容
            key: 消息键
        """
        pass

    @abstractmethod
    def flush(self):
        """刷新缓冲区"""
        pass

    @abstractmethod
    def close(self):
        """关闭连接"""
        pass
