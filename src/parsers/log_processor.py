"""
日志结构化处理主程序
整合日志解析、清洗、输出的完整流程

使用示例:
    python -m src.parsers.log_processor --config config/log_parser_config.yml
"""
import argparse
import json
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

# 添加项目根目录到路径
sys.path.insert(0, str(Path(__file__).parent.parent))

from src.parsers.parsers import RegexParser, COMMON_PATTERNS, LogparserParser, PREDEFINED_PATTERNS, JSONParser, StandardLogSchema
from src.parsers.stream_processor import StreamProcessor, DataCleaner, create_default_cleaner
from src.parsers.interfaces import DataSink, StreamConsumer
from src.utils.logger import get_logger

logger = get_logger(__name__)


class LogProcessor:
    """
    日志处理器
    整合解析、清洗、入库的完整流程
    """

    def __init__(
        self,
        config: Optional[Dict[str, Any]] = None,
        data_sink: Optional[DataSink] = None,
    ):
        """
        初始化日志处理器

        Args:
            config: 配置字典
            data_sink: 数据输出接口实现（可选）
        """
        self.config = config or {}

        # 初始化解析器
        self.parsers = {}
        self._init_parsers()

        # 初始化清洗器
        self.cleaner = create_default_cleaner()

        # 初始化数据输出接口
        self.data_sink = data_sink
        if self.data_sink:
            self._init_data_sink()

        # 统计信息
        self.stats = {
            'total': 0,
            'parsed': 0,
            'cleaned': 0,
            'output': 0,
            'failed': 0,
            'start_time': datetime.now(),
        }

    def _init_parsers(self):
        """初始化各类解析器"""
        # 正则解析器 - 加载常用模式
        regex_config = {
            'patterns': COMMON_PATTERNS,
        }
        self.parsers['regex'] = RegexParser(name='regex', config=regex_config)

        # Logparser 解析器 - 加载预定义模式
        self.parsers['logparser'] = LogparserParser(name='logparser')
        self.parsers['logparser'].load_patterns(PREDEFINED_PATTERNS)

        # JSON 解析器
        self.parsers['json'] = JSONParser(name='json')

        logger.info(f"初始化完成 {len(self.parsers)} 个解析器")

    def _init_data_sink(self):
        """初始化数据输出接口"""
        try:
            if self.data_sink.connect():
                logger.info("数据输出接口初始化成功")
            else:
                logger.warning("数据输出接口连接失败，将使用文件输出模式")
                self.data_sink = None
        except Exception as e:
            logger.warning(f"数据输出接口初始化失败：{e}，将使用文件输出模式")
            self.data_sink = None

    def parse_log(self, raw_log: str, log_type: Optional[str] = None) -> Optional[Dict[str, Any]]:
        """
        解析单条日志

        Args:
            raw_log: 原始日志字符串
            log_type: 指定日志类型

        Returns:
            解析后的标准日志
        """
        self.stats['total'] += 1

        # 尝试 JSON 格式
        if raw_log.strip().startswith('{'):
            parsed = self.parsers['json'].parse(raw_log)
            if parsed:
                self.stats['parsed'] += 1
                return parsed

        # 尝试 Logparser
        if log_type and log_type in self.parsers['logparser'].patterns:
            self.parsers['logparser'].set_active_pattern(log_type)
            parsed = self.parsers['logparser'].parse(raw_log)
            if parsed:
                self.stats['parsed'] += 1
                return parsed

        # 尝试所有正则模式
        for pattern_name, pattern in COMMON_PATTERNS.items():
            try:
                regex_parser = RegexParser(name=f'regex_{pattern_name}', config={
                    'pattern': pattern,
                    'log_type': pattern_name.split('_')[0] if '_' in pattern_name else 'unknown',
                })
                parsed = regex_parser.parse(raw_log)
                if parsed:
                    self.stats['parsed'] += 1
                    return parsed
            except Exception as e:
                logger.debug(f"模式 {pattern_name} 解析失败：{e}")

        # 所有解析都失败
        logger.debug(f"解析失败：{raw_log[:100]}")
        self.stats['failed'] += 1
        return None

    def clean_log(self, parsed_log: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """
        清洗日志数据

        Args:
            parsed_log: 解析后的日志

        Returns:
            清洗后的日志
        """
        if not parsed_log:
            return None

        cleaned = self.cleaner.clean(parsed_log)
        if cleaned:
            self.stats['cleaned'] += 1
        return cleaned

    def output_data(self, logs: List[Dict[str, Any]], table: Optional[str] = None):
        """
        输出日志数据（通过 DataSink 接口）

        Args:
            logs: 日志列表
            table: 目标表名或集合名（可选）
        """
        if not self.data_sink:
            logger.warning("数据输出接口未初始化，保存到文件")
            self._save_to_file(logs, f'logs_output_{datetime.now().strftime("%Y%m%d_%H%M%S")}.json')
            return

        try:
            # 转换 datetime 为字符串
            formatted_logs = []
            for log in logs:
                formatted_log = log.copy()
                for key, value in formatted_log.items():
                    if isinstance(value, datetime):
                        formatted_log[key] = value.isoformat()
                formatted_logs.append(formatted_log)

            if self.data_sink.insert(formatted_logs, table):
                self.stats['output'] += len(formatted_logs)
                logger.info(f"成功输出 {len(formatted_logs)} 条数据")
            else:
                logger.error("数据输出失败")
                self._save_to_file(logs, f'logs_failed_{datetime.now().strftime("%Y%m%d_%H%M%S")}.json')

        except Exception as e:
            logger.error(f"数据输出失败：{e}")
            self._save_to_file(logs, f'logs_failed_{datetime.now().strftime("%Y%m%d_%H%M%S")}.json')

    def _save_to_file(self, logs: List[Dict[str, Any]], filename: str):
        """保存日志到文件（备用方案）"""
        output_dir = Path('logs_output')
        output_dir.mkdir(exist_ok=True)

        filepath = output_dir / filename
        with open(filepath, 'w', encoding='utf-8') as f:
            json.dump(logs, f, ensure_ascii=False, indent=2, default=str)

        logger.info(f"日志已保存到：{filepath}")

    def process_file(self, input_file: str, batch_size: int = 1000):
        """
        处理日志文件

        Args:
            input_file: 输入文件路径
            batch_size: 批处理大小
        """
        logger.info(f"开始处理文件：{input_file}")

        batch = []
        line_count = 0

        with open(input_file, 'r', encoding='utf-8', errors='ignore') as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue

                line_count += 1

                # 解析
                parsed = self.parse_log(line)
                if not parsed:
                    continue

                # 清洗
                cleaned = self.clean_log(parsed)
                if not cleaned:
                    continue

                batch.append(cleaned)

                # 批量输出
                if len(batch) >= batch_size:
                    self.output_data(batch)
                    batch = []

                    # 输出进度
                    if line_count % 10000 == 0:
                        logger.info(f"已处理 {line_count} 行日志")

        # 处理剩余数据
        if batch:
            self.output_data(batch)

        # 输出统计
        self._print_stats(line_count)

    def process_stream(
        self,
        stream_consumer: StreamConsumer,
        topic: str,
        consumer_group: str,
    ):
        """
        从流式数据源处理（通过 StreamConsumer 接口）

        Args:
            stream_consumer: 流式消费者接口实现
            topic: 主题
            consumer_group: 消费者组
        """
        logger.info("启动流式处理")

        def parser_func(data: Dict[str, Any]) -> Optional[Dict[str, Any]]:
            raw_log = data.get('raw_log') or data.get('message') or data.get('log')
            if not raw_log:
                return None
            return self.parse_log(raw_log)

        def sink_func(batch_data):
            logs = [record.value for record in batch_data if record.value]
            if logs:
                self.output_data(logs)

        # 创建流式处理器
        processor = StreamProcessor(
            parser_func=parser_func,
            cleaner=self.cleaner,
            sink_func=sink_func,
            batch_size=self.config.get('batch_size', 1000),
            batch_timeout=self.config.get('batch_timeout', 5.0),
        )

        try:
            processor.start(topic, consumer_group)
        except KeyboardInterrupt:
            logger.info("接收到停止信号")
        finally:
            processor.stop()
            self._print_stats()

    def _print_stats(self, total_lines: Optional[int] = None):
        """输出统计信息"""
        elapsed = (datetime.now() - self.stats['start_time']).total_seconds()

        stats_msg = """
===== 处理统计 =====
总行数：{}
解析成功：{}
清洗成功：{}
输出成功：{}
失败：{}
耗时：{:.2f} 秒
处理速度：{:.2f} 行/秒
==================="""

        total = total_lines or self.stats['total']
        speed = total / elapsed if elapsed > 0 else 0

        logger.info(stats_msg.format(
            total,
            self.stats['parsed'],
            self.stats['cleaned'],
            self.stats['output'],
            self.stats['failed'],
            elapsed,
            speed,
        ))


def load_config(config_file: str) -> Dict[str, Any]:
    """加载配置文件"""
    import yaml  # type: ignore  # noqa: F401

    with open(config_file, 'r', encoding='utf-8') as f:
        return yaml.safe_load(f)


def main():
    """主函数"""
    parser = argparse.ArgumentParser(description='日志结构化处理工具')
    parser.add_argument('--config', type=str, help='配置文件路径')
    parser.add_argument('--input', type=str, help='输入日志文件')
    parser.add_argument('--batch-size', type=int, default=1000, help='批处理大小')

    args = parser.parse_args()

    # 加载配置
    config = {}
    if args.config:
        config = load_config(args.config)
        logger.info(f"加载配置文件：{args.config}")

    # 创建处理器（不传入 data_sink 则使用文件输出）
    processor = LogProcessor(config)

    # 处理模式
    if args.input:
        # 文件处理模式
        processor.process_file(args.input, args.batch_size)
    else:
        # 交互模式
        print("日志结构化处理工具 - 交互模式")
        print("输入日志内容进行解析，输入 'quit' 退出")
        print("-" * 50)

        while True:
            try:
                line = input("> ").strip()
                if line.lower() == 'quit':
                    break

                parsed = processor.parse_log(line)
                if parsed:
                    cleaned = processor.clean_log(parsed)
                    print(json.dumps(cleaned, ensure_ascii=False, indent=2, default=str))
                else:
                    print("解析失败")

            except EOFError:
                break
            except KeyboardInterrupt:
                break

        processor._print_stats()


if __name__ == '__main__':
    main()
