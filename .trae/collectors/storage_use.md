```markdown
# Log Analysis AI Assistant - 存储模块使用文档

## 1. 概述

本存储模块提供三个数据存储客户端实现：

- **ClickHouseClient**：负责将标准化日志批量写入 ClickHouse，支持条件查询与聚合分析。
- **KafkaClient**：封装 Kafka 生产者与消费者，用于日志在组件间的可靠传递。
- **ElasticsearchClient**：可选组件，提供日志全文检索与索引管理能力。

三者均遵循统一的配置字典初始化模式，便于集成到数据处理流水线中。

## 2. 环境要求与版本清单

| 组件                   | 最低版本 | 测试版本 | 用途                     |
| ---------------------- | -------- | -------- | ------------------------ |
| Python                 | 3.9      | 3.10.x   | 运行存储客户端           |
| clickhouse-connect     | 0.7.0    | 0.7.16   | ClickHouse 驱动          |
| kafka-python           | 2.3.0    | 2.3.0    | Kafka 客户端             |
| elasticsearch          | 8.0.0    | 8.17.0   | Elasticsearch 客户端     |
| Apache Kafka           | 3.0      | 3.7.0    | 消息队列                 |
| ClickHouse             | 22.0     | 24.x     | 列式数据库               |
| Elasticsearch          | 8.0      | 8.17.0   | 搜索引擎（可选）         |
| Docker / Docker Compose| 20.10    | 最新     | 测试环境容器编排         |

## 3. 安装依赖

在项目虚拟环境中执行：

```bash
pip install clickhouse-connect kafka-python elasticsearch
```

## 4. ClickHouseClient 接口与使用

**类路径**：`src.storage.clickhouse.ClickHouseClient`

### 4.1 初始化参数

| 参数       | 类型 | 必填 | 默认值      | 说明                         |
| ---------- | ---- | ---- | ----------- | ---------------------------- |
| `host`     | str  | 否   | `localhost` | ClickHouse 服务器地址        |
| `port`     | int  | 否   | `8123`      | HTTP 端口                    |
| `username` | str  | 否   | `default`   | 用户名                       |
| `password` | str  | 否   | `''`        | 密码                         |
| `database` | str  | 否   | `default`   | 默认数据库                   |
| `timeout`  | int  | 否   | `30`        | 请求超时（秒）               |
| `compress` | bool | 否   | `False`     | 是否启用压缩                 |

### 4.2 方法说明

| 方法                                                         | 说明                                                         |
| ------------------------------------------------------------ | ------------------------------------------------------------ |
| `connect() -> None`                                          | 建立与 ClickHouse 的连接，成功后将 `_connected` 置为 `True`。 |
| `insert_logs(table: str, logs: List[Dict]) -> int`           | 批量插入日志。返回实际插入的行数。要求日志字典的键与表字段一致。 |
| `query_logs(table: str, conditions: Dict = None, limit: int = 1000) -> List[Dict]` | 条件查询日志。`conditions` 为字段等值条件，如 `{'log_type': 'vpn'}`。 |
| `aggregate(table: str, metrics: List[str], group_by: List[str], conditions: Dict = None) -> List[Dict]` | 聚合查询。`metrics` 如 `['count()']`，`group_by` 为分组字段列表。 |
| `close() -> None`                                            | 关闭连接。                                                   |

### 4.3 使用示例

```python
from src.storage.clickhouse import ClickHouseClient

config = {
    'host': 'localhost',
    'port': 8123,
    'username': 'default',
    'password': 'clickhouse',
    'database': 'log_analysis'
}
client = ClickHouseClient(config)
client.connect()

# 批量插入日志（日志字典需包含表结构对应字段）
logs = [
    {'timestamp': '2026-04-01T10:00:00', 'log_type': 'vpn', 'source': '/var/log/vpn.log', 'message': 'login success', ...}
]
inserted = client.insert_logs('raw_logs', logs)

# 查询最近1000条vpn日志
results = client.query_logs('raw_logs', conditions={'log_type': 'vpn'}, limit=1000)

# 按日志类型统计数量
agg = client.aggregate('raw_logs', metrics=['count() AS cnt'], group_by=['log_type'])

client.close()
```

### 4.4 表结构要求

建议建表语句（与采集模块输出字段匹配）：

```sql
CREATE TABLE raw_logs (
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
PARTITION BY toYYYYMMDD(timestamp);
```

## 5. KafkaClient 接口与使用

**类路径**：`src.storage.kafka_client.KafkaClient`

### 5.1 初始化参数

| 参数                         | 类型 | 必填 | 默认值              | 说明                                 |
| ---------------------------- | ---- | ---- | ------------------- | ------------------------------------ |
| `bootstrap_servers`          | str  | 是   | -                   | Kafka broker 地址，逗号分隔          |
| `producer_acks`              | str  | 否   | `'all'`             | 生产者确认级别                       |
| `producer_retries`           | int  | 否   | `3`                 | 生产者重试次数                       |
| `consumer_group_id`          | str  | 否   | `'default_group'`   | 消费者组 ID                          |
| `consumer_auto_offset_reset` | str  | 否   | `'earliest'`        | offset 重置策略                       |
| `consumer_enable_auto_commit`| bool | 否   | `True`              | 是否自动提交 offset                   |
| 安全配置（可选）             | -    | -    | -                   | `security_protocol`, `sasl_*` 等      |

### 5.2 方法说明

| 方法                                                         | 说明                                                         |
| ------------------------------------------------------------ | ------------------------------------------------------------ |
| `connect_producer() -> None`                                 | 初始化 Kafka 生产者。                                        |
| `connect_consumer(topics: List[str], group_id: str = None) -> None` | 初始化 Kafka 消费者，订阅指定主题。                          |
| `send_message(topic: str, message: Dict, key: str = None, retries: int = 3) -> bool` | 发送单条消息。返回是否成功。                                 |
| `send_batch(topic: str, messages: List[Dict], keys: List[str] = None) -> int` | 批量发送消息。返回成功发送条数。                             |
| `consume(topics: List[str] = None, callback: Callable = None, max_messages: int = None) -> List[Dict]` | 消费消息。若提供 `callback`，则每条消息调用之；否则返回消息列表。 |
| `commit_offsets() -> None`                                   | 手动提交消费者偏移量（当 `enable_auto_commit=False` 时使用）。 |
| `close() -> None`                                            | 关闭生产者和消费者连接。                                     |

### 5.3 使用示例

**生产者**：

```python
from src.storage.kafka_client import KafkaClient

client = KafkaClient({'bootstrap_servers': 'localhost:9092'})
client.connect_producer()

msg = {'timestamp': '2026-04-01T10:00:00', 'message': 'test'}
client.send_message('logs_raw', msg)

batch = [msg, msg]
client.send_batch('logs_raw', batch)

client.close()
```

**消费者**：

```python
client = KafkaClient({
    'bootstrap_servers': 'localhost:9092',
    'consumer_group_id': 'my_group',
    'consumer_auto_offset_reset': 'earliest'
})
client.connect_consumer(['logs_raw'])

def handle_message(msg, raw_msg):
    print(f"Received: {msg}")

client.consume(callback=handle_message, max_messages=10)
client.close()
```

## 6. ElasticsearchClient 接口与使用（可选）

**类路径**：`src.storage.elasticsearch.ElasticsearchClient`

> 注：该客户端在当前测试流程中默认禁用，可按需启用。

### 6.1 初始化参数

| 参数        | 类型       | 必填 | 默认值               | 说明                     |
| ----------- | ---------- | ---- | -------------------- | ------------------------ |
| `hosts`     | List[str]  | 否   | `['localhost:9200']` | ES 节点地址列表          |
| `http_auth` | (user,pwd) | 否   | `None`               | HTTP 基本认证            |
| `use_ssl`   | bool       | 否   | `False`              | 是否启用 SSL             |
| `verify_certs` | bool    | 否   | `False`              | 是否验证证书             |

### 6.2 方法说明

| 方法                                                         | 说明                                                         |
| ------------------------------------------------------------ | ------------------------------------------------------------ |
| `connect() -> None`                                          | 建立连接，执行 ping 测试。                                   |
| `index_log(index: str, log_data: Dict, doc_id: str = None) -> bool` | 索引单条日志。返回是否成功。                                 |
| `index_bulk(index: str, logs: List[Dict]) -> int`            | 批量索引日志。返回成功数量。                                 |
| `search(index: str, query: Dict, size: int = 100) -> List[Dict]` | 执行 DSL 查询，返回 `_source` 列表。                         |
| `aggregate(index: str, aggs: Dict, query: Dict = None) -> Dict` | 执行聚合分析，返回聚合结果字典。                             |
| `close() -> None`                                            | 关闭连接。                                                   |

### 6.3 使用示例

```python
from src.storage.elasticsearch import ElasticsearchClient

client = ElasticsearchClient({'hosts': ['localhost:9200']})
client.connect()

log = {'@timestamp': '2026-04-01T10:00:00', 'message': 'test'}
client.index_log('test_logs', log)

# 搜索包含 "error" 的日志
query = {'query': {'match': {'message': 'error'}}}
results = client.search('test_logs', query, size=10)

client.close()
```

## 7. 测试脚本使用指南

存储模块提供了两个自动化测试脚本，位于 `tests/collectors/storage/` 目录。

### 7.1 storage_test.py —— 存储模块单元集成测试

**功能**：验证 ClickHouseClient 和 KafkaClient 的核心功能，自动启动所需的 Docker 容器（Kafka、ClickHouse）。

**运行前提**：
- Docker 已安装并可用。
- Python 依赖已安装（见第 3 节）。
- 项目根目录下执行，且拥有 Docker 操作权限。

**执行命令**：

```bash
cd /path/to/Log_Analysis_AI_Assistant
source venv/bin/activate
python tests/collectors/storage/storage_test.py
```

**测试内容**：
1. 检查系统依赖（Docker、Python 包）。
2. 启动 Kafka 与 ClickHouse 容器。
3. 创建测试 Topic 与表。
4. **Kafka 测试**：发送 5 条消息，并消费验证。
5. **ClickHouse 测试**：插入 10 条日志，执行条件查询和聚合查询。
6. 输出汇总结果，并清理所有容器。

**输出示例**：

```
============================================================
 存储模块集成测试
============================================================

[1] 检查系统依赖...
[OK] Docker 已安装
[OK] kafka-python 已安装
[OK] clickhouse-connect 已安装
[OK] 所有依赖检查通过

[2] 启动 Docker 服务 (Kafka + ClickHouse)...
[OK] Docker 容器已启动
[OK] Kafka 已就绪
[OK] ClickHouse 已就绪
...
============================================================
 测试结果汇总
============================================================
[OK] Kafka 客户端: 通过
[OK] ClickHouse 客户端: 通过
============================================================
 所有存储模块测试通过！
============================================================
```

### 7.2 collect_and_storage_test.py —— 采集到存储端到端测试

**功能**：验证完整数据流：**日志生成 → Filebeat → Kafka → 采集器消费 → ClickHouse 写入**。

**运行前提**：
- 已通过 `storage_test.py` 环境检查。
- Filebeat 已安装（参考 `collector_use.md` 第 4.1 节）。
- `tests/collectors/gen_vpn_logs.py` 脚本存在且可用。

**执行命令**：

```bash
python tests/collectors/storage/collect_and_storage_test.py
```

**测试流程**：
1. 检查 Docker、Filebeat 及 Python 依赖。
2. 启动 Kafka 与 ClickHouse 容器。
3. 调用 `gen_vpn_logs.py` 生成模拟 VPN 日志文件。
4. 动态生成 Filebeat 配置，并启动 Filebeat 进程采集日志发送到 Kafka。
5. 使用 `FilebeatCollector` 从 Kafka 消费日志，并批量写入 ClickHouse。
6. 查询 ClickHouse 验证数据落地。
7. 清理所有资源（容器、Filebeat 进程、临时文件）。

**输出示例**：

```
============================================================
 采集模块 + 存储模块端到端集成测试
============================================================

[1] 检查系统依赖...
[OK] Docker 已安装
[OK] Filebeat 已安装
[OK] kafka-python 已安装
[OK] clickhouse-connect 已安装
[OK] 所有依赖检查通过

[2] 启动 Docker 服务 (Kafka + ClickHouse)...
[OK] 容器已启动
[OK] Kafka 已就绪
[OK] ClickHouse 已就绪

[3] 生成 VPN 测试日志...
[OK] 生成 1 个日志文件，共 40 行

[4] 启动 Filebeat 服务...
[OK] Filebeat 已启动，PID: 12345

[5] 采集日志并存入 ClickHouse...
   [INFO] 采集器已启动，开始消费...
   [INFO] 收到第 1 条: vpn - Apr  1 10:00:00 vpn-server vpn[1234]: LOGIN user...
   ...
[OK] 成功写入 15 条日志
[OK] ClickHouse 查询验证通过，当前表中共 15 条记录

============================================================
 测试结果
============================================================
[OK] 端到端测试通过！日志已成功从 Filebeat 流转至 ClickHouse。
```

## 8. 常见问题

| 问题                                   | 解决方案                                                     |
| -------------------------------------- | ------------------------------------------------------------ |
| ClickHouse 连接失败 `Connection refused` | 确认 Docker 容器运行中：`docker ps | grep clickhouse`；检查端口映射。 |
| 插入 ClickHouse 时报 `DB::Exception: Table doesn't exist` | 运行测试脚本前会自动建表；手动建表请参考 4.4 节。              |
| Kafka 消费不到消息                     | 检查 topic 是否正确；消费者 `group_id` 若重复且 offset 已提交，可更换 `group_id` 或设置 `auto_offset_reset='earliest'`。 |
| Filebeat 发送失败 `kafka: message too large` | 调整 Filebeat 配置中的 `max_message_bytes` 或 Kafka broker 的 `message.max.bytes`。 |
| 测试脚本权限错误（如 `/var/run/docker.sock`） | 将用户加入 `docker` 组：`sudo usermod -aG docker $USER` 并重新登录。 |
| Elasticsearch 测试被跳过               | 默认已注释 ES 测试；如需启用，取消 `storage_test.py` 中相关注释并确保 ES 容器配置正确。 |

## 9. 注意事项

1. **密码安全**：测试脚本中使用了硬编码密码 `clickhouse`，生产环境请通过环境变量或配置文件管理敏感信息。
2. **资源占用**：两个测试脚本均会启动 Docker 容器，测试完成后会自动清理，若中途退出请手动执行 `docker compose down -v` 释放资源。
3. **端口冲突**：若本地已运行 Kafka（9092）或 ClickHouse（8123），请先停止对应服务或修改脚本中的端口映射。
4.**Elasticsearch**:该模块由于虚拟机配置原因启动过慢暂未测试
```