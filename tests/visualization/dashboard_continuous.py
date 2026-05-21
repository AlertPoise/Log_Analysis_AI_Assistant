#!/usr/bin/env python3
"""
持续采集 + 行为分析 + AI 分析 + 实时仪表板
所有配置从 .env 或 settings 读取，无需修改脚本源码
"""

import os
import sys
import time
import uuid
import socket
import subprocess
import threading
import signal
import json
import random
from pathlib import Path
from datetime import datetime
from typing import Optional, Dict, Any, List

# ---------- 自动定位项目根目录 ----------
def find_project_root(start_path: Path) -> Path:
    for parent in [start_path] + list(start_path.parents):
        if (parent / "src" / "parsers" / "__init__.py").exists():
            return parent
    raise RuntimeError("未找到项目根目录（包含 src/parsers/__init__.py）")

SCRIPT_DIR = Path(__file__).parent.resolve()
PROJECT_ROOT = find_project_root(SCRIPT_DIR)
sys.path.insert(0, str(PROJECT_ROOT))

# ---------- 导入配置和日志 ----------
from src.utils.config import settings
from src.utils.logger import get_logger

logger = get_logger(__name__)

# ---------- 从 settings 读取所有配置 ----------
KAFKA_BOOTSTRAP = settings.kafka_bootstrap_servers
KAFKA_TOPIC = settings.kafka_logs_topic
CLICKHOUSE_HOST = settings.clickhouse_host
CLICKHOUSE_PORT = settings.clickhouse_port
CLICKHOUSE_DATABASE = settings.clickhouse_database
CLICKHOUSE_USER = settings.clickhouse_user
CLICKHOUSE_PASSWORD = settings.clickhouse_password
CLICKHOUSE_TABLE = settings.clickhouse_table
STREAMLIT_PORT = settings.streamlit_server_port
STREAMLIT_ADDRESS = settings.streamlit_server_address

# 行为分析参数
ANALYSIS_WINDOW_MINUTES = settings.analysis_window_minutes
ANALYSIS_INTERVAL_SEC = settings.analysis_interval_sec
ANOMALY_THRESHOLD = settings.anomaly_threshold

# 日志生成速度
LOG_GEN_INTERVAL_SEC = settings.log_gen_interval_sec
LOGS_PER_BATCH = settings.logs_per_batch

# 临时目录
TEST_TMP = Path(settings.test_tmp_dir).expanduser().resolve()
MONITORED_LOGS_DIR = TEST_TMP / "monitored_logs"
FILEBEAT_DATA_DIR = Path(settings.filebeat_data_dir).expanduser().resolve()
FILEBEAT_LOGS_DIR = Path(settings.filebeat_logs_dir).expanduser().resolve()
FILEBEAT_CONFIG_DIR = Path(settings.filebeat_config_dir).expanduser().resolve()
COMPOSE_FILE = TEST_TMP / "docker-compose.yml"
DASHBOARD_PATH = PROJECT_ROOT / "src" / "visualization" / "dashboard.py"

# ---------- 导入 VPN 日志生成器 ----------
TESTS_COLLECTORS_DIR = PROJECT_ROOT / "tests" / "collectors"
GEN_LOGS_AVAILABLE = False
if TESTS_COLLECTORS_DIR.exists():
    sys.path.insert(0, str(TESTS_COLLECTORS_DIR))
    try:
        from gen_vpn_logs import (
            VPNLogEntry, USERS, VPN_PROTOCOLS, AUTH_METHODS, CLIENTS,
            VPN_GATEWAYS, FAIL_REASONS, ANOMALY_IPS, USER_USUAL_IPS,
            random_ip, ip_to_geo, gen_session_id, is_off_hours, is_unusual_ip
        )
        GEN_LOGS_AVAILABLE = True
        print("[✓] gen_vpn_logs 模块导入成功")
    except ImportError as e:
        print(f"[WARN] 无法导入 gen_vpn_logs: {e}")
else:
    print("[WARN] tests/collectors 目录不存在，无法生成日志")

# ---------- 辅助函数 ----------
class Fmt:
    @staticmethod
    def header(txt): print(f"\n{'='*60}\n {txt}\n{'='*60}", flush=True)
    @staticmethod
    def step(n, txt): print(f"\n[{n}] {txt}...", flush=True)
    @staticmethod
    def ok(txt): print(f"[✓] {txt}", flush=True)
    @staticmethod
    def err(txt): print(f"[✗] {txt}", flush=True)
    @staticmethod
    def info(txt): print(f" [i] {txt}", flush=True)

def check_prerequisites():
    Fmt.step(1, "检查系统依赖")
    try:
        subprocess.run(["sudo", "docker", "--version"], check=True, capture_output=True)
        Fmt.ok("Docker 已安装")
    except:
        Fmt.err("Docker 未安装或 sudo 权限不足")
        return False
    try:
        subprocess.run(["sudo", "filebeat", "version"], check=True, capture_output=True)
        Fmt.ok("Filebeat 已安装")
    except:
        Fmt.err("Filebeat 未安装")
        return False
    packages = [("kafka-python", "kafka"), ("clickhouse-connect", "clickhouse_connect"),
                ("streamlit", "streamlit"), ("openai", "openai")]
    missing = []
    for pkg_name, import_name in packages:
        try:
            __import__(import_name)
            Fmt.ok(f"{pkg_name} 已安装")
        except ImportError:
            missing.append(pkg_name)
            Fmt.err(f"{pkg_name} 未安装")
    if missing:
        Fmt.err(f"缺少包: {', '.join(missing)}")
        Fmt.info("请运行: pip install " + " ".join(missing))
        return False
    return True

def start_services():
    """启动 Kafka + ClickHouse 容器，并使用外部 clickhouse.sql 初始化表"""
    Fmt.step(2, "启动 Kafka + ClickHouse 容器")
    compose_content = f"""
services:
  kafka:
    image: apache/kafka:latest
    container_name: test_integration_kafka
    environment:
      KAFKA_NODE_ID: 1
      KAFKA_PROCESS_ROLES: broker,controller
      KAFKA_CONTROLLER_QUORUM_VOTERS: 1@localhost:9093
      KAFKA_LISTENERS: PLAINTEXT://0.0.0.0:9092,CONTROLLER://0.0.0.0:9093
      KAFKA_ADVERTISED_LISTENERS: PLAINTEXT://localhost:9092
      KAFKA_LISTENER_SECURITY_PROTOCOL_MAP: PLAINTEXT:PLAINTEXT,CONTROLLER:PLAINTEXT
      KAFKA_CONTROLLER_LISTENER_NAMES: CONTROLLER
      KAFKA_OFFSETS_TOPIC_REPLICATION_FACTOR: 1
      KAFKA_HEAP_OPTS: "-Xmx512m -Xms512m"
    ports:
      - "9092:9092"
  clickhouse:
    image: clickhouse/clickhouse-server:latest
    container_name: test_integration_clickhouse
    ports:
      - "{CLICKHOUSE_PORT}:8123"
      - "9000:9000"
    environment:
      CLICKHOUSE_DB: {CLICKHOUSE_DATABASE}
      CLICKHOUSE_USER: {CLICKHOUSE_USER}
      CLICKHOUSE_PASSWORD: {CLICKHOUSE_PASSWORD}
      CLICKHOUSE_DEFAULT_ACCESS_MANAGEMENT: 1
    ulimits:
      nofile:
        soft: 262144
        hard: 262144
"""
    TEST_TMP.mkdir(parents=True, exist_ok=True)
    with open(COMPOSE_FILE, 'w') as f:
        f.write(compose_content)

    # 清理端口占用
    for port in [8123, 9092]:
        subprocess.run(f"sudo lsof -t -i:{port} | xargs sudo kill -9",
                       shell=True, stderr=subprocess.DEVNULL, stdout=subprocess.DEVNULL)
        time.sleep(1)

    # 停止并删除旧容器
    subprocess.run(["sudo", "docker", "compose", "-f", str(COMPOSE_FILE), "down", "-v"],
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    subprocess.run(["sudo", "docker", "rm", "-f", "test_integration_kafka", "test_integration_clickhouse"],
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    # 启动新容器
    subprocess.run(["sudo", "docker", "compose", "-f", str(COMPOSE_FILE), "up", "-d"], check=True)

    # 等待 Kafka 端口就绪
    for i in range(30):
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(1)
        if sock.connect_ex(('localhost', 9092)) == 0:
            sock.close()
            break
        sock.close()
        time.sleep(2)
    else:
        subprocess.run(["sudo", "docker", "logs", "test_integration_kafka"])
        raise RuntimeError("Kafka 端口未就绪")
    Fmt.ok("Kafka 端口已监听")
    Fmt.info("等待 Kafka 完全启动...")
    time.sleep(15)

    # 创建 topic
    Fmt.info("创建 Kafka topic...")
    topic_cmd = [
        "sudo", "docker", "exec", "test_integration_kafka",
        "kafka-topics", "--create", "--topic", KAFKA_TOPIC,
        "--bootstrap-server", "localhost:9092",
        "--partitions", "1", "--replication-factor", "1"
    ]
    result = subprocess.run(topic_cmd, capture_output=True, text=True)
    if result.returncode != 0 and "already exists" not in result.stderr:
        topic_cmd2 = [
            "sudo", "docker", "exec", "test_integration_kafka",
            "/opt/kafka/bin/kafka-topics.sh", "--create", "--topic", KAFKA_TOPIC,
            "--bootstrap-server", "localhost:9092",
            "--partitions", "1", "--replication-factor", "1"
        ]
        subprocess.run(topic_cmd2, capture_output=True)
    Fmt.ok(f"Kafka topic '{KAFKA_TOPIC}' 已就绪")

    # 等待 ClickHouse
    import clickhouse_connect
    for i in range(60):
        try:
            ch_client = clickhouse_connect.get_client(
                host=CLICKHOUSE_HOST, port=CLICKHOUSE_PORT,
                username=CLICKHOUSE_USER, password=CLICKHOUSE_PASSWORD,
                connect_timeout=3
            )
            ch_client.command("SELECT 1")
            break
        except Exception as e:
            if i < 59:
                time.sleep(2)
            else:
                subprocess.run(["sudo", "docker", "logs", "test_integration_clickhouse"])
                raise RuntimeError(f"ClickHouse 启动超时: {e}")
    Fmt.ok("ClickHouse 已就绪")

    # 显式创建数据库（增加日志）
    Fmt.info(f"创建数据库: {CLICKHOUSE_DATABASE}")
    try:
        ch_client.command(f"CREATE DATABASE IF NOT EXISTS {CLICKHOUSE_DATABASE}")
        Fmt.ok(f"数据库 {CLICKHOUSE_DATABASE} 已就绪")
    except Exception as e:
        Fmt.err(f"创建数据库失败: {e}")
        raise
    ch_client.close()

    # 重新连接并指定数据库
    ch_client = clickhouse_connect.get_client(
        host=CLICKHOUSE_HOST, port=CLICKHOUSE_PORT,
        username=CLICKHOUSE_USER, password=CLICKHOUSE_PASSWORD,
        database=CLICKHOUSE_DATABASE, connect_timeout=3
    )

    # 执行外部 SQL 文件建表
    init_clickhouse_tables(ch_client)
    ch_client.close()
    Fmt.ok("所有 ClickHouse 表已就绪")

def init_clickhouse_tables(ch_client):
    """顺序执行 SQL 文件中的所有语句，遇错即停"""
    sql_file = PROJECT_ROOT / "config" / "clickhouse.sql"
    if not sql_file.exists():
        Fmt.err(f"找不到 SQL 文件: {sql_file}")
        return

    with open(sql_file, 'r', encoding='utf-8') as f:
        sql_content = f.read()

    # 替换占位符
    sql_content = sql_content.replace("{CLICKHOUSE_DATABASE}", CLICKHOUSE_DATABASE)
    sql_content = sql_content.replace("{CLICKHOUSE_TABLE}", CLICKHOUSE_TABLE)

    # 按分号分割
    statements = [s.strip() for s in sql_content.split(';') if s.strip()]

    Fmt.info(f"共解析到 {len(statements)} 条 SQL 语句")

    for idx, stmt in enumerate(statements, 1):
        if not stmt:
            continue
        # 跳过纯注释行（可选，但保留也无妨）
        if stmt.startswith('--'):
            Fmt.info(f"[{idx}] 跳过注释行")
            continue
        Fmt.info(f"执行 [{idx}/{len(statements)}]: {stmt[:80]}...")
        try:
            ch_client.command(stmt)
            Fmt.ok(f"[{idx}] 成功")
        except Exception as e:
            Fmt.err(f"[{idx}] 失败: {stmt[:200]}")
            Fmt.err(f"错误: {e}")
            raise

# ---------- 实时日志生成器 ----------
def continuous_log_generator(stop_event: threading.Event):
    if not GEN_LOGS_AVAILABLE:
        Fmt.err("无法生成日志：gen_vpn_logs 模块不可用")
        return

    MONITORED_LOGS_DIR.mkdir(parents=True, exist_ok=True)

    # 为每个用户预生成稳定行为
    user_profiles = {}
    for user in USERS:
        username = user["username"]
        usual_prefixes = USER_USUAL_IPS.get(username, ["192.168."])
        usual_ips = []
        for _ in range(2):
            prefix = random.choice(usual_prefixes)
            usual_ips.append(prefix + str(random.randint(1, 254)))
        _, city = ip_to_geo(usual_ips[0])
        h_start, h_end = user["usual_hours"]
        core_hour = random.randint(h_start, h_end - 1)
        user_profiles[username] = {
            "usual_ips": usual_ips,
            "usual_city": city,
            "usual_hour": core_hour,
            "usual_hours_range": (h_start, h_end)
        }

    anomaly_ratio = 0.05
    while not stop_event.is_set():
        lines = []
        for _ in range(LOGS_PER_BATCH):
            user = random.choice(USERS)
            username = user["username"]
            profile = user_profiles[username]
            is_anomaly = random.random() < anomaly_ratio

            dt = datetime.now()
            if not is_anomaly:
                hour = profile["usual_hour"] + random.randint(-2, 2)
                hour = max(profile["usual_hours_range"][0],
                           min(profile["usual_hours_range"][1] - 1, hour))
                dt = dt.replace(hour=hour, minute=random.randint(0, 59),
                                second=random.randint(0, 59))

            if is_anomaly:
                if random.random() < 0.5:
                    src_ip = random_ip(random.choice(ANOMALY_IPS))
                else:
                    src_ip = random_ip(random.choice(USER_USUAL_IPS.get(username, ["192.168."])))
                country, city = ip_to_geo(src_ip)
                if random.random() < 0.5:
                    event_type = "LOGIN_FAIL"
                    result = "FAIL"
                    fail_reason = random.choice(FAIL_REASONS)
                    risk_score = random.randint(40, 80)
                else:
                    event_type = "LOGIN_SUCCESS"
                    result = "SUCCESS"
                    fail_reason = None
                    risk_score = random.randint(50, 90)
            else:
                src_ip = random.choice(profile["usual_ips"])
                country, city = "中国", profile["usual_city"]
                event_type = "LOGIN_SUCCESS"
                result = "SUCCESS"
                fail_reason = None
                risk_score = 0

            off_hours = is_off_hours(dt, user)
            unusual_ip = is_unusual_ip(username, src_ip)

            log_entry = VPNLogEntry(
                timestamp=dt.strftime("%Y-%m-%d %H:%M:%S"),
                username=username,
                dept=user["dept"],
                role=user["role"],
                src_ip=src_ip,
                src_country=country,
                src_city=city,
                vpn_gateway=random.choice(VPN_GATEWAYS),
                dst_internal_ip="10.0.0.1",
                event_type=event_type,
                protocol=random.choice(VPN_PROTOCOLS),
                auth_method=random.choice(AUTH_METHODS),
                client_software=random.choice(CLIENTS),
                session_id=gen_session_id(),
                result=result,
                fail_reason=fail_reason,
                session_duration_sec=None,
                bytes_sent=None,
                bytes_recv=None,
                is_off_hours=off_hours,
                is_unusual_ip=unusual_ip,
                risk_score=risk_score,
                risk_tags="异常" if is_anomaly else "正常"
            )
            d = log_entry.__dict__
            line = (f"{d['timestamp']} {d['vpn_gateway']} vpnd: "
                    f"event={d['event_type']} user={d['username']} dept={d['dept']} "
                    f"src_ip={d['src_ip']} src_geo={d['src_country']}/{d['src_city']} "
                    f"proto={d['protocol']} auth={d['auth_method']} "
                    f"client=\"{d['client_software']}\" session={d['session_id']} "
                    f"result={d['result']}")
            if d['fail_reason']:
                line += f" reason={d['fail_reason']}"
            line += f" risk_score={d['risk_score']} risk_tags=\"{d['risk_tags']}\"\n"
            lines.append(line)

        filename = f"vpn_batch_{int(time.time()*1000)}.log"
        filepath = MONITORED_LOGS_DIR / filename
        with open(filepath, 'w', encoding='utf-8') as f:
            f.writelines(lines)
        time.sleep(LOG_GEN_INTERVAL_SEC)

# ---------- Filebeat 启动 ----------
def start_filebeat() -> subprocess.Popen:
    Fmt.step(3, "启动 Filebeat")
    subprocess.run(["sudo", "pkill", "-f", "filebeat"],
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    time.sleep(2)

    for d in [FILEBEAT_DATA_DIR, FILEBEAT_LOGS_DIR, FILEBEAT_CONFIG_DIR]:
        if d.exists():
            subprocess.run(["sudo", "rm", "-rf", str(d)], check=False)
        d.mkdir(parents=True)

    config_path = FILEBEAT_CONFIG_DIR / "filebeat.yml"
    config_content = f"""
filebeat.inputs:
- type: log
  enabled: true
  paths:
    - {MONITORED_LOGS_DIR}/*.log
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

logging.level: warning
logging.to_files: true
logging.files:
  path: {FILEBEAT_LOGS_DIR}
  name: filebeat
  keepfiles: 7
"""
    with open(config_path, 'w') as f:
        f.write(config_content)

    cmd = [
        "sudo", "filebeat", "-e",
        "-c", str(config_path.absolute()),
        "--path.data", str(FILEBEAT_DATA_DIR),
        "--path.logs", str(FILEBEAT_LOGS_DIR),
        "--strict.perms=false",
    ]
    proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    Fmt.ok(f"Filebeat 已启动 PID={proc.pid}")
    time.sleep(10)
    return proc

# ---------- Kafka 消费者 + 解析 + 写入 ClickHouse ----------
def continuous_ingester(stop_event: threading.Event):
    from kafka import KafkaConsumer
    import clickhouse_connect
    from src.parsers.parsers import RegexParser, PREDEFINED_PATTERNS

    consumer = KafkaConsumer(
        KAFKA_TOPIC,
        bootstrap_servers=KAFKA_BOOTSTRAP,
        group_id=f"continuous_group_{uuid.uuid4().hex[:8]}",
        auto_offset_reset='earliest',
        enable_auto_commit=True,
        value_deserializer=lambda m: m.decode('utf-8')
    )
    Fmt.info(f"Kafka 消费者已启动 group_id={consumer.config['group_id']}")

    ch_client = clickhouse_connect.get_client(
        host=CLICKHOUSE_HOST, port=CLICKHOUSE_PORT,
        username=CLICKHOUSE_USER, password=CLICKHOUSE_PASSWORD,
        database=CLICKHOUSE_DATABASE
    )

    # 初始化 VPN 解析器
    vpn_config = PREDEFINED_PATTERNS['vpn_gateway']
    vpn_parser = RegexParser(name='vpn_gateway', config={
        'pattern': vpn_config['regex'],
        'log_type': vpn_config['log_type'],
    })

    buffer = []
    buffer_max_size = 50
    buffer_timeout = 5.0
    last_flush = time.time()

    def flush_buffer():
        nonlocal buffer, last_flush
        if not buffer:
            return
        columns = [
            'timestamp', 'log_type', 'username', 'dept', 'role', 'action',
            'event_type', 'result', 'fail_reason', 'source_ip', 'destination_ip',
            'vpn_gateway', 'src_country', 'src_city', 'protocol', 'auth_method',
            'client_software', 'session_id', 'is_off_hours', 'is_unusual_ip',
            'session_duration_sec', 'bytes_sent', 'bytes_recv', 'risk_score',
            'risk_tags', 'raw_log', 'parser', 'parse_status', 'collected_at'
        ]
        rows = []
        for log in buffer:
            row = []
            for col in columns:
                val = log.get(col)
                if val is None or val == '':
                    if col in ('timestamp', 'collected_at'):
                        val = datetime.now()
                    elif col in ('bytes_sent', 'bytes_recv', 'session_duration_sec'):
                        val = 0
                    elif col == 'risk_score':
                        val = 0
                    elif col in ('is_off_hours', 'is_unusual_ip'):
                        val = False
                    else:
                        val = ''
                else:
                    if col in ('timestamp', 'collected_at'):
                        if isinstance(val, str):
                            try:
                                val = datetime.fromisoformat(val.replace('Z', '+00:00'))
                            except:
                                val = datetime.now()
                    elif col in ('session_duration_sec', 'bytes_sent', 'bytes_recv', 'risk_score'):
                        try:
                            val = int(val)
                        except:
                            val = 0
                    elif col in ('is_off_hours', 'is_unusual_ip'):
                        val = bool(val)
                    else:
                        val = str(val)
                row.append(val)
            rows.append(row)
        try:
            ch_client.insert(CLICKHOUSE_TABLE, rows, column_names=columns)
            Fmt.info(f"写入 {len(buffer)} 条日志到 ClickHouse")
        except Exception as e:
            Fmt.err(f"写入失败: {e}")
        buffer = []
        last_flush = time.time()

    for msg in consumer:
        if stop_event.is_set():
            break
        raw = msg.value
        try:
            data = json.loads(raw)
            raw_log = data.get('message', raw)
        except:
            raw_log = raw

        parsed = vpn_parser.parse(raw_log)
        if not parsed:
            continue

        parsed['raw_log'] = raw_log
        parsed['collected_at'] = datetime.now()
        parsed['parser'] = 'vpn_regex'
        parsed['parse_status'] = 'success'
        if 'log_type' not in parsed:
            parsed['log_type'] = 'vpn'
        buffer.append(parsed)

        if len(buffer) >= buffer_max_size:
            flush_buffer()
        elif time.time() - last_flush > buffer_timeout:
            flush_buffer()

    flush_buffer()
    consumer.close()
    ch_client.close()
    Fmt.info("持续消费线程退出")

# ---------- 行为分析 + AI 分析线程 ----------
def behavior_ai_analyzer(stop_event: threading.Event):
    import clickhouse_connect
    from src.behavior.service import BehaviorAnalysisService
    from src.ai.analyzer import AIAnalyzer

    ai_analyzer = None
    try:
        ai_config = settings.current_ai_config
        if ai_config and ai_config.get('api_key'):
            ai_analyzer = AIAnalyzer(
                api_key=ai_config['api_key'],
                platform=ai_config['platform'],
                model=ai_config.get('model'),
                base_url=ai_config.get('base_url')
            )
            Fmt.ok(f"AI 分析器初始化成功，平台: {ai_config['platform']}")
        else:
            Fmt.err("未配置 AI API Key，AI 分析将被跳过")
    except Exception as e:
        Fmt.err(f"AI 分析器初始化失败: {e}")

    service = BehaviorAnalysisService()
    try:
        ch_client = clickhouse_connect.get_client(
            host=CLICKHOUSE_HOST, port=CLICKHOUSE_PORT,
            username=CLICKHOUSE_USER, password=CLICKHOUSE_PASSWORD,
            database=CLICKHOUSE_DATABASE, connect_timeout=10
        )
        Fmt.ok("行为分析线程 ClickHouse 连接成功")
    except Exception as e:
        Fmt.err(f"行为分析线程 ClickHouse 连接失败: {e}")
        return

    Fmt.info(f"行为分析线程已启动，检测窗口: {ANALYSIS_WINDOW_MINUTES} 分钟，间隔: {ANALYSIS_INTERVAL_SEC} 秒")
    while not stop_event.is_set():
        time.sleep(ANALYSIS_INTERVAL_SEC)
        query = f"""
        SELECT timestamp, log_type, username, action, source_ip, src_city,
               dept, role, event_type, result, fail_reason, protocol, auth_method,
               client_software, session_id, is_off_hours, is_unusual_ip,
               session_duration_sec, bytes_sent, bytes_recv, risk_score
        FROM {CLICKHOUSE_TABLE}
        WHERE timestamp >= now() - INTERVAL {ANALYSIS_WINDOW_MINUTES} MINUTE
        ORDER BY timestamp
        """
        try:
            result = ch_client.query(query)
            logs = result.result_rows
            if not logs:
                continue
            columns = result.column_names
            log_dicts = [dict(zip(columns, row)) for row in logs]
            users = set(log['username'] for log in log_dicts if log.get('username'))
            for username in users:
                user_logs = [log for log in log_dicts if log['username'] == username]
                history_query = f"""
                SELECT * FROM {CLICKHOUSE_TABLE}
                WHERE username = %(user)s AND timestamp >= now() - INTERVAL 24 HOUR
                ORDER BY timestamp DESC LIMIT 1000
                """
                history_result = ch_client.query(history_query, parameters={'user': username})
                history_logs = [dict(zip(history_result.column_names, row)) for row in history_result.result_rows]
                try:
                    analysis = service.analyze_user(username, history_logs, user_logs)
                    anomalies = analysis.get('anomalies', [])
                    for anomaly in anomalies:
                        anomaly_id = insert_anomaly(ch_client, anomaly)
                        anomaly_score = anomaly.get('anomaly_score', 0)
                        if ai_analyzer and anomaly_score >= ANOMALY_THRESHOLD:
                            try:
                                description = anomaly.get('description', '')
                                related_log_ids = anomaly.get('related_logs', [])
                                log_context = ""
                                if related_log_ids:
                                    ids_str = ','.join(str(i) for i in related_log_ids)
                                    # 注意：表中有 raw_log 列，不是 raw_message
                                    context_query = f"SELECT raw_log FROM {CLICKHOUSE_TABLE} WHERE id IN ({ids_str})"
                                    context_res = ch_client.query(context_query)
                                    log_context = "\n".join(row[0] for row in context_res.result_rows if row[0])
                                ai_result = ai_analyzer.analyze_anomaly(
                                    username=username,
                                    anomaly_description=description,
                                    log_context=log_context
                                )
                                update_anomaly_with_ai(ch_client, anomaly_id, ai_result)
                                Fmt.info(f"AI 分析成功: 用户={username}, 威胁类型={ai_result.get('threat_type')}")
                            except Exception as e:
                                logger.error(f"AI 分析失败 (用户 {username}): {e}")
                except Exception as e:
                    logger.error(f"行为分析服务出错 (用户 {username}): {e}")
        except Exception as e:
            logger.error(f"行为分析线程查询出错: {e}")

    ch_client.close()
    Fmt.info("行为分析线程已退出")

def insert_anomaly(ch_client, anomaly: dict) -> int:
    import uuid
    anomaly_id = abs(hash(f"{anomaly['username']}_{anomaly['timestamp']}_{uuid.uuid4()}")) % (2**63)
    insert_sql = """
    INSERT INTO anomaly_detection (id, detection_time, username, anomaly_type, anomaly_score,
                                   risk_level, description, context, related_events, is_processed)
    VALUES (%(id)s, now(), %(username)s, %(anomaly_type)s, %(anomaly_score)s,
            %(risk_level)s, %(description)s, %(context)s, %(related_events)s, false)
    """
    ch_client.command(insert_sql, parameters={
        'id': anomaly_id,
        'username': anomaly['username'],
        'anomaly_type': anomaly['anomaly_type'],
        'anomaly_score': anomaly['anomaly_score'],
        'risk_level': anomaly['risk_level'],
        'description': anomaly['description'],
        'context': json.dumps(anomaly.get('context', {})),
        'related_events': anomaly.get('related_logs', [])
    })
    return anomaly_id

def update_anomaly_with_ai(ch_client, anomaly_id: int, ai_result: dict):
    update_sql = """
    ALTER TABLE anomaly_detection UPDATE
        threat_type = %(threat_type)s,
        ai_analysis = %(ai_analysis)s,
        `处置建议` = %(suggestion)s,
        is_processed = true,
        processed_at = now()
    WHERE id = %(id)s
    """
    ch_client.command(update_sql, parameters={
        'id': anomaly_id,
        'threat_type': ai_result.get('threat_type', 'UNKNOWN'),
        'ai_analysis': ai_result.get('description', ''),
        'suggestion': ai_result.get('suggestion', '')
    })

# ---------- Streamlit 启动 ----------
def start_streamlit() -> Optional[subprocess.Popen]:
    Fmt.step(5, "启动 Streamlit 仪表板")
    if not DASHBOARD_PATH.exists():
        Fmt.err(f"仪表板文件未找到: {DASHBOARD_PATH}")
        return None
    cmd = [
        sys.executable, "-m", "streamlit", "run",
        str(DASHBOARD_PATH),
        "--server.port", str(STREAMLIT_PORT),
        "--server.address", STREAMLIT_ADDRESS,
        "--browser.serverAddress", STREAMLIT_ADDRESS
    ]
    proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    Fmt.ok(f"Streamlit 已启动 PID={proc.pid}")
    time.sleep(5)
    print("\n" + "="*60, flush=True)
    print(" ✅ 持续采集系统已就绪！", flush=True)
    print(f" 🌐 访问仪表板: http://{STREAMLIT_ADDRESS}:{STREAMLIT_PORT}", flush=True)
    print(" 📡 日志持续生成并写入 ClickHouse", flush=True)
    print(" 🤖 行为分析和 AI 分析已启动", flush=True)
    print(" 🔴 按 Ctrl+C 停止所有组件", flush=True)
    print("="*60 + "\n", flush=True)
    return proc

# ---------- 清理 ----------
def cleanup(filebeat_proc, streamlit_proc, stop_event):
    Fmt.step(6, "清理测试环境")
    stop_event.set()
    if filebeat_proc:
        filebeat_proc.terminate()
        try:
            filebeat_proc.wait(timeout=5)
        except:
            filebeat_proc.kill()
        subprocess.run(["sudo", "pkill", "-9", "-f", "filebeat"], stdout=subprocess.DEVNULL)
        Fmt.ok("Filebeat 已停止")
    if streamlit_proc:
        streamlit_proc.terminate()
        try:
            streamlit_proc.wait(timeout=5)
        except:
            streamlit_proc.kill()
        Fmt.ok("Streamlit 已停止")
    if COMPOSE_FILE.exists():
        subprocess.run(["sudo", "docker", "compose", "-f", str(COMPOSE_FILE), "down", "-v"],
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        COMPOSE_FILE.unlink(missing_ok=True)
        Fmt.ok("Docker 容器已清理")
    if TEST_TMP.exists():
        subprocess.run(["sudo", "rm", "-rf", str(TEST_TMP)], check=False)
        Fmt.ok("临时文件已清理")

# ---------- 主函数 ----------
def main():
    Fmt.header("完整版日志分析 AI 助手（高速生成，真实用户）")
    if not check_prerequisites():
        sys.exit(1)

    stop_event = threading.Event()
    filebeat_proc = None
    streamlit_proc = None

    def signal_handler(sig, frame):
        print("\n[!] 收到中断信号，正在清理...", flush=True)
        cleanup(filebeat_proc, streamlit_proc, stop_event)
        sys.exit(0)

    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)

    try:
        start_services()
        MONITORED_LOGS_DIR.mkdir(parents=True, exist_ok=True)

        gen_thread = threading.Thread(target=continuous_log_generator, args=(stop_event,), daemon=True)
        gen_thread.start()

        filebeat_proc = start_filebeat()

        ingest_thread = threading.Thread(target=continuous_ingester, args=(stop_event,), daemon=True)
        ingest_thread.start()

        analysis_thread = threading.Thread(target=behavior_ai_analyzer, args=(stop_event,), daemon=True)
        analysis_thread.start()

        streamlit_proc = start_streamlit()
        if not streamlit_proc:
            cleanup(filebeat_proc, streamlit_proc, stop_event)
            sys.exit(1)

        while True:
            time.sleep(1)
            if filebeat_proc.poll() is not None:
                Fmt.err("Filebeat 进程意外退出")
                break
            if streamlit_proc.poll() is not None:
                Fmt.err("Streamlit 进程意外退出")
                break
    except Exception as e:
        Fmt.err(f"系统异常: {e}")
        import traceback
        traceback.print_exc()
    finally:
        cleanup(filebeat_proc, streamlit_proc, stop_event)

if __name__ == "__main__":
    main()