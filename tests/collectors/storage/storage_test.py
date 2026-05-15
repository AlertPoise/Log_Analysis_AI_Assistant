#!/usr/bin/env python3
"""
存储模块集成测试

测试 src/storage/ 下的 ClickHouse、Kafka 客户端功能。
自动启动所需 Docker 服务（Kafka、ClickHouse），执行测试后清理环境。

适用环境：Linux（含 VMware 虚拟机），需要 Docker 和 Python 3.9+。
"""

import os
import sys
import time
import subprocess
import json
import shutil
import uuid
from pathlib import Path
from datetime import datetime
from typing import List, Dict, Any, Optional

# 添加项目根目录到 sys.path
sys.path.insert(0, str(Path(__file__).parent.parent.parent.parent))

from src.storage.kafka_client import KafkaClient
from src.storage.clickhouse import ClickHouseClient
# from src.storage.elasticsearch import ElasticsearchClient  # 暂时禁用

# 项目路径常量
PROJECT_ROOT = Path(__file__).parent.parent.parent.parent  # Log_Analysis_AI_Assistant
TESTS_DIR = PROJECT_ROOT / "tests"
STORAGE_TEST_DIR = TESTS_DIR / "collectors" / "storage"

# 测试配置
KAFKA_BOOTSTRAP = "localhost:9092"
KAFKA_TOPIC = "storage_test_topic"
CLICKHOUSE_HOST = "localhost"
CLICKHOUSE_PORT = 8123
CLICKHOUSE_DATABASE = "test_logs"
CLICKHOUSE_TABLE = "raw_logs"
CLICKHOUSE_PASSWORD = "clickhouse"
# ES_HOST = "localhost"
# ES_PORT = 9200
# ES_INDEX = "test_logs"

STORAGE_TEST_DIR.mkdir(parents=True, exist_ok=True)


class TerminalFormatter:
    """终端格式化输出，美化测试日志"""

    @staticmethod
    def print_header(text: str):
        print(f"\n{'=' * 60}")
        print(f" {text}")
        print(f"{'=' * 60}")

    @staticmethod
    def print_section(text: str):
        print(f"\n{'-' * 50}")
        print(f" {text}")
        print(f"{'-' * 50}")

    @staticmethod
    def print_step(step_num: int, text: str):
        print(f"\n[{step_num}] {text}...")

    @staticmethod
    def print_success(text: str):
        print(f"[OK] {text}")

    @staticmethod
    def print_warning(text: str):
        print(f"[WARN] {text}")

    @staticmethod
    def print_error(text: str):
        print(f"[ERROR] {text}")

    @staticmethod
    def print_info(text: str):
        print(f"   [INFO] {text}")


fmt = TerminalFormatter()


# -------------------- 依赖检查 --------------------
def check_prerequisites() -> bool:
    """检查系统依赖（Docker 和 Python 包）"""
    fmt.print_step(1, "检查系统依赖")

    # 检查 Docker
    try:
        subprocess.run(["docker", "--version"], check=True, capture_output=True)
        fmt.print_success("Docker 已安装")
    except:
        fmt.print_error("Docker 未安装，请先安装 Docker")
        return False

    # 检查必要的 Python 包
    packages = {
        "kafka-python": "kafka",
        "clickhouse-connect": "clickhouse_connect",
        # "elasticsearch": "elasticsearch"  # 暂时禁用
    }
    missing = []
    for pkg_name, import_name in packages.items():
        try:
            __import__(import_name)
            fmt.print_success(f"{pkg_name} 已安装")
        except ImportError:
            missing.append(pkg_name)
            fmt.print_error(f"{pkg_name} 未安装")

    if missing:
        fmt.print_error(f"缺少 Python 包: {', '.join(missing)}")
        fmt.print_info("请运行: pip install " + " ".join(missing))
        return False

    fmt.print_success("所有依赖检查通过")
    return True


# -------------------- Docker 服务管理 --------------------
def start_services() -> Dict[str, Any]:
    """使用 Docker Compose 启动 Kafka、ClickHouse"""
    fmt.print_step(2, "启动 Docker 服务 (Kafka + ClickHouse)")

    compose_file = STORAGE_TEST_DIR / "docker-compose.storage.yml"

    compose_content = """
services:
  kafka:
    image: apache/kafka:latest
    container_name: storage_test_kafka
    environment:
      KAFKA_NODE_ID: 1
      KAFKA_PROCESS_ROLES: broker,controller
      KAFKA_CONTROLLER_QUORUM_VOTERS: 1@localhost:9093
      KAFKA_LISTENERS: PLAINTEXT://0.0.0.0:9092,CONTROLLER://0.0.0.0:9093
      KAFKA_ADVERTISED_LISTENERS: PLAINTEXT://localhost:9092
      KAFKA_LISTENER_SECURITY_PROTOCOL_MAP: PLAINTEXT:PLAINTEXT,CONTROLLER:PLAINTEXT
      KAFKA_CONTROLLER_LISTENER_NAMES: CONTROLLER
      KAFKA_OFFSETS_TOPIC_REPLICATION_FACTOR: 1
    ports:
      - "9092:9092"

  clickhouse:
    image: clickhouse/clickhouse-server:latest
    container_name: storage_test_clickhouse
    ports:
      - "8123:8123"
      - "9000:9000"
    environment:
      CLICKHOUSE_DB: test_logs
      CLICKHOUSE_USER: default
      CLICKHOUSE_PASSWORD: clickhouse
      CLICKHOUSE_DEFAULT_ACCESS_MANAGEMENT: 1
    ulimits:
      nofile:
        soft: 262144
        hard: 262144
"""

    with open(compose_file, 'w') as f:
        f.write(compose_content)

    fmt.print_info("停止并清理旧容器...")
    subprocess.run(
        ["docker", "compose", "-f", str(compose_file), "down", "-v"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
    )

    fmt.print_info("启动服务容器...")
    subprocess.run(
        ["docker", "compose", "-f", str(compose_file), "up", "-d"],
        check=True
    )
    fmt.print_success("Docker 容器已启动")

    # 等待服务就绪
    wait_for_services()

    # 创建 Kafka topic
    create_kafka_topic()

    # 创建 ClickHouse 表
    create_clickhouse_table()

    return {"compose_file": compose_file}


def wait_for_services():
    """等待所有服务就绪"""
    fmt.print_info("等待服务就绪...")

    # 等待 Kafka
    import socket
    from kafka import KafkaAdminClient
    fmt.print_info("等待 Kafka...")
    for i in range(1, 31):
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(1)
            if sock.connect_ex(('localhost', 9092)) == 0:
                sock.close()
                KafkaAdminClient(bootstrap_servers=KAFKA_BOOTSTRAP).close()
                break
            sock.close()
        except:
            pass
        time.sleep(2)
    else:
        raise RuntimeError("Kafka 启动超时")
    fmt.print_success("Kafka 已就绪")

    # 等待 ClickHouse
    import clickhouse_connect
    fmt.print_info("等待 ClickHouse...")
    for i in range(1, 61):
        try:
            client = clickhouse_connect.get_client(
                host=CLICKHOUSE_HOST,
                port=CLICKHOUSE_PORT,
                username='default',
                password=CLICKHOUSE_PASSWORD,
                database=CLICKHOUSE_DATABASE,
                connect_timeout=3
            )
            result = client.command("SELECT 1")
            if result == 1:
                client.close()
                break
            client.close()
        except Exception:
            pass
        time.sleep(2)
    else:
        subprocess.run(["docker", "logs", "storage_test_clickhouse"])
        raise RuntimeError("ClickHouse 启动超时")
    fmt.print_success("ClickHouse 已就绪")

    # ========== 暂时禁用 Elasticsearch 等待 ==========
    fmt.print_info("跳过 Elasticsearch 等待")
    """
    fmt.print_info("等待 Elasticsearch...")
    max_attempts = 120
    for i in range(1, max_attempts + 1):
        fmt.print_progress(i, max_attempts, "等待 Elasticsearch")
        try:
            es = Elasticsearch([f"http://{ES_HOST}:{ES_PORT}"])
            if es.ping():
                health = es.cluster.health()
                if health['status'] in ('green', 'yellow'):
                    break
        except Exception:
            pass
        time.sleep(2 + (i // 30))
    else:
        subprocess.run(["docker", "logs", "--tail", "50", "storage_test_es"])
        raise RuntimeError("Elasticsearch 启动超时")
    print()
    fmt.print_success("Elasticsearch 已就绪")
    """


def create_kafka_topic():
    """创建测试用的 Kafka topic"""
    from kafka.admin import KafkaAdminClient, NewTopic
    from kafka.errors import TopicAlreadyExistsError
    try:
        admin = KafkaAdminClient(bootstrap_servers=KAFKA_BOOTSTRAP)
        admin.create_topics([NewTopic(name=KAFKA_TOPIC, num_partitions=1, replication_factor=1)])
        fmt.print_success(f"Kafka topic '{KAFKA_TOPIC}' 创建成功")
    except TopicAlreadyExistsError:
        fmt.print_success(f"Kafka topic '{KAFKA_TOPIC}' 已存在")
    except Exception as e:
        fmt.print_error(f"创建 Kafka topic 失败: {e}")
        raise


def create_clickhouse_table():
    """在 ClickHouse 中创建测试表"""
    client = ClickHouseClient({
        'host': CLICKHOUSE_HOST,
        'port': CLICKHOUSE_PORT,
        'username': 'default',
        'password': CLICKHOUSE_PASSWORD,
        'database': CLICKHOUSE_DATABASE
    })
    client.connect()
    create_sql = f"""
    CREATE TABLE IF NOT EXISTS {CLICKHOUSE_TABLE} (
        timestamp       DateTime64(3),
        log_type        String,
        source          String,
        message         String,
        host            String,
        offset          Int64,
        partition       Int32,
        collector       String,
        collected_at    DateTime64(3),
        msg_id          String
    ) ENGINE = MergeTree()
    ORDER BY (log_type, timestamp)
    """
    client.client.command(create_sql)
    client.close()
    fmt.print_success(f"ClickHouse 表 '{CLICKHOUSE_TABLE}' 已就绪")


# -------------------- 测试用例 --------------------
def test_kafka_client() -> bool:
    """测试 Kafka 客户端发送和消费消息"""
    fmt.print_section("测试 Kafka 客户端")
    test_passed = True

    # 准备测试数据
    test_messages = [
        {"id": i, "message": f"test log {i}", "timestamp": datetime.now().isoformat()}
        for i in range(1, 6)
    ]

    # 初始化生产者
    kafka_client = KafkaClient({'bootstrap_servers': KAFKA_BOOTSTRAP})
    kafka_client.connect_producer()

    # 批量发送消息
    fmt.print_info("发送测试消息到 Kafka...")
    sent_count = kafka_client.send_batch(KAFKA_TOPIC, test_messages)
    if sent_count != len(test_messages):
        fmt.print_error(f"批量发送失败: 期望 {len(test_messages)} 条，实际 {sent_count} 条")
        test_passed = False
    else:
        fmt.print_success(f"成功发送 {sent_count} 条消息")

    # 关闭生产者，初始化消费者
    kafka_client.close()

    # 使用新的消费者组，确保从头消费
    kafka_client = KafkaClient({
        'bootstrap_servers': KAFKA_BOOTSTRAP,
        'consumer_group_id': f'test_group_{uuid.uuid4().hex[:8]}',
        'consumer_auto_offset_reset': 'earliest'
    })
    kafka_client.connect_consumer([KAFKA_TOPIC])

    fmt.print_info("消费 Kafka 消息...")
    consumed = []
    try:
        def callback(msg_value, raw_msg):
            consumed.append(msg_value)

        kafka_client.consume(callback=callback, max_messages=len(test_messages), timeout_ms=5000)
    except Exception as e:
        fmt.print_error(f"消费过程中出错: {e}")
        test_passed = False

    if len(consumed) == len(test_messages):
        fmt.print_success(f"成功消费 {len(consumed)} 条消息")
    else:
        fmt.print_error(f"消费数量不符: 期望 {len(test_messages)} 条，实际 {len(consumed)} 条")
        test_passed = False

    kafka_client.close()
    return test_passed


def test_clickhouse_client() -> bool:
    """测试 ClickHouse 客户端插入、查询和聚合"""
    fmt.print_section("测试 ClickHouse 客户端")
    test_passed = True

    client = ClickHouseClient({
        'host': CLICKHOUSE_HOST,
        'port': CLICKHOUSE_PORT,
        'username': 'default',
        'password': CLICKHOUSE_PASSWORD,
        'database': CLICKHOUSE_DATABASE
    })

    try:
        client.connect()
        fmt.print_success("ClickHouse 连接成功")

        # 生成测试日志（符合采集器输出结构）
        test_logs = []
        for i in range(1, 11):
            log_type = "vpn" if i % 2 == 0 else "system"
            test_logs.append({
                "timestamp": datetime.now().isoformat(),
                "log_type": log_type,
                "source": "/var/log/test.log",
                "message": f"Test log message {i}",
                "host": "test-server",
                "offset": i * 100,
                "partition": 0,
                "collector": "test",
                "collected_at": datetime.now().isoformat(),
                "msg_id": f"msg_{uuid.uuid4().hex[:8]}"
            })

        # 批量插入
        fmt.print_info(f"插入 {len(test_logs)} 条测试日志...")
        inserted = client.insert_logs(CLICKHOUSE_TABLE, test_logs)
        if inserted != len(test_logs):
            fmt.print_error(f"插入数量不符: 期望 {len(test_logs)}，实际 {inserted}")
            test_passed = False
        else:
            fmt.print_success(f"成功插入 {inserted} 条日志")

        # 查询测试
        fmt.print_info("执行条件查询...")
        results = client.query_logs(CLICKHOUSE_TABLE, conditions={"log_type": "vpn"}, limit=5)
        if len(results) > 0:
            fmt.print_success(f"查询到 {len(results)} 条 vpn 类型日志")
            sample = results[0]
            required = ['timestamp', 'log_type', 'message']
            if all(k in sample for k in required):
                fmt.print_success("查询结果字段完整")
            else:
                fmt.print_error("查询结果缺少必需字段")
                test_passed = False
        else:
            fmt.print_warning("未查询到 vpn 日志，但插入成功，可能时间戳问题")
            test_passed = False

        # 聚合测试
        fmt.print_info("执行聚合查询...")
        agg_results = client.aggregate(
            table=CLICKHOUSE_TABLE,
            metrics=["count() AS cnt"],
            group_by=["log_type"]
        )
        if agg_results:
            fmt.print_success(f"聚合查询返回 {len(agg_results)} 个分组")
            for row in agg_results:
                fmt.print_info(f"   {row['log_type']}: {row['cnt']} 条")
        else:
            fmt.print_error("聚合查询无结果")
            test_passed = False

        client.close()
        return test_passed

    except Exception as e:
        fmt.print_error(f"ClickHouse 测试异常: {e}")
        import traceback
        traceback.print_exc()
        return False

#启动过慢暂时不启用
"""
def test_elasticsearch_client() -> bool:
    fmt.print_section("测试 Elasticsearch 客户端")
    test_passed = True

    client = ElasticsearchClient({
        'hosts': [f'{ES_HOST}:{ES_PORT}'],
        'use_ssl': False
    })

    try:
        client.connect()
        fmt.print_success("Elasticsearch 连接成功")

        # 生成测试文档
        test_docs = []
        for i in range(1, 6):
            test_docs.append({
                "timestamp": datetime.now().isoformat(),
                "log_type": "auth",
                "source": "/var/log/auth.log",
                "message": f"Failed password for user{i}",
                "host": "test-server",
                "user": f"user{i}",
                "status": "failed"
            })

        # 批量索引
        fmt.print_info(f"索引 {len(test_docs)} 个文档...")
        success_count = client.index_bulk(ES_INDEX, test_docs)
        if success_count != len(test_docs):
            fmt.print_error(f"索引数量不符: 期望 {len(test_docs)}，实际 {success_count}")
            test_passed = False
        else:
            fmt.print_success(f"成功索引 {success_count} 个文档")

        # 等待索引刷新
        time.sleep(2)

        # 搜索测试
        fmt.print_info("执行全文搜索...")
        search_query = {
            "query": {
                "match": {
                    "message": "failed"
                }
            }
        }
        search_results = client.search(ES_INDEX, search_query, size=10)
        if len(search_results) > 0:
            fmt.print_success(f"搜索到 {len(search_results)} 个文档")
        else:
            fmt.print_error("搜索无结果")
            test_passed = False

        # 聚合测试
        fmt.print_info("执行聚合分析...")
        aggs = {
            "by_log_type": {
                "terms": {"field": "log_type.keyword"}
            }
        }
        agg_results = client.aggregate(ES_INDEX, aggs)
        if agg_results:
            fmt.print_success("聚合查询成功")
            buckets = agg_results.get('by_log_type', {}).get('buckets', [])
            for bucket in buckets:
                fmt.print_info(f"   {bucket['key']}: {bucket['doc_count']} 个文档")
        else:
            fmt.print_error("聚合查询失败")
            test_passed = False

        client.close()
        return test_passed

    except Exception as e:
        fmt.print_error(f"Elasticsearch 测试异常: {e}")
        import traceback
        traceback.print_exc()
        return False
"""


# -------------------- 环境清理 --------------------
def cleanup(compose_file: Path):
    """清理测试环境"""
    fmt.print_section("清理测试环境")

    if compose_file and compose_file.exists():
        fmt.print_info("停止并删除 Docker 容器...")
        subprocess.run(
            ["docker", "compose", "-f", str(compose_file), "down", "-v"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
        )
        compose_file.unlink(missing_ok=True)
        fmt.print_success("Docker 容器已清理")
    else:
        fmt.print_info("未找到 compose 文件，跳过清理")

    fmt.print_success("环境清理完成")


# -------------------- 主函数 --------------------
def main():
    fmt.print_header("存储模块集成测试")

    if not check_prerequisites():
        sys.exit(1)

    compose_file = None
    try:
        # 启动服务
        services = start_services()
        compose_file = services["compose_file"]

        # 运行测试
        results = {
            "Kafka 客户端": test_kafka_client(),
            "ClickHouse 客户端": test_clickhouse_client(),
            # "Elasticsearch 客户端": test_elasticsearch_client()  # 暂时禁用
        }

        # 汇总结果
        fmt.print_header("测试结果汇总")
        all_passed = True
        for name, passed in results.items():
            if passed:
                fmt.print_success(f"{name}: 通过")
            else:
                fmt.print_error(f"{name}: 失败")
                all_passed = False

        if all_passed:
            fmt.print_header("所有存储模块测试通过！")
        else:
            fmt.print_header("部分测试失败")
            sys.exit(1)

    except Exception as e:
        fmt.print_error(f"测试过程中发生异常: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
    finally:
        if compose_file:
            cleanup(compose_file)


if __name__ == "__main__":
    main()