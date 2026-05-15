"""
流式数据处理器
模拟 Flink 的流式处理模式，从数据源消费日志并进行清洗

开发任务:
1. 实现流式数据处理管道
2. 实现数据清洗和转换逻辑
3. 支持批量处理和容错
4. 通过接口与外部数据源交互
"""
import json
import time
from typing import Any, Callable, Dict, List, Optional
from datetime import datetime
from dataclasses import dataclass, field
from enum import Enum
import threading
from queue import Queue, Empty

from ..utils.logger import get_logger

logger = get_logger(__name__)


class ProcessingStatus(Enum):
    """处理状态"""
    PENDING = "pending"
    PROCESSING = "processing"
    SUCCESS = "success"
    FAILED = "failed"
    RETRY = "retry"


@dataclass
class StreamRecord:
    """流式数据记录"""
    key: Optional[str]
    value: Dict[str, Any]
    timestamp: datetime = field(default_factory=datetime.now)
    offset: Optional[int] = None
    partition: Optional[int] = None
    topic: Optional[str] = None
    processing_status: ProcessingStatus = ProcessingStatus.PENDING
    retry_count: int = 0


class DataCleaner:
    """
    数据清洗器
    负责清洗和转换日志数据
    """

    def __init__(self, config: Optional[Dict[str, Any]] = None):
        """
        初始化数据清洗器

        Args:
            config: 配置字典
        """
        self.config = config or {}
        self.cleaning_rules = self.config.get('cleaning_rules', [])
        self.field_mappings = self.config.get('field_mappings', {})
        self.filters = self.config.get('filters', [])

    def clean(self, record: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """
        清洗单条记录

        Args:
            record: 原始记录

        Returns:
            清洗后的记录，None 表示过滤掉
        """
        try:
            # 1. 应用过滤器
            if not self._apply_filters(record):
                return None

            # 2. 字段映射
            cleaned = self._apply_field_mappings(record)

            # 3. 数据清洗
            cleaned = self._clean_fields(cleaned)

            # 4. 应用自定义规则
            for rule in self.cleaning_rules:
                cleaned = self._apply_rule(cleaned, rule)

            return cleaned
        except Exception as e:
            logger.error(f"清洗记录失败：{e}")
            return None

    def _apply_filters(self, record: Dict[str, Any]) -> bool:
        """应用过滤器，返回 True 表示保留"""
        for filter_config in self.filters:
            field_name = filter_config.get('field')
            operator = filter_config.get('operator')
            value = filter_config.get('value')

            if field_name not in record:
                continue

            record_value = record[field_name]

            if operator == 'equals' and record_value != value:
                return False
            elif operator == 'not_equals' and record_value == value:
                return False
            elif operator == 'contains' and value not in str(record_value):
                return False
            elif operator == 'regex':
                import re
                if not re.match(value, str(record_value)):
                    return False

        return True

    def _apply_field_mappings(self, record: Dict[str, Any]) -> Dict[str, Any]:
        """应用字段映射"""
        result = {}
        for target_field, source_field in self.field_mappings.items():
            if source_field in record:
                result[target_field] = record[source_field]

        # 保留未映射的字段
        for key, value in record.items():
            if key not in self.field_mappings.values():
                result[key] = value

        return result

    def _clean_fields(self, record: Dict[str, Any]) -> Dict[str, Any]:
        """清洗字段值"""
        cleaned = {}

        for key, value in record.items():
            # 去除字符串空格
            if isinstance(value, str):
                value = value.strip()

            # 转换空字符串为 None
            if value == '':
                value = None

            # 转换数字字符串
            if isinstance(value, str) and value.isdigit():
                value = int(value)

            cleaned[key] = value

        return cleaned

    def _apply_rule(self, record: Dict[str, Any], rule: Dict[str, Any]) -> Dict[str, Any]:
        """应用自定义清洗规则"""
        rule_type = rule.get('type')

        if rule_type == 'default_value':
            field_name = rule.get('field')
            default = rule.get('default')
            if field_name not in record or record[field_name] is None:
                record[field_name] = default

        elif rule_type == 'transform':
            field_name = rule.get('field')
            transform_func = rule.get('function')
            if field_name in record and record[field_name] is not None:
                if transform_func == 'uppercase':
                    record[field_name] = str(record[field_name]).upper()
                elif transform_func == 'lowercase':
                    record[field_name] = str(record[field_name]).lower()
                elif transform_func == 'int':
                    try:
                        record[field_name] = int(record[field_name])
                    except (ValueError, TypeError):
                        pass
                elif transform_func == 'float':
                    try:
                        record[field_name] = float(record[field_name])
                    except (ValueError, TypeError):
                        pass

        elif rule_type == 'remove_field':
            field_name = rule.get('field')
            if field_name in record:
                del record[field_name]

        return record


class StreamProcessor:
    """
    流式处理器
    模拟 Flink 的流式处理模式，通过接口与外部数据源和输出系统交互
    """

    def __init__(
        self,
        parser_func: Callable[[Dict[str, Any]], Optional[Dict[str, Any]]],
        cleaner: Optional[DataCleaner] = None,
        sink_func: Optional[Callable[[Dict[str, Any]], None]] = None,
        batch_size: int = 100,
        batch_timeout: float = 5.0,
    ):
        """
        初始化流式处理器

        Args:
            parser_func: 解析函数
            cleaner: 数据清洗器
            sink_func: 输出函数（通过 DataSink 接口）
            batch_size: 批处理大小
            batch_timeout: 批处理超时（秒）
        """
        self.parser_func = parser_func
        self.cleaner = cleaner or DataCleaner()
        self.sink_func = sink_func
        self.batch_size = batch_size
        self.batch_timeout = batch_timeout

        # 处理管道
        self.processing_pipeline: List[Callable] = []

        # 批处理缓冲区
        self.batch_buffer: List[StreamRecord] = []
        self.buffer_lock = threading.Lock()

        # 控制标志
        self.running = False
        self.consumer = None

        # 统计信息
        self.stats = {
            'processed': 0,
            'success': 0,
            'failed': 0,
            'filtered': 0,
            'start_time': None,
        }

    def add_processing_step(self, func: Callable[[Dict[str, Any]], Dict[str, Any]]):
        """
        添加处理步骤到管道

        Args:
            func: 处理函数
        """
        self.processing_pipeline.append(func)
        logger.info(f"添加处理步骤：{func.__name__}")

    def process_record(self, record: StreamRecord) -> Optional[Dict[str, Any]]:
        """
        处理单条记录

        Args:
            record: 流式记录

        Returns:
            处理后的数据
        """
        try:
            record.processing_status = ProcessingStatus.PROCESSING

            # 1. 解析
            parsed = self.parser_func(record.value)
            if not parsed:
                logger.debug(f"解析失败：{record.value.get('raw_log', '')[:100]}")
                record.processing_status = ProcessingStatus.FAILED
                return None

            # 2. 清洗
            cleaned = self.cleaner.clean(parsed)
            if not cleaned:
                logger.debug(f"清洗过滤：{parsed.get('username', 'unknown')}")
                self.stats['filtered'] += 1
                return None

            # 3. 应用处理管道
            for step_func in self.processing_pipeline:
                try:
                    cleaned = step_func(cleaned)
                except Exception as e:
                    logger.error(f"处理步骤失败 [{step_func.__name__}]: {e}")

            # 4. 添加元数据
            cleaned['_processed_at'] = datetime.now().isoformat()
            cleaned['_topic'] = record.topic
            cleaned['_partition'] = record.partition
            cleaned['_offset'] = record.offset

            record.processing_status = ProcessingStatus.SUCCESS
            return cleaned

        except Exception as e:
            logger.error(f"处理记录失败：{e}")
            record.processing_status = ProcessingStatus.FAILED
            self.stats['failed'] += 1
            return None

    def flush_batch(self):
        """批量刷新缓冲区"""
        with self.buffer_lock:
            if not self.batch_buffer:
                return

            # 收集成功处理的记录
            batch_data = []
            for record in self.batch_buffer:
                if record.processing_status == ProcessingStatus.SUCCESS:
                    # 这里需要从 record 中获取处理后的数据
                    # 简化处理，实际应该在 process_record 时保存结果
                    batch_data.append(record)

            # 批量写入
            if batch_data and self.sink_func:
                try:
                    self.sink_func(batch_data)
                    logger.info(f"批量写入 {len(batch_data)} 条记录")
                except Exception as e:
                    logger.error(f"批量写入失败：{e}")

            # 清空缓冲区
            self.batch_buffer.clear()

    def start(self, topic: str, consumer_group: str):
        """
        启动流式处理（使用模拟数据源）

        Args:
            topic: 主题名称
            consumer_group: 消费者组
        """
        logger.info(f"启动流式处理：topic={topic}, group={consumer_group}")
        self.running = True
        self.stats['start_time'] = datetime.now()

        # 使用模拟消费者进行测试
        # 实际使用时，外部可以通过继承本类并覆写此方法来实现真实的数据源消费
        self._run_mock_consumer(topic)



    def _run_mock_consumer(self, topic: str):
        """模拟消费者（用于测试）"""
        logger.info("运行模拟消费者")

        # 模拟一些测试数据
        test_data = [
            {"raw_log": "2024-01-01 10:00:00 LOGIN user=admin ip=192.168.1.1 status=SUCCESS"},
            {"raw_log": "2024-01-01 10:01:00 LOGOUT user=admin ip=192.168.1.1 status=SUCCESS"},
        ]

        for i, data in enumerate(test_data):
            if not self.running:
                break

            record = StreamRecord(
                key=f"key_{i}",
                value=data,
                offset=i,
                partition=0,
                topic=topic,
            )

            result = self.process_record(record)
            if result:
                self.stats['processed'] += 1
                self.stats['success'] += 1
                logger.info(f"处理成功：{result}")

            time.sleep(0.1)

        logger.info("模拟消费者完成")

    def stop(self):
        """停止流式处理"""
        logger.info("停止流式处理")
        self.running = False

        # 刷新剩余数据
        self.flush_batch()

    def get_stats(self) -> Dict[str, Any]:
        """获取处理统计信息"""
        stats = self.stats.copy()
        if stats['start_time']:
            elapsed = (datetime.now() - stats['start_time']).total_seconds()
            stats['elapsed_seconds'] = elapsed
            if elapsed > 0:
                stats['throughput'] = stats['processed'] / elapsed
        return stats


def create_default_cleaner() -> DataCleaner:
    """创建默认的数据清洗器"""
    config = {
        'filters': [
            # 过滤掉测试数据
            {'field': 'username', 'operator': 'not_equals', 'value': 'test'},
        ],
        'field_mappings': {
            # 字段映射示例
            'user': 'username',
            'ip': 'source_ip',
        },
        'cleaning_rules': [
            # 设置默认值
            {'type': 'default_value', 'field': 'severity_level', 'default': 'INFO'},
            # 字段转换
            {'type': 'transform', 'field': 'username', 'function': 'lowercase'},
        ],
    }
    return DataCleaner(config)
