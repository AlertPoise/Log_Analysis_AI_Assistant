"""
测试采集器
TODO: 添加单元测试

开发任务:
1. 测试采集器基类
2. 测试 Filebeat 采集器
3. 测试 Flume 采集器
"""

import sys
from pathlib import Path

# 添加项目根目录到 sys.path，确保可以导入 src 模块
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

import pytest
from unittest.mock import MagicMock, patch
from src.collectors.filebeat import FilebeatCollector
from src.collectors.flume import FlumeCollector


class TestBaseCollector:
    """测试基类功能（验证、丰富）"""

    def test_validate_log_success(self):
        from src.collectors.base import BaseCollector
        # 创建一个临时子类实例（基类抽象，需要实例化具体子类）
        collector = FilebeatCollector(config={})
        valid_log = {
            'timestamp': '2025-04-08T10:00:00',
            'log_type': 'vpn',
            'source': 'test',
            'message': 'user login'
        }
        assert collector.validate_log(valid_log) is True

    def test_validate_log_missing_field(self):
        collector = FilebeatCollector(config={})
        invalid_log = {
            'timestamp': '2025-04-08T10:00:00',
            'log_type': 'vpn'
            # 缺少 source 和 message
        }
        assert collector.validate_log(invalid_log) is False

    def test_enrich_log(self):
        collector = FilebeatCollector(config={})
        raw_log = {
            'timestamp': '2025-04-08T10:00:00',
            'log_type': 'vpn',
            'source': 'test',
            'message': 'user login'
        }
        enriched = collector.enrich_log(raw_log)
        assert 'collector' in enriched
        assert 'collected_at' in enriched
        assert 'msg_id' in enriched
        assert enriched['message'] == 'user login'


class TestFilebeatCollector:
    """测试 Filebeat 采集器（模拟 Kafka）"""

    @patch('src.collectors.filebeat.KafkaConsumer')
    def test_start_and_stop(self, mock_kafka_consumer):
        """测试启动和停止"""
        config = {
            'bootstrap_servers': 'localhost:9092',
            'kafka_topic': 'logs_raw',
            'group_id': 'test_group'
        }
        collector = FilebeatCollector(name='test', config=config)
        collector.start()
        assert collector.is_running is True
        mock_kafka_consumer.assert_called_once()
        collector.stop()
        assert collector.is_running is False
        # 验证 consumer.close 被调用
        collector.consumer.close.assert_called_once()

    @patch('src.collectors.filebeat.KafkaConsumer')
    def test_collect_yields_logs(self, mock_kafka_consumer):
        """测试 collect 生成器能正确产出日志"""
        # 模拟 KafkaConsumer 返回的消息
        mock_msg = MagicMock()
        mock_msg.value = {
            '@timestamp': '2025-04-08T10:00:00',
            'fields': {'log_type': 'vpn'},
            'message': 'user login success',
            'host': {'name': 'ubuntu-vm'},
            'offset': 123,
            'partition': 0
        }
        mock_consumer = MagicMock()
        mock_consumer.__iter__.return_value = [mock_msg]
        mock_kafka_consumer.return_value = mock_consumer

        config = {'bootstrap_servers': 'localhost:9092', 'kafka_topic': 'logs_raw'}
        collector = FilebeatCollector(config=config)
        collector.start()
        logs = list(collector.collect())  # 取一条
        assert len(logs) == 1
        log = logs[0]
        assert log['log_type'] == 'vpn'
        assert log['message'] == 'user login success'
        assert 'collector' in log
        assert 'msg_id' in log
        collector.stop()


class TestFlumeCollector:
    """测试 Flume 采集器"""

    @patch('src.collectors.flume.KafkaConsumer')
    def test_start_and_stop(self, mock_kafka_consumer):
        config = {
            'bootstrap_servers': 'localhost:9092',
            'kafka_topic': 'logs_raw',
            'group_id': 'flume_test'
        }
        collector = FlumeCollector(config=config)
        collector.start()
        assert collector.is_running is True
        collector.stop()
        assert collector.is_running is False

    @patch('src.collectors.flume.KafkaConsumer')
    def test_collect_with_batch(self, mock_kafka_consumer):
        # 模拟两条消息
        mock_msg1 = MagicMock()
        mock_msg1.value = {'timestamp': '2025-04-08T10:00:00', 'log_type': 'api', 'message': 'API call', 'source': 'flume'}
        mock_msg2 = MagicMock()
        mock_msg2.value = {'timestamp': '2025-04-08T10:01:00', 'log_type': 'api', 'message': 'API call2', 'source': 'flume'}
        mock_consumer = MagicMock()
        mock_consumer.__iter__.return_value = [mock_msg1, mock_msg2]
        mock_kafka_consumer.return_value = mock_consumer

        collector = FlumeCollector(config={'batch_size': 10})
        collector.start()
        logs = list(collector.collect())
        assert len(logs) == 2
        assert logs[0]['log_type'] == 'api'
        assert logs[1]['message'] == 'API call2'
        collector.stop()


if __name__ == "__main__":
    import subprocess
    sys.exit(subprocess.call(["pytest", __file__]))