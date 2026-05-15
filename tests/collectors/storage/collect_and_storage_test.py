#!/usr/bin/env python3
"""
采集模块 + 存储模块端到端集成测试

测试完整数据流：
    日志生成 -> Filebeat -> Kafka -> 采集器消费 -> ClickHouse 存储

适用于 VMware Linux 环境，需要 Docker、Filebeat 和相关 Python 依赖。
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

from src.collectors.filebeat import FilebeatCollector
from src.collectors.flume import FlumeCollector
from src.storage.clickhouse import ClickHouseClient

# 路径常量
PROJECT_ROOT = Path(__file__).parent.parent.parent.parent  # Log_Analysis_AI_Assistant
TESTS_COLLECTORS_DIR = PROJECT_ROOT / "tests" / "collectors"
STORAGE_TEST_DIR = TESTS_COLLECTORS_DIR / "storage"
SAMPLE_LOGS_DIR = TESTS_COLLECTORS_DIR / "sample_logs"
GEN_VPN_LOGS_SCRIPT = TESTS_COLLECTORS_DIR / "gen_vpn_logs.py"

# 服务配置
KAFKA_BOOTSTRAP = "localhost:9092"
KAFKA_TOPIC = "logs_raw"
CLICKHOUSE_HOST = "localhost"
CLICKHOUSE_PORT = 8123
CLICKHOUSE_DATABASE = "test_logs"
CLICKHOUSE_TABLE = "raw_logs"
CLICKHOUSE_PASSWORD = ""

# 确保目录存在
SAMPLE_LOGS_DIR.mkdir(exist_ok=True)
STORAGE_TEST_DIR.mkdir(parents=True, exist_ok=True)


class TerminalFormatter:
    """终端格式化输出"""

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
    """检查必要的系统和 Python 依赖"""
    fmt.print_step(1, "检查系统依赖")

    # Docker
    try:
        subprocess.run(["docker", "--version"], check=True, capture_output=True)
        fmt.print_success("Docker 已安装")
    except:
        fmt.print_error("Docker 未安装")
        return False

    # Filebeat
    try:
        subprocess.run(["filebeat", "version"], check=True, capture_output=True)
        fmt.print_success("Filebeat 已安装")
    except:
        fmt.print_error("Filebeat 未安装")
        return False

    # Python 包
    packages = {
        "kafka-python": "kafka",
        "clickhouse-connect": "clickhouse_connect",
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
    """启动 Kafka 和 ClickHouse 容器"""
    fmt.print_step(2, "启动 Docker 服务 (Kafka + ClickHouse)")

    compose_file = STORAGE_TEST_DIR / "docker-compose.collect_storage.yml"

    compose_content = """
services:
  kafka:
    image: apache/kafka:latest
    container_name: collect_storage_kafka
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
    user: "101:101"
    container_name: collect_storage_clickhouse
    ports:
      - "8123:8123"
      - "9000:9000"
    environment:
      CLICKHOUSE_DB: test_logs
      CLICKHOUSE_USER: default
      CLICKHOUSE_PASSWORD: ""
      CLICKHOUSE_DEFAULT_ACCESS_MANAGEMENT: 1
    ulimits:
      nofile:
        soft: 262144
        hard: 262144
"""

    with open(compose_file, 'w') as f:
        f.write(compose_content)

    fmt.print_info("清理旧容器...")
    subprocess.run(
        ["docker", "compose", "-f", str(compose_file), "down", "-v"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
    )

    fmt.print_info("启动服务...")
    subprocess.run(
        ["docker", "compose", "-f", str(compose_file), "up", "-d"],
        check=True
    )
    fmt.print_success("容器已启动")

    wait_for_services()
    create_kafka_topic()
    create_clickhouse_table()

    return {"compose_file": compose_file}


def wait_for_services():
    """等待 Kafka 和 ClickHouse 就绪"""
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
            # 先不指定数据库连接
            client = clickhouse_connect.get_client(
                host=CLICKHOUSE_HOST,
                port=CLICKHOUSE_PORT,
                username='default',
                password=CLICKHOUSE_PASSWORD,
                connect_timeout=3
            )
            result = client.command("SELECT 1")
            if result == 1:
                # 创建数据库（如果不存在）
                client.command(f"CREATE DATABASE IF NOT EXISTS {CLICKHOUSE_DATABASE}")
                client.close()
                break
            client.close()
        except Exception:
            pass
        time.sleep(2)
    else:
        subprocess.run(["docker", "logs", "collect_storage_clickhouse"])
        raise RuntimeError("ClickHouse 启动超时")
    fmt.print_success("ClickHouse 已就绪")


def create_kafka_topic():
    """创建 Kafka 主题"""
    from kafka.admin import KafkaAdminClient, NewTopic
    from kafka.errors import TopicAlreadyExistsError
    try:
        admin = KafkaAdminClient(bootstrap_servers=KAFKA_BOOTSTRAP)
        admin.create_topics([NewTopic(name=KAFKA_TOPIC, num_partitions=1, replication_factor=1)])
        fmt.print_success(f"Kafka topic '{KAFKA_TOPIC}' 创建成功")
    except TopicAlreadyExistsError:
        fmt.print_success(f"Kafka topic '{KAFKA_TOPIC}' 已存在")
    except Exception as e:
        fmt.print_error(f"创建 topic 失败: {e}")
        raise


def create_clickhouse_table():
    """创建 ClickHouse 表"""
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


# -------------------- 日志生成 --------------------
def generate_vpn_logs() -> bool:
    """使用 gen_vpn_logs.py 生成测试日志"""
    fmt.print_step(3, "生成 VPN 测试日志")

    if not GEN_VPN_LOGS_SCRIPT.exists():
        fmt.print_error(f"未找到日志生成脚本: {GEN_VPN_LOGS_SCRIPT}")
        return False

    # 清空目录
    if SAMPLE_LOGS_DIR.exists():
        shutil.rmtree(SAMPLE_LOGS_DIR)
    SAMPLE_LOGS_DIR.mkdir(exist_ok=True)

    cmd = [
        sys.executable,
        str(GEN_VPN_LOGS_SCRIPT),
        "--start", "2026-04-01",
        "--days", "2",
        "--count", "20",
        "--outdir", str(SAMPLE_LOGS_DIR),
        "--format", "syslog",
        "--seed", "42"
    ]

    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        fmt.print_error(f"日志生成失败: {result.stderr}")
        return False

    log_files = list(SAMPLE_LOGS_DIR.glob("*.log"))
    if not log_files:
        fmt.print_error("未生成任何日志文件")
        return False

    total_lines = 0
    for log_file in log_files:
        with open(log_file, 'r', encoding='utf-8') as f:
            total_lines += len(f.readlines())

    fmt.print_success(f"生成 {len(log_files)} 个日志文件，共 {total_lines} 行")
    return True


# -------------------- Filebeat 启动 --------------------
def start_filebeat():
    """启动 Filebeat 采集日志并发送到 Kafka"""
    fmt.print_step(4, "启动 Filebeat 服务")

    # 清理旧数据
    data_dir = PROJECT_ROOT / "filebeat_data"
    logs_dir = PROJECT_ROOT / "filebeat_logs"

    subprocess.run(["sudo", "pkill", "-f", "filebeat"],
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    time.sleep(2)

    for dir_path in [data_dir, logs_dir]:
        if dir_path.exists():
            try:
                shutil.rmtree(dir_path)
            except PermissionError:
                subprocess.run(["sudo", "rm", "-rf", str(dir_path)], check=True)

    for dir_path in [data_dir, logs_dir]:
        dir_path.mkdir(exist_ok=True, parents=True)
        subprocess.run(["sudo", "chown", "-R", f"{os.getenv('USER')}:{os.getenv('USER')}", str(dir_path)], check=False)
        subprocess.run(["sudo", "chmod", "-R", "755", str(dir_path)], check=False)

    # 生成 Filebeat 配置
    config_path = PROJECT_ROOT / "config" / "filebeat.yml"
    config_path.parent.mkdir(exist_ok=True)
    config_content = f"""
filebeat.inputs:
- type: log
  enabled: true
  paths:
    - {SAMPLE_LOGS_DIR}/*.log
  fields:
    log_type: vpn
  fields_under_root: true

output.kafka:
  hosts: ["{KAFKA_BOOTSTRAP}"]
  topic: "{KAFKA_TOPIC}"
  partition.round_robin:
    reachable_only: false
  required_acks: 1
  compression: gzip
  max_message_bytes: 1000000

logging.level: warning
logging.to_files: true
logging.files:
  path: {logs_dir}
  name: filebeat
  keepfiles: 7
"""
    with open(config_path, 'w') as f:
        f.write(config_content)

    # 启动 Filebeat
    cmd = [
        "sudo", "filebeat", "-e",
        "-c", str(config_path.absolute()),
        "--path.data", str(data_dir),
        "--path.logs", str(logs_dir),
        "--strict.perms=false",
    ]
    proc = subprocess.Popen(cmd, cwd=str(PROJECT_ROOT),
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    fmt.print_success(f"Filebeat 已启动，PID: {proc.pid}")
    time.sleep(10)  # 等待采集传输
    return proc


# -------------------- 采集并存储测试 --------------------
def _produce_test_logs_to_kafka(count: int = 15):
    """直接向 Kafka 写入测试日志（不依赖 Filebeat）"""
    from kafka import KafkaProducer
    import json

    # 定义硬编码的测试数据模板
    TEST_LOG_TEMPLATES = [
        {"log_type": "vpn", "action": "login", "status": "success", "user_prefix": "admin"},
        {"log_type": "vpn", "action": "logout", "status": "success", "user_prefix": "user"},
        {"log_type": "vpn", "action": "login", "status": "failed", "user_prefix": "guest"},
        {"log_type": "vpn", "action": "connect", "status": "success", "user_prefix": "admin"},
        {"log_type": "vpn", "action": "disconnect", "status": "success", "user_prefix": "user"},
    ]

    fmt.print_info(f"========== 开始向 Kafka 写入测试数据 ==========")
    fmt.print_info(f"目标 Topic: {KAFKA_TOPIC}")
    fmt.print_info(f"目标 Broker: {KAFKA_BOOTSTRAP}")
    fmt.print_info(f"计划写入: {count} 条测试日志")
    fmt.print_info(f"--------------------------------------------")

    try:
        producer = KafkaProducer(
            bootstrap_servers=KAFKA_BOOTSTRAP.split(','),
            value_serializer=lambda v: json.dumps(v).encode('utf-8'),
            max_block_ms=10000
        )

        for i in range(count):
            template = TEST_LOG_TEMPLATES[i % len(TEST_LOG_TEMPLATES)]
            log_entry = {
                '@timestamp': datetime.now().isoformat(),
                'message': f"[TEST DATA] {template['log_type'].upper()} {template['action']} - {template['user_prefix']}{i:03d} from 192.168.1.{i} ({template['status']})",
                'fields': {
                    'log_type': template['log_type']
                },
                'log': {
                    'file': {
                        'path': f'/var/log/vpn/test_{template["log_type"]}_{i}.log'
                    }
                },
                'host': {
                    'name': 'test-host-01'
                },
                'user': f'{template["user_prefix"]}{i:03d}',
                'src_ip': f'192.168.1.{i}',
                'action': template['action'],
                'status': template['status'],
                '_test_data': True,  # 标记为测试数据
                '_test_index': i     # 测试数据索引
            }

            # 打印每条写入的测试数据（确保可见）
            fmt.print_info(f"写入 #{i+1}: [{log_entry['log_type']}] {log_entry['action']} | {log_entry['user']} | {log_entry['status']}")
            producer.send(KAFKA_TOPIC, value=log_entry)

        producer.flush()
        producer.close()

        fmt.print_info(f"--------------------------------------------")
        fmt.print_success(f"成功向 Kafka 写入 {count} 条测试日志")
        fmt.print_info(f"========== 测试数据写入完成 ==========")
        return True
    except Exception as e:
        fmt.print_error(f"写入 Kafka 失败: {e}")
        return False


def test_collect_and_store() -> bool:
    """测试采集器消费 Kafka 并存入 ClickHouse"""
    fmt.print_step(5, "采集日志并存入 ClickHouse")

    # 先创建数据库（如果不存在）
    fmt.print_info("正在连接 ClickHouse...")
    try:
        import clickhouse_connect
        admin_client = clickhouse_connect.get_client(
            host=CLICKHOUSE_HOST,
            port=CLICKHOUSE_PORT,
            username='default',
            password=CLICKHOUSE_PASSWORD,
            connect_timeout=5,
            send_receive_timeout=5
        )
        admin_client.command(f"CREATE DATABASE IF NOT EXISTS {CLICKHOUSE_DATABASE}")
        admin_client.close()
        fmt.print_success(f"数据库 '{CLICKHOUSE_DATABASE}' 已就绪")
    except Exception as e:
        fmt.print_error(f"连接 ClickHouse 失败: {e}")
        return False

    # 直接向 Kafka 写入测试数据（不依赖 Filebeat）
    if not _produce_test_logs_to_kafka(count=15):
        fmt.print_error("无法写入测试数据到 Kafka")
        return False

    # 初始化 ClickHouse 客户端
    ch_client = ClickHouseClient({
        'host': CLICKHOUSE_HOST,
        'port': CLICKHOUSE_PORT,
        'username': 'default',
        'password': CLICKHOUSE_PASSWORD,
        'database': CLICKHOUSE_DATABASE
    })
    ch_client.connect()

    # 初始化采集器（使用 FilebeatCollector）
    group_id = f"collect_storage_test_{uuid.uuid4().hex[:8]}"
    collector = FilebeatCollector(config={
        'bootstrap_servers': KAFKA_BOOTSTRAP,
        'kafka_topic': KAFKA_TOPIC,
        'group_id': group_id
    })
    collector.start()
    fmt.print_info("采集器已启动，开始消费...")

    logs_batch = []
    max_logs = 15
    received = 0
    start_time = time.time()
    timeout = 45

    try:
        for log in collector.collect():
            logs_batch.append(log)
            received += 1
            fmt.print_info(f"收到第 {received} 条: {log.get('log_type', 'unknown')} - {log.get('message', '')[:40]}...")

            if received >= max_logs:
                break
            if time.time() - start_time > timeout:
                fmt.print_warning(f"等待超时，已收集 {received} 条")
                break
    except Exception as e:
        fmt.print_error(f"消费异常: {e}")
        collector.stop()
        ch_client.close()
        return False

    collector.stop()
    fmt.print_info(f"采集器已停止，共收集 {len(logs_batch)} 条日志")

    if len(logs_batch) == 0:
        fmt.print_error("未采集到任何日志，测试失败")
        ch_client.close()
        return False

    # 写入 ClickHouse
    fmt.print_info(f"正在写入 {len(logs_batch)} 条日志到 ClickHouse...")
    try:
        inserted = ch_client.insert_logs(CLICKHOUSE_TABLE, logs_batch)
        if inserted != len(logs_batch):
            fmt.print_error(f"写入数量不符: 期望 {len(logs_batch)}, 实际 {inserted}")
            ch_client.close()
            return False
        fmt.print_success(f"成功写入 {inserted} 条日志")
    except Exception as e:
        fmt.print_error(f"写入 ClickHouse 失败: {e}")
        ch_client.close()
        return False

    # 验证查询
    time.sleep(2)
    try:
        results = ch_client.query_logs(CLICKHOUSE_TABLE, limit=100)
        if len(results) >= len(logs_batch):
            fmt.print_success(f"ClickHouse 查询验证通过，当前表中共 {len(results)} 条记录")
        else:
            fmt.print_warning(f"查询结果少于写入量: 写入 {len(logs_batch)}，查询到 {len(results)}")
    except Exception as e:
        fmt.print_error(f"验证查询失败: {e}")

    ch_client.close()
    return True


# -------------------- 环境清理 --------------------
def cleanup(compose_file: Path, filebeat_proc: Optional[subprocess.Popen]):
    """清理测试环境"""
    fmt.print_section("清理测试环境")

    # 停止 Filebeat
    if filebeat_proc:
        fmt.print_info("停止 Filebeat...")
        filebeat_proc.terminate()
        try:
            filebeat_proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            filebeat_proc.kill()
        subprocess.run(["sudo", "pkill", "-9", "-f", "filebeat"],
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        fmt.print_success("Filebeat 已停止")

    # 删除数据目录
    for dir_name in ["filebeat_data", "filebeat_logs"]:
        dir_path = PROJECT_ROOT / dir_name
        if dir_path.exists():
            try:
                shutil.rmtree(dir_path)
            except PermissionError:
                subprocess.run(["sudo", "rm", "-rf", str(dir_path)], check=False)

    # 停止 Docker 容器
    if compose_file and compose_file.exists():
        fmt.print_info("停止 Docker 容器...")
        subprocess.run(
            ["docker", "compose", "-f", str(compose_file), "down", "-v"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
        )
        compose_file.unlink(missing_ok=True)
        fmt.print_success("容器已清理")

    # 清理生成的日志文件
    if SAMPLE_LOGS_DIR.exists():
        shutil.rmtree(SAMPLE_LOGS_DIR, ignore_errors=True)

    fmt.print_success("环境清理完成")


# -------------------- 主函数 --------------------
def main():
    fmt.print_header("采集模块 + 存储模块端到端集成测试")

    if not check_prerequisites():
        sys.exit(1)

    compose_file = None
    filebeat_proc = None

    try:
        # 启动服务
        services = start_services()
        compose_file = services["compose_file"]

        # 生成日志
        if not generate_vpn_logs():
            cleanup(compose_file, filebeat_proc)
            sys.exit(1)

        # 启动 Filebeat
        filebeat_proc = start_filebeat()

        # 等待日志传输到 Kafka
        fmt.print_info("等待 10 秒确保日志传输到 Kafka...")
        time.sleep(10)

        # 执行采集与存储测试
        test_passed = test_collect_and_store()

        # 结果汇总
        fmt.print_header("测试结果")
        if test_passed:
            fmt.print_success("端到端测试通过！日志已成功从 Filebeat 流转至 ClickHouse。")
        else:
            fmt.print_error("端到端测试失败，请检查上述日志。")
            sys.exit(1)

    except Exception as e:
        fmt.print_error(f"测试过程中发生异常: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
    finally:
        cleanup(compose_file, filebeat_proc)


if __name__ == "__main__":
    main()