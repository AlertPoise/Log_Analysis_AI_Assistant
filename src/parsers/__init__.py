"""
日志解析模块

提供日志解析、清洗、输出的完整流程
通过接口与外部存储系统交互

"""
from .interfaces import DataSink, DataSource, StreamConsumer, StreamProducer
from .parsers import (
    BaseParser,
    JSONParser,
    RegexParser,
    LogparserParser,
    StandardLogSchema,
    FieldExtractor,
    LogSeverity,
    LogType,
    ActionType,
    COMMON_PATTERNS,
    PREDEFINED_PATTERNS,
    create_default_logparser,
    create_parser,
)
from .stream_processor import StreamProcessor, DataCleaner, create_default_cleaner
from .log_processor import LogProcessor, load_config

__all__ = [
    # 接口定义
    'DataSink',
    'DataSource',
    'StreamConsumer',
    'StreamProducer',

    # 解析器
    'BaseParser',
    'JSONParser',
    'RegexParser',
    'LogparserParser',

    # Schema
    'StandardLogSchema',
    'FieldExtractor',
    'LogSeverity',
    'LogType',
    'ActionType',

    # 预定义模式
    'COMMON_PATTERNS',
    'PREDEFINED_PATTERNS',

    # 解析器工厂
    'create_default_logparser',
    'create_parser',

    # 流式处理
    'StreamProcessor',
    'DataCleaner',
    'create_default_cleaner',

    # 主处理器
    'LogProcessor',
    'load_config',
]
