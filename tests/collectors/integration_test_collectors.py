#!/usr/bin/env python3
"""
集成测试：验证采集模块 + Filebeat + Kafka + VPN日志生成器
同时测试 FilebeatCollector 和 FlumeCollector
适用于 VMware Linux 环境，所有组件均以真实进程运行。
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
from typing import List, Dict, Any

# 添加项目根目录到 sys.path
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from src.collectors.filebeat import FilebeatCollector
from src.collectors.flume import FlumeCollector

PROJECT_ROOT = Path(__file__).parent.parent.parent          # Log_Analysis_AI_Assistant
TESTS_COLLECTORS_DIR = Path(__file__).parent                # tests/collectors
SAMPLE_LOGS_DIR = TESTS_COLLECTORS_DIR / "sample_logs"
GEN_VPN_LOGS_SCRIPT = TESTS_COLLECTORS_DIR / "gen_vpn_logs.py"

KAFKA_TOPIC = "logs_raw"
KAFKA_BOOTSTRAP = "localhost:9092"

SAMPLE_LOGS_DIR.mkdir(exist_ok=True)

class TerminalFormatter:

    @staticmethod
    def print_header(text: str):
        """标题"""
        print(f"\n{'='*60}")
        print(f"  {text}")
        print(f"{'='*60}")

    @staticmethod
    def print_section(text: str):
        print(f"\n{'─'*50}")
        print(f"  {text}")
        print(f"{'─'*50}")

    @staticmethod
    def print_step(step_num: int, text: str):
        print(f"\n[{step_num}] {text}...")

    @staticmethod
    def print_success(text: str):
        print(f"success{text}")

    @staticmethod
    def print_warning(text: str):
        """打印警告信息"""
        print(f"warning{text}")

    @staticmethod
    def print_error(text: str):
        """打印错误信息"""
        print(f"error{text}")

    @staticmethod
    def print_info(text: str):
        """打印信息"""
        print(f"  #  {text}")

    @staticmethod
    def print_progress(progress: int, total: int, text: str = ""):
        """打印进度条"""
        percent = (progress / total) * 100
        bar_length = 30
        filled_length = int(bar_length * progress // total)
        bar = '█' * filled_length + '░' * (bar_length - filled_length)
        print(f"\r  [{bar}] {percent:.1f}% {text}", end='', flush=True)
        if progress == total:
            print()

fmt = TerminalFormatter()

def check_prerequisites() -> bool:
    """检查系统依赖（容器化版本）"""
    fmt.print_step(1, "检查系统依赖")

    dependencies = [
        ("docker", ["docker", "--version"], "Docker"),
    ]

    missing = []
    for name, cmd, display_name in dependencies:
        try:
            subprocess.run(cmd, check=True, capture_output=True)
            fmt.print_success(f"{display_name} 已安装")
        except:
            missing.append(display_name)
            fmt.print_error(f"{display_name} 未安装")

    # 检查 Filebeat 容器是否运行
    try:
        result = subprocess.run(["docker", "ps", "--filter", "name=filebeat", "--format", "{{.Names}}"],
                               capture_output=True, text=True)
        if "filebeat" in result.stdout:
            fmt.print_success("Filebeat 容器已运行")
        else:
            fmt.print_warning("Filebeat 容器未运行，将在测试中启动")
    except:
        fmt.print_warning("无法检查 Filebeat 容器状态")

    try:
        from kafka import KafkaConsumer
        fmt.print_success("kafka-python 已安装")
    except ImportError:
        missing.append("kafka-python")
        fmt.print_error("kafka-python 未安装，请运行: pip install kafka-python")

    if missing:
        fmt.print_error(f"缺少依赖: {', '.join(missing)}")
        return False

    fmt.print_success("所有依赖检查通过")
    return True

def start_kafka_with_docker() -> Path:
    """启动 Kafka 容器（优先使用已运行的容器）"""
    fmt.print_step(2, "启动 Kafka 容器")

    compose_file = PROJECT_ROOT / "docker-compose.yml"

    # 检查是否已有运行的 Kafka 容器
    result = subprocess.run(["docker", "ps", "--filter", "name=kafka", "--format", "{{.Names}}"],
                           capture_output=True, text=True)
    if "kafka" in result.stdout:
        fmt.print_info("检测到已运行的 Kafka 容器，直接使用...")
        # 检查端口是否可用
        import socket
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(2)
        if sock.connect_ex(('localhost', 9092)) == 0:
            sock.close()
            fmt.print_success("Kafka 服务已就绪")
            return None  # 返回 None 表示使用现有容器，不需要清理
        sock.close()

    fmt.print_info("清理现有容器...")
    subprocess.run(["docker", "rm", "-f", "kafka-kraft"],
                   stderr=subprocess.DEVNULL, stdout=subprocess.DEVNULL)

    # 使用国内镜像
    compose_content = """
services:
  kafka:
    image: registry.cn-hangzhou.aliyuncs.com/apache/kafka:latest
    container_name: kafka-kraft
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
"""
    with open(compose_file, 'w') as f:
        f.write(compose_content)

    fmt.print_info("启动 Kafka 服务...")
    subprocess.run(["docker", "compose", "-f", str(compose_file), "down", "--remove-orphans"],
                   stderr=subprocess.DEVNULL, stdout=subprocess.DEVNULL)

    try:
        subprocess.run(["docker", "compose", "-f", str(compose_file), "up", "-d"], check=True)
    except subprocess.CalledProcessError:
        fmt.print_error("使用阿里云镜像失败，尝试官方镜像...")
        # 尝试使用官方镜像
        compose_content = compose_content.replace("registry.cn-hangzhou.aliyuncs.com/apache/kafka:latest", "apache/kafka:latest")
        with open(compose_file, 'w') as f:
            f.write(compose_content)
        subprocess.run(["docker", "compose", "-f", str(compose_file), "up", "-d"], check=True)

    fmt.print_success("Kafka 容器已启动")
    wait_for_kafka()
    create_kafka_topic()
    return compose_file

def wait_for_kafka(max_retries: int = 30, delay: int = 2):
    """等待 Kafka 就绪"""
    fmt.print_info("等待 Kafka 服务就绪...")

    import socket
    from kafka import KafkaAdminClient

    for i in range(max_retries):
        fmt.print_progress(i + 1, max_retries, "等待 Kafka")
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(1)
            if sock.connect_ex(('localhost', 9092)) == 0:
                sock.close()
                KafkaAdminClient(bootstrap_servers=KAFKA_BOOTSTRAP).close()
                print()
                fmt.print_success("Kafka 已就绪")
                return
            sock.close()
        except:
            pass
        time.sleep(delay)

    raise RuntimeError("Kafka 启动超时")

def create_kafka_topic():
    """创建 Kafka 主题"""
    fmt.print_info("创建 Kafka 主题...")

    from kafka.admin import KafkaAdminClient, NewTopic
    from kafka.errors import TopicAlreadyExistsError

    try:
        admin = KafkaAdminClient(bootstrap_servers=KAFKA_BOOTSTRAP)
        admin.create_topics([NewTopic(name=KAFKA_TOPIC, num_partitions=1, replication_factor=1)])
        fmt.print_success(f"Topic '{KAFKA_TOPIC}' 创建成功")
    except TopicAlreadyExistsError:
        fmt.print_success(f"Topic '{KAFKA_TOPIC}' 已存在")
    except Exception as e:
        fmt.print_error(f"创建 topic 失败: {e}")
        raise

def generate_vpn_logs() -> bool:
    """使用 gen_vpn_logs.py 生成 VPN 日志文件"""
    fmt.print_step(3, "生成 VPN 测试日志")

    if not GEN_VPN_LOGS_SCRIPT.exists():
        fmt.print_error(f"未找到 VPN 日志生成脚本: {GEN_VPN_LOGS_SCRIPT}")
        return False

    # 清空 sample_logs 目录
    if SAMPLE_LOGS_DIR.exists():
        shutil.rmtree(SAMPLE_LOGS_DIR)
    SAMPLE_LOGS_DIR.mkdir(exist_ok=True)

    # 生成 VPN 日志，指定为 syslog 格式
    fmt.print_info("执行 VPN 日志生成...")
    cmd = [
        sys.executable,
        str(GEN_VPN_LOGS_SCRIPT),
        "--start", "2026-04-01",  # 指定起始日期
        "--days", "7",  # 生成7天的日志
        "--count", "30",  # 增加日志数量确保测试足够
        "--outdir", str(SAMPLE_LOGS_DIR),
        "--format", "syslog",  # 只生成 syslog 格式
        "--seed", "42"  # 固定种子，保证测试结果可复现
    ]

    result = subprocess.run(cmd, capture_output=True, text=True)

    if result.returncode != 0:
        fmt.print_error(f"生成 VPN 日志失败: {result.stderr}")
        return False

    # 检查生成的文件
    log_files = list(SAMPLE_LOGS_DIR.glob("*.log"))
    if not log_files:
        fmt.print_error(f"在 {SAMPLE_LOGS_DIR} 中未找到日志文件")
        return False

    # 统计日志行数
    total_lines = 0
    for log_file in log_files:
        with open(log_file, 'r', encoding='utf-8') as f:
            total_lines += len(f.readlines())

    fmt.print_success(f"VPN 日志生成成功: {len(log_files)} 个文件，共 {total_lines} 行日志")

    # 显示示例
    for log_file in log_files[:1]:  # 只显示第一个文件的前2行
        with open(log_file, 'r', encoding='utf-8') as f:
            lines = f.readlines()[:2]
        fmt.print_info(f"日志示例 ({log_file.name}):")
        for i, line in enumerate(lines, 1):
            fmt.print_info(f"  [{i}] {line.strip()[:80]}...")

    return True

def start_filebeat():
    """启动容器化的 Filebeat 服务"""
    fmt.print_step(4, "启动 Filebeat 容器")

    compose_file = PROJECT_ROOT / "tests" / "collectors" / "docker-compose-full.yml"

    # 检查容器是否已运行
    result = subprocess.run(["docker", "ps", "--filter", "name=filebeat", "--format", "{{.Names}}"],
                           capture_output=True, text=True)
    if "filebeat" in result.stdout:
        fmt.print_success("Filebeat 容器已在运行，跳过启动")
        return None  # 返回 None 表示使用容器

    # 启动 Filebeat 容器
    fmt.print_info("启动 Filebeat 容器...")
    subprocess.run(["docker", "compose", "-f", str(compose_file), "up", "-d", "filebeat"], check=True)

    # 等待容器启动
    fmt.print_info("等待 Filebeat 容器就绪...")
    for _ in range(10):
        result = subprocess.run(["docker", "ps", "--filter", "name=filebeat", "--filter", "status=running", "--format", "{{.Names}}"],
                               capture_output=True, text=True)
        if "filebeat" in result.stdout:
            fmt.print_success("Filebeat 容器已启动")
            return None  # 返回 None 表示使用容器
        time.sleep(2)

    raise RuntimeError("Filebeat 容器启动超时")

def test_consumption(collector_class, min_messages: int = 5, timeout: int = 30) -> bool:
    """测试采集器消费能力"""
    collector_name = collector_class.__name__
    fmt.print_section(f"测试 {collector_name}")

    group_id = f"test_{collector_name.lower()}_{uuid.uuid4().hex[:8]}"
    config = {
        'bootstrap_servers': KAFKA_BOOTSTRAP,
        'kafka_topic': KAFKA_TOPIC,
        'group_id': group_id
    }

    # 创建并启动采集器
    collector = collector_class(config=config)
    try:
        collector.start()
        fmt.print_info(f"{collector_name} 已启动，开始消费...")
    except Exception as e:
        fmt.print_error(f"启动 {collector_name} 失败: {e}")
        return False

    received = []
    start_time = time.time()

    try:
        for log in collector.collect():
            received.append(log)

            # 显示进度
            if len(received) <= 3 or len(received) % 5 == 0:
                msg_preview = log.get('message', '')[:50]
                if len(msg_preview) < len(log.get('message', '')):
                    msg_preview += "..."
                fmt.print_info(f"收到第 {len(received)} 条: {log.get('log_type', 'unknown')} - {msg_preview}")

            if len(received) >= min_messages:
                fmt.print_success(f"已收集 {len(received)} 条日志，达到目标")
                break
            if time.time() - start_time > timeout:
                fmt.print_warning(f"等待超时 ({timeout}s)，已收集 {len(received)} 条")
                break
    except Exception as e:
        fmt.print_error(f"消费过程中出错: {e}")
        return False
    finally:
        collector.stop()
        fmt.print_info(f"{collector_name} 已停止")

    # 验证结果
    if len(received) >= min_messages:
        # 验证字段完整性
        required_fields = ['timestamp', 'log_type', 'message']
        for i, log in enumerate(received[:3]):
            if not all(k in log for k in required_fields):
                fmt.print_error(f"第 {i+1} 条日志缺少必需字段")
                return False

        # 显示统计信息
        fmt.print_success(f"{collector_name} 测试通过!")
        fmt.print_info(f"  - 成功消费: {len(received)} 条日志")
        fmt.print_info(f"  - 耗时: {time.time() - start_time:.2f} 秒")
        fmt.print_info(f"  - 首条消息: {received[0].get('message', '')[:50]}...")
        return True
    else:
        fmt.print_error(f"{collector_name} 测试失败: 只收到 {len(received)} 条，少于目标 {min_messages} 条")
        return False

def test_incremental_collection(collector_class) -> bool:
    """测试采集器增量采集能力"""
    collector_name = collector_class.__name__
    fmt.print_section(f"测试 {collector_name} 增量采集")

    group_id = f"inc_{collector_name.lower()}_{uuid.uuid4().hex[:8]}"
    config = {
        'bootstrap_servers': KAFKA_BOOTSTRAP,
        'kafka_topic': KAFKA_TOPIC,
        'group_id': group_id
    }

    # 第一次消费
    fmt.print_info("第一次消费 (收集3条)...")
    c1 = collector_class(config=config)
    c1.start()
    first_batch = []
    try:
        start = time.time()
        for log in c1.collect():
            first_batch.append(log)
            if len(first_batch) >= 3 or time.time() - start > 30:
                break
    finally:
        c1.stop()

    if not first_batch:
        fmt.print_error("第一次消费未收到任何日志")
        return False

    first_offsets = [l.get('offset') for l in first_batch]
    fmt.print_info(f"第一次消费 {len(first_batch)} 条，offsets: {first_offsets}")

    # 等待一段时间
    fmt.print_info("等待5秒后继续消费...")
    time.sleep(5)

    # 第二次消费（使用相同的 group_id 应从上一次 offset 继续）
    fmt.print_info("第二次消费 (收集3条)...")
    c2 = collector_class(config=config)
    c2.start()
    second_batch = []
    try:
        start = time.time()
        for log in c2.collect():
            second_batch.append(log)
            if len(second_batch) >= 3 or time.time() - start > 30:
                break
    finally:
        c2.stop()

    if not second_batch:
        fmt.print_error("第二次消费未收到任何日志")
        return False

    second_offsets = [l.get('offset') for l in second_batch]
    fmt.print_info(f"第二次消费 {len(second_batch)} 条，offsets: {second_offsets}")

    # 验证增量采集
    if first_batch and second_batch:
        min_second_offset = min(offset for offset in second_offsets if offset is not None)
        max_first_offset = max(offset for offset in first_offsets if offset is not None)

        if min_second_offset > max_first_offset:
            fmt.print_success(f"{collector_name} 增量采集验证成功!")
            fmt.print_info(f"  - 第一次消费最大 offset: {max_first_offset}")
            fmt.print_info(f"  - 第二次消费最小 offset: {min_second_offset}")
            return True
        else:
            fmt.print_warning(f"offset 未正确递增: {min_second_offset} <= {max_first_offset}")

    fmt.print_error(f"{collector_name} 增量采集验证失败")
    return False

def cleanup(kafka_compose_file: Path, filebeat_proc):
    """清理测试环境（容器化版本）"""
    fmt.print_section("清理测试环境")

    # 停止 Filebeat 容器
    fmt.print_info("停止 Filebeat 容器...")
    try:
        subprocess.run(["docker", "stop", "filebeat"],
                      stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        subprocess.run(["docker", "rm", "filebeat"],
                      stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        fmt.print_success("Filebeat 容器已停止并移除")
    except:
        fmt.print_info("Filebeat 容器未运行，跳过")

    # 清理数据目录
    fmt.print_info("清理数据目录...")
    data_dir = PROJECT_ROOT / "filebeat_data"
    logs_dir = PROJECT_ROOT / "filebeat_logs"

    for dir_path in [data_dir, logs_dir]:
        if dir_path.exists():
            try:
                shutil.rmtree(dir_path)
                fmt.print_info(f"已删除目录: {dir_path.name}")
            except PermissionError:
                subprocess.run(["sudo", "rm", "-rf", str(dir_path)],
                              stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                fmt.print_info(f"已用 sudo 删除目录: {dir_path.name}")

    fmt.print_info("停止 Kafka 容器...")
    if kafka_compose_file and kafka_compose_file.exists():
        subprocess.run(["docker", "compose", "-f", str(kafka_compose_file), "down", "-v"],
                      capture_output=True)
        kafka_compose_file.unlink(missing_ok=True)
        fmt.print_success("Kafka 容器已停止并清理")
    else:
        fmt.print_info("Kafka 容器文件不存在，跳过清理")

    fmt.print_success("环境清理完成")

def main():
    fmt.print_header("采集模块集成测试 (VPN日志生成器)")

    # 检查依赖
    if not check_prerequisites():
        sys.exit(1)

    kafka_compose_file = None
    filebeat_proc = None

    try:
        # 启动 Kafka
        kafka_compose_file = start_kafka_with_docker()

        # 生成 VPN 日志
        if not generate_vpn_logs():
            cleanup(kafka_compose_file, filebeat_proc)
            sys.exit(1)

        # 启动 Filebeat
        filebeat_proc = start_filebeat()

        # 等待 Filebeat 采集和传输
        fmt.print_step(5, "等待日志采集和传输")
        fmt.print_info("等待 30 秒让 Filebeat 采集并发送日志到 Kafka...")
        fmt.print_info("(Filebeat 默认每 10 秒扫描一次新文件)")
        for i in range(1, 31):
            fmt.print_progress(i, 30, "等待日志传输")
            time.sleep(1)
        print()  # 换行

        # 测试 FilebeatCollector
        fmt.print_section("开始测试 FilebeatCollector")
        filebeat_basic_ok = test_consumption(FilebeatCollector, min_messages=5, timeout=30)
        filebeat_inc_ok = test_incremental_collection(FilebeatCollector)

        # 测试 FlumeCollector
        fmt.print_section("开始测试 FlumeCollector")
        flume_basic_ok = test_consumption(FlumeCollector, min_messages=5, timeout=30)
        flume_inc_ok = test_incremental_collection(FlumeCollector)

        # 汇总结果
        fmt.print_header("测试结果汇总")

        test_results = [
            ("FilebeatCollector 基本消费", filebeat_basic_ok),
            ("FilebeatCollector 增量采集", filebeat_inc_ok),
            ("FlumeCollector 基本消费", flume_basic_ok),
            ("FlumeCollector 增量采集", flume_inc_ok),
        ]

        all_passed = True
        for test_name, passed in test_results:
            if passed:
                fmt.print_success(f"{test_name}: 通过")
            else:
                fmt.print_error(f"{test_name}: 失败")
                all_passed = False

        if all_passed:
            fmt.print_header("🎉 所有集成测试通过！")
        else:
            fmt.print_header("❌ 部分测试失败")
            sys.exit(1)

    except Exception as e:
        fmt.print_error(f"测试过程中发生异常: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
    finally:
        cleanup(kafka_compose_file, filebeat_proc)

if __name__ == "__main__":
    main()