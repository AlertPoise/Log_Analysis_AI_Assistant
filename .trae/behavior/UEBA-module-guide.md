# UEBA Module Guide

> 本文档用于指导本项目中 UEBA（User and Entity Behavior Analytics，用户行为分析）模块的开发。
> 当前目标不是实现完整实时 UEBA 系统，而是先完成一个**最小可用、可解释、可扩展、能处理十万级日志数据的一次性用户行为基线构建模块**。

---

## 1. 当前版本定位

当前 UEBA 模块的第一版定位为：

```text
离线一次性用户行为基线构建模块
```

也就是说，本版本只做：

```text
从结构化日志数据库中读取指定时间范围内的日志
    ↓
在数据库侧完成主要聚合统计
    ↓
Python 侧合并聚合结果
    ↓
生成每个用户的行为基线
    ↓
将用户基线写入数据库
    ↓
供后续异常检测、AI 分析、前端展示使用
```

当前版本暂时不做：

```text
不做 Kafka 实时消费
不做实时基线更新
不做滑动窗口增量更新
不做复杂机器学习
不做深度用户画像
不做全流程告警
不直接读取十万级原始日志到 Python 内存
不保存每个用户的完整历史日志列表
```

本版本的关键目标是：

```text
1. 能从结构化日志表中读取十万级以上日志对应的聚合结果
2. 能按用户生成正常行为基线
3. 能将基线稳定写入数据库
4. 能避免 Python 侧被大数据量冲烂
5. 能为后续异常评分、AI 分析、前端展示留下稳定接口
```

---

## 2. UEBA 模块在项目中的位置

UEBA 不负责日志采集，也不负责原始日志解析。它应该位于结构化日志之后。

```text
日志采集模块
    ↓
日志结构化处理模块
    ↓
结构化日志表 logs_structured
    ↓
UEBA 用户行为建模模块
    ↓
用户行为基线表 user_behavior_baselines
    ↓
AI 异常分析 / 可视化 / 告警模块
```

UEBA 模块的输入是：

```text
结构化日志表中的标准化日志数据
```

UEBA 模块的输出是：

```text
每个用户的一条或多条行为基线记录
```

后续异常检测模块可以使用该基线判断：

```text
该用户是否在非常用时间出现
该用户是否使用了非常用 IP
该用户是否来自非常用地区
该用户是否访问了非常用接口
该用户失败率是否异常
该用户访问频率是否明显偏离历史行为
```

---

## 3. 第一版核心设计原则

### 3.1 数据库侧聚合优先

第一版要处理至少十万级日志，因此不要在 Python 中一次性读取所有原始日志。

错误方向：

```python
logs = db.query("SELECT * FROM logs_structured")
for log in logs:
    process(log)
```

正确方向：

```text
ClickHouse / 数据库先 GROUP BY 聚合
Python 只拿聚合后的结果
Python 负责合并和生成基线
```

数据库更适合做大规模扫描、分组、计数、Top-N 统计。Python 侧只应该处理压缩后的聚合结果。

---

### 3.2 不保存原始日志明细

UEBA 基线构建过程中，不要为每个用户保存完整日志列表。

错误设计：

```python
user_logs[username].append(log)
```

正确设计：

```python
user_features[username].sample_count += 1
user_features[username].hour_counter[hour] += 1
user_features[username].ip_counter[ip] += 1
user_features[username].endpoint_counter[endpoint] += 1
```

第一版采用数据库侧聚合后，Python 侧甚至不需要逐条处理日志，只需要处理聚合结果。

---

### 3.3 每个维度单独聚合，不写巨型 SQL

不要为了减少查询次数写一条巨大 SQL。

建议拆成多个简单、清晰、可调试的聚合查询：

```text
1. 用户总览统计
2. 用户小时分布统计
3. 用户常用 IP Top-N
4. 用户常用地区 Top-N
5. 用户常用接口 Top-N
6. 用户行为类型分布
7. 用户状态分布
8. 用户每日事件数统计
```

每个 SQL 只负责一个维度，最终在 Python 侧按 `username` 合并。

---

### 3.4 配置模块只是临时默认配置，不是长期最终形态

当前可以保留 `config.py`，但必须明确：

```text
config.py 只是第一版默认配置来源
后续配置应该能从外部接口、配置表、前端页面、环境变量传入
```

因此不要把代码写成强依赖全局常量的形式。

不推荐：

```python
from src.behavior.config import READ_BATCH_SIZE
from src.behavior.config import TOP_IP_LIMIT
```

推荐：

```python
config = UebaBaselineConfig(
    top_ip_limit=10,
    top_location_limit=10,
    top_endpoint_limit=20,
)
service = UebaService(repository, baseline_store, config)
```

这样后续暴露接口时，只需要把外部传入 JSON 转成配置对象即可。

---

### 3.5 复杂字段先用 JSON 字符串存储

第一版不要过度设计复杂嵌套表结构。

例如以下字段可以先以 JSON 字符串存入数据库：

```text
common_active_hours
common_ips
common_locations
common_endpoints
action_distribution
status_distribution
baseline_json
```

这样后期基线结构变化时，不需要频繁修改表结构。

---

## 4. 建议目录结构

建议在 behavior 模块目录下实现 UEBA 行为基线能力：

```text
src/behavior/
├── __init__.py
├── config.py
├── schemas.py
├── repository.py
├── aggregate_merger.py
├── baseline_builder.py
├── baseline_store.py
└── service.py
```

可选脚本目录：

```text
scripts/
└── build_ueba_baseline.py
```

各文件职责如下：

| 文件                               | 职责                     |
| -------------------------------- | ---------------------- |
| `config.py`                      | 提供第一版默认配置，并定义配置对象      |
| `schemas.py`                     | 定义 UEBA 内部稳定数据结构       |
| `repository.py`                  | 从结构化日志表读取数据库侧聚合结果      |
| `aggregate_merger.py`            | 将多个 SQL 聚合结果按用户合并成用户特征 |
| `baseline_builder.py`            | 根据用户特征生成用户行为基线         |
| `baseline_store.py`              | 将基线批量写入数据库，并提供基线查询能力   |
| `service.py`                     | 对外提供 UEBA 模块统一调用入口     |
| `scripts/build_ueba_baseline.py` | 命令行触发一次性基线构建           |

---

## 5. `config.py` 详细设计

### 5.1 模块职责

`config.py` 用于保存当前版本的默认参数。

但要注意，它不是长期最终配置中心。后期这些参数应该可以通过接口传入。

因此这里应定义配置数据类，而不是只定义一堆全局常量。

---

### 5.2 推荐配置对象

```python
from dataclasses import dataclass


@dataclass
class UebaBaselineConfig:
    baseline_window_days: int = 30
    min_sample_count: int = 20

    top_ip_limit: int = 10
    top_location_limit: int = 10
    top_endpoint_limit: int = 20
    top_action_limit: int = 20
    top_status_limit: int = 20

    common_hour_min_ratio: float = 0.05
    common_ip_min_ratio: float = 0.03
    common_location_min_ratio: float = 0.03
    common_endpoint_min_ratio: float = 0.02

    endpoint_normalize: bool = True
    model_version: str = "ueba_baseline_v1"

    write_batch_size: int = 1000
```

---

### 5.3 参数说明

| 参数                          | 含义                  |
| --------------------------- | ------------------- |
| `baseline_window_days`      | 默认使用最近多少天日志生成基线     |
| `min_sample_count`          | 用户日志数低于该值时认为基线不可靠   |
| `top_ip_limit`              | 每个用户最多保存多少个常用 IP    |
| `top_location_limit`        | 每个用户最多保存多少个常用地区     |
| `top_endpoint_limit`        | 每个用户最多保存多少个常用接口     |
| `top_action_limit`          | 每个用户最多保存多少个行为类型     |
| `top_status_limit`          | 每个用户最多保存多少个状态类型     |
| `common_hour_min_ratio`     | 某小时占比达到多少才算常用活跃小时   |
| `common_ip_min_ratio`       | 某 IP 占比达到多少才算常用 IP  |
| `common_location_min_ratio` | 某地区占比达到多少才算常用地区     |
| `common_endpoint_min_ratio` | 某接口占比达到多少才算常用接口     |
| `endpoint_normalize`        | 是否对 endpoint 去参数归一化 |
| `model_version`             | 当前基线模型版本            |
| `write_batch_size`          | 基线写入数据库时的批量大小       |

---

### 5.4 后期配置暴露方式

后期可以提供接口：

```text
POST /api/behavior/baseline/build
```

请求体示例：

```json
{
  "start_time": "2026-05-01 00:00:00",
  "end_time": "2026-05-19 00:00:00",
  "config": {
    "min_sample_count": 50,
    "top_ip_limit": 10,
    "top_endpoint_limit": 20,
    "common_hour_min_ratio": 0.05,
    "model_version": "ueba_baseline_v1"
  }
}
```

接口层将 JSON 转成 `UebaBaselineConfig` 后传入 `UebaService`。

---

## 6. `schemas.py` 详细设计

### 6.1 模块职责

`schemas.py` 定义 UEBA 内部稳定数据结构。

数据库字段可以变，但 UEBA 内部结构应尽量稳定。

推荐数据流：

```text
数据库聚合结果
    ↓
UserAggregateFeature
    ↓
UserBaseline
    ↓
数据库基线表记录
```

---

### 6.2 聚合项结构

用于保存某个维度的 Top-N 项。

```python
from dataclasses import dataclass, field
from datetime import datetime, date
from typing import Any


@dataclass
class CountRatioItem:
    value: str | int
    count: int
    ratio: float
```

适用场景：

```text
常用小时
常用 IP
常用地区
常用接口
```

---

### 6.3 用户聚合特征结构

```python
@dataclass
class UserAggregateFeature:
    username: str
    sample_count: int = 0
    failed_count: int = 0
    active_days: int = 0
    first_seen: datetime | None = None
    last_seen: datetime | None = None

    hour_counts: dict[int, int] = field(default_factory=dict)
    ip_counts: dict[str, int] = field(default_factory=dict)
    location_counts: dict[str, int] = field(default_factory=dict)
    endpoint_counts: dict[str, int] = field(default_factory=dict)
    action_counts: dict[str, int] = field(default_factory=dict)
    status_counts: dict[str, int] = field(default_factory=dict)
    daily_counts: dict[str, int] = field(default_factory=dict)
```

注意：

```text
这里存的是聚合计数，不是原始日志。
```

---

### 6.4 用户基线结构

```python
@dataclass
class UserBaseline:
    username: str
    sample_count: int
    is_reliable: bool

    common_active_hours: list[CountRatioItem]
    common_ips: list[CountRatioItem]
    common_locations: list[CountRatioItem]
    common_endpoints: list[CountRatioItem]

    action_distribution: dict[str, float]
    status_distribution: dict[str, float]

    failed_rate: float
    avg_daily_events: float
    active_day_avg_events: float
    max_daily_events: int

    baseline_start_time: datetime
    baseline_end_time: datetime
    model_version: str
```

---

### 6.5 基线构建结果结构

用于 `service.py` 返回本次构建统计。

```python
@dataclass
class BaselineBuildResult:
    success: bool
    baseline_start_time: datetime
    baseline_end_time: datetime
    total_user_count: int
    reliable_user_count: int
    unreliable_user_count: int
    total_log_count: int
    model_version: str
    duration_seconds: float
    message: str = ""
```

---

## 7. `repository.py` 详细设计

### 7.1 模块职责

`repository.py` 负责访问结构化日志表，执行数据库侧聚合 SQL。

它不生成基线，不做业务判断，只负责取数。

它应该提供的是聚合查询函数，而不是原始日志读取函数。

---

### 7.2 假设结构化日志表

第一版假设结构化日志表名为：

```text
logs_structured
```

核心字段为：

```text
timestamp   DateTime
username    String
source_ip   String
location    String
action      String
endpoint    String
status      String
```

如果实际项目中的字段名不同，应在 `repository.py` 中做适配。

例如实际字段叫：

```text
user_name
src_ip
api_path
result
```

则 SQL 中统一别名为：

```sql
SELECT
    user_name AS username,
    src_ip AS source_ip,
    api_path AS endpoint,
    result AS status
FROM logs_structured
```

不要让 `baseline_builder.py`、`aggregate_merger.py` 直接感知数据库字段差异。

---

### 7.3 统一时间过滤条件

所有聚合 SQL 都必须使用相同时间窗口。

ClickHouse 推荐使用：

```sql
PREWHERE timestamp >= {start_time:DateTime}
    AND timestamp < {end_time:DateTime}
WHERE username != ''
```

这里的 `{start_time:DateTime}`、`{end_time:DateTime}`、`{limit:UInt32}` 只表示逻辑参数。

实际代码实现必须以项目现有 ClickHouse 封装为准。当前项目使用 clickhouse_connect / ClickHouseClient 的 `client.query(query, parameters=params)` 参数化查询方式时，应采用该客户端支持的参数写法。

重点是：

```text
必须参数化，不要通过字符串拼接把 start_time、end_time、limit 等用户输入直接拼进 SQL。
Repository 层负责封装 SQL 参数，不允许上层模块拼 SQL。
```

---

### 7.4 用户总览统计

用途：生成每个用户的基础统计骨架。

SQL：

```sql
SELECT
    username,
    count() AS sample_count,
    min(timestamp) AS first_seen,
    max(timestamp) AS last_seen,
    countIf(lower(status) IN ('failed', 'fail', 'error', 'failure')) AS failed_count,
    uniqExact(toDate(timestamp)) AS active_days
FROM logs_structured
PREWHERE timestamp >= {start_time:DateTime}
    AND timestamp < {end_time:DateTime}
WHERE username != ''
GROUP BY username;
```

返回示例：

```text
username    sample_count    failed_count    active_days
zhangsan    12000           320             27
lisi        8500            110             25
```

Repository 接口：

```python
def fetch_user_summary(self, start_time, end_time) -> list[dict]:
    ...
```

---

### 7.5 用户小时分布统计

用途：统计用户在哪些小时段活跃。

SQL：

```sql
SELECT
    username,
    toHour(timestamp) AS active_hour,
    count() AS cnt
FROM logs_structured
PREWHERE timestamp >= {start_time:DateTime}
    AND timestamp < {end_time:DateTime}
WHERE username != ''
GROUP BY
    username,
    active_hour
ORDER BY
    username,
    active_hour;
```

说明：

```text
第一版建议称为 active_hours，而不是 login_hours。
```

原因是当前结构化日志中不一定只有登录行为。除非 action 能稳定区分登录事件，否则不要把所有日志小时分布都称为登录小时分布。

Repository 接口：

```python
def fetch_hour_distribution(self, start_time, end_time) -> list[dict]:
    ...
```

---

### 7.6 用户常用 IP Top-N

用途：统计每个用户最常见的来源 IP。

SQL：

```sql
SELECT
    username,
    source_ip,
    cnt
FROM
(
    SELECT
        username,
        source_ip,
        count() AS cnt
    FROM logs_structured
    PREWHERE timestamp >= {start_time:DateTime}
        AND timestamp < {end_time:DateTime}
    WHERE username != ''
      AND source_ip != ''
    GROUP BY
        username,
        source_ip
    ORDER BY
        username ASC,
        cnt DESC
)
LIMIT {limit:UInt32} BY username;
```

Repository 接口：

```python
def fetch_top_ips(self, start_time, end_time, limit: int) -> list[dict]:
    ...
```

---

### 7.7 用户常用地区 Top-N

用途：统计每个用户最常见的登录或访问地区。

SQL：

```sql
SELECT
    username,
    location,
    cnt
FROM
(
    SELECT
        username,
        location,
        count() AS cnt
    FROM logs_structured
    PREWHERE timestamp >= {start_time:DateTime}
        AND timestamp < {end_time:DateTime}
    WHERE username != ''
      AND location != ''
    GROUP BY
        username,
        location
    ORDER BY
        username ASC,
        cnt DESC
)
LIMIT {limit:UInt32} BY username;
```

Repository 接口：

```python
def fetch_top_locations(self, start_time, end_time, limit: int) -> list[dict]:
    ...
```

---

### 7.8 用户常用接口 Top-N

用途：统计每个用户最常访问的 API 接口。

接口字段需要特别注意：

```text
/api/order?id=1
/api/order?id=2
/api/order?id=3
```

如果直接按完整 URL 聚合，会导致接口基线极度碎片化。

因此第一版建议去掉查询参数：

```sql
replaceRegexpOne(endpoint, '\\?.*$', '') AS endpoint_path
```

SQL：

```sql
SELECT
    username,
    endpoint_path,
    cnt
FROM
(
    SELECT
        username,
        replaceRegexpOne(endpoint, '\\?.*$', '') AS endpoint_path,
        count() AS cnt
    FROM logs_structured
    PREWHERE timestamp >= {start_time:DateTime}
        AND timestamp < {end_time:DateTime}
    WHERE username != ''
      AND endpoint != ''
    GROUP BY
        username,
        endpoint_path
    ORDER BY
        username ASC,
        cnt DESC
)
LIMIT {limit:UInt32} BY username;
```

Repository 接口：

```python
def fetch_top_endpoints(self, start_time, end_time, limit: int) -> list[dict]:
    ...
```

---

### 7.9 用户行为类型分布

用途：统计用户行为类型占比。

SQL：

```sql
SELECT
    username,
    action,
    count() AS cnt
FROM logs_structured
PREWHERE timestamp >= {start_time:DateTime}
    AND timestamp < {end_time:DateTime}
WHERE username != ''
  AND action != ''
GROUP BY
    username,
    action
ORDER BY
    username ASC,
    cnt DESC;
```

Repository 接口：

```python
def fetch_action_distribution(self, start_time, end_time) -> list[dict]:
    ...
```

如果 action 种类可能非常多，可以增加：

```sql
LIMIT {limit:UInt32} BY username
```

---

### 7.10 用户状态分布

用途：统计用户成功、失败、错误等状态占比。

SQL：

```sql
SELECT
    username,
    status,
    count() AS cnt
FROM logs_structured
PREWHERE timestamp >= {start_time:DateTime}
    AND timestamp < {end_time:DateTime}
WHERE username != ''
  AND status != ''
GROUP BY
    username,
    status
ORDER BY
    username ASC,
    cnt DESC;
```

Repository 接口：

```python
def fetch_status_distribution(self, start_time, end_time) -> list[dict]:
    ...
```

---

### 7.11 用户每日事件数

用途：计算用户平均每日事件数、活跃日平均事件数、最大单日事件数。

SQL：

```sql
SELECT
    username,
    toDate(timestamp) AS event_date,
    count() AS cnt
FROM logs_structured
PREWHERE timestamp >= {start_time:DateTime}
    AND timestamp < {end_time:DateTime}
WHERE username != ''
GROUP BY
    username,
    event_date
ORDER BY
    username ASC,
    event_date ASC;
```

Repository 接口：

```python
def fetch_daily_event_counts(self, start_time, end_time) -> list[dict]:
    ...
```

---

### 7.12 Repository 类建议结构

```python
class UebaRepository:
    def __init__(self, client, database: str = "log_analysis"):
        self.client = client
        self.database = database

    def fetch_user_summary(self, start_time, end_time) -> list[dict]:
        ...

    def fetch_hour_distribution(self, start_time, end_time) -> list[dict]:
        ...

    def fetch_top_ips(self, start_time, end_time, limit: int) -> list[dict]:
        ...

    def fetch_top_locations(self, start_time, end_time, limit: int) -> list[dict]:
        ...

    def fetch_top_endpoints(self, start_time, end_time, limit: int) -> list[dict]:
        ...

    def fetch_action_distribution(self, start_time, end_time) -> list[dict]:
        ...

    def fetch_status_distribution(self, start_time, end_time) -> list[dict]:
        ...

    def fetch_daily_event_counts(self, start_time, end_time) -> list[dict]:
        ...
```

---

## 8. `aggregate_merger.py` 详细设计

### 8.1 模块职责

`aggregate_merger.py` 负责把多个聚合 SQL 的结果合并到统一的 `UserAggregateFeature` 中。

它不访问数据库，也不写数据库。

输入是：

```text
repository.py 返回的多组聚合查询结果
```

输出是：

```text
dict[str, UserAggregateFeature]
```

---

### 8.2 为什么需要单独的合并层

如果把合并逻辑写在 `service.py`，`service.py` 会变得很重。

如果把合并逻辑写在 `baseline_builder.py`，会导致基线生成逻辑和数据清洗逻辑混在一起。

因此建议单独拆出：

```text
repository.py          负责取数
aggregate_merger.py    负责合并聚合结果
baseline_builder.py    负责生成基线
```

---

### 8.3 合并器类设计

```python
class AggregateMerger:
    def merge(
        self,
        user_summary_rows: list[dict],
        hour_rows: list[dict],
        ip_rows: list[dict],
        location_rows: list[dict],
        endpoint_rows: list[dict],
        action_rows: list[dict],
        status_rows: list[dict],
        daily_rows: list[dict],
    ) -> dict[str, UserAggregateFeature]:
        ...
```

---

### 8.4 合并逻辑

先根据用户总览统计创建用户特征对象：

```python
features: dict[str, UserAggregateFeature] = {}

for row in user_summary_rows:
    username = row["username"]
    features[username] = UserAggregateFeature(
        username=username,
        sample_count=int(row["sample_count"]),
        failed_count=int(row["failed_count"]),
        active_days=int(row["active_days"]),
        first_seen=row.get("first_seen"),
        last_seen=row.get("last_seen"),
    )
```

然后合并小时分布：

```python
for row in hour_rows:
    username = row["username"]
    if username not in features:
        continue
    features[username].hour_counts[int(row["active_hour"])] = int(row["cnt"])
```

合并 IP：

```python
for row in ip_rows:
    username = row["username"]
    if username not in features:
        continue
    features[username].ip_counts[str(row["source_ip"])] = int(row["cnt"])
```

合并地区：

```python
for row in location_rows:
    username = row["username"]
    if username not in features:
        continue
    features[username].location_counts[str(row["location"])] = int(row["cnt"])
```

合并接口：

```python
for row in endpoint_rows:
    username = row["username"]
    if username not in features:
        continue
    features[username].endpoint_counts[str(row["endpoint_path"])] = int(row["cnt"])
```

合并行为类型：

```python
for row in action_rows:
    username = row["username"]
    if username not in features:
        continue
    features[username].action_counts[str(row["action"])] = int(row["cnt"])
```

合并状态分布：

```python
for row in status_rows:
    username = row["username"]
    if username not in features:
        continue
    features[username].status_counts[str(row["status"])] = int(row["cnt"])
```

合并每日事件数：

```python
for row in daily_rows:
    username = row["username"]
    if username not in features:
        continue
    features[username].daily_counts[str(row["event_date"])] = int(row["cnt"])
```

---

### 8.5 异常数据处理原则

合并时遇到不完整数据不要直接崩溃。

应采用以下策略：

```text
username 不存在：跳过
某个维度为空：保留空 dict
cnt 为空或无法转换：按 0 或跳过
字段缺失：记录日志，跳过该行
```

但对于用户总览统计中的关键字段：

```text
username
sample_count
```

如果缺失，应跳过该用户。

---

## 9. `baseline_builder.py` 详细设计

### 9.1 模块职责

`baseline_builder.py` 是 UEBA 第一版的核心业务模块。

它负责将 `UserAggregateFeature` 转成 `UserBaseline`。

它不读数据库，也不写数据库。

---

### 9.2 基线生成入口

```python
class BaselineBuilder:
    def __init__(self, config: UebaBaselineConfig):
        self.config = config

    def build_baselines(
        self,
        features: dict[str, UserAggregateFeature],
        start_time,
        end_time,
    ) -> list[UserBaseline]:
        ...
```

---

### 9.3 可靠性判断

第一版基线可靠性只根据样本数量判断。

```python
is_reliable = feature.sample_count >= config.min_sample_count
```

建议逻辑：

```text
sample_count < 20：不可靠
sample_count >= 20：可靠
```

后期可以扩展为：

```text
样本数量
活跃天数
时间跨度
行为覆盖度
```

但第一版不要复杂化。

---

### 9.4 通用 Top-N 生成函数

建议写一个通用函数，把计数字典转成带占比的列表。

```python
def build_count_ratio_items(
    counts: dict[str | int, int],
    total: int,
    limit: int,
    min_ratio: float = 0.0,
) -> list[CountRatioItem]:
    if total <= 0:
        return []

    items = []
    for value, count in sorted(counts.items(), key=lambda x: x[1], reverse=True):
        ratio = count / total
        if ratio < min_ratio:
            continue
        items.append(
            CountRatioItem(
                value=value,
                count=count,
                ratio=round(ratio, 6),
            )
        )
        if len(items) >= limit:
            break

    return items
```

---

### 9.5 常用活跃小时

常用小时从 `hour_counts` 生成。

```python
common_active_hours = build_count_ratio_items(
    counts=feature.hour_counts,
    total=feature.sample_count,
    limit=24,
    min_ratio=config.common_hour_min_ratio,
)
```

说明：

```text
不要把出现过的所有小时都加入常用时间
只有占比达到阈值的小时才算常用活跃小时
```

示例：

```json
[
  {"value": 9, "count": 2300, "ratio": 0.191667},
  {"value": 10, "count": 2100, "ratio": 0.175},
  {"value": 14, "count": 1800, "ratio": 0.15}
]
```

---

### 9.6 常用 IP

```python
common_ips = build_count_ratio_items(
    counts=feature.ip_counts,
    total=feature.sample_count,
    limit=config.top_ip_limit,
    min_ratio=config.common_ip_min_ratio,
)
```

不要只保存 IP 字符串，应保存：

```text
IP 值
出现次数
占比
```

后续异常检测时可以区分：

```text
从来没出现过的 IP
出现过但占比极低的 IP
稳定常用 IP
```

---

### 9.7 常用地区

```python
common_locations = build_count_ratio_items(
    counts=feature.location_counts,
    total=feature.sample_count,
    limit=config.top_location_limit,
    min_ratio=config.common_location_min_ratio,
)
```

保存格式示例：

```json
[
  {"value": "北京", "count": 9500, "ratio": 0.791667},
  {"value": "上海", "count": 1300, "ratio": 0.108333}
]
```

---

### 9.8 常用接口

```python
common_endpoints = build_count_ratio_items(
    counts=feature.endpoint_counts,
    total=feature.sample_count,
    limit=config.top_endpoint_limit,
    min_ratio=config.common_endpoint_min_ratio,
)
```

接口必须经过归一化后再聚合。

推荐基线保存的是：

```text
/api/order/query
/api/user/profile
/api/login
```

而不是：

```text
/api/order/query?id=1
/api/order/query?id=2
/api/order/query?id=3
```

---

### 9.9 行为类型分布

将计数字典转成比例字典。

```python
def build_distribution(counts: dict[str, int], total: int) -> dict[str, float]:
    if total <= 0:
        return {}
    return {
        key: round(value / total, 6)
        for key, value in counts.items()
        if value > 0
    }
```

示例：

```json
{
  "API_CALL": 0.81,
  "LOGIN_SUCCESS": 0.12,
  "LOGOUT": 0.05,
  "LOGIN_FAILED": 0.02
}
```

---

### 9.10 状态分布

同样转成比例字典。

示例：

```json
{
  "SUCCESS": 0.96,
  "FAILED": 0.03,
  "ERROR": 0.01
}
```

---

### 9.11 失败率

```python
failed_rate = feature.failed_count / feature.sample_count
```

需要防止除零：

```python
failed_rate = 0.0 if feature.sample_count <= 0 else feature.failed_count / feature.sample_count
```

---

### 9.12 每日事件统计

需要生成：

```text
avg_daily_events
active_day_avg_events
max_daily_events
```

定义：

```text
avg_daily_events：总日志数 / 基线窗口天数
active_day_avg_events：总日志数 / 实际有日志的天数
max_daily_events：基线窗口中单日最大日志数
```

示例：

```python
window_days = max((end_time - start_time).days, 1)
active_days = max(feature.active_days, 1)

avg_daily_events = feature.sample_count / window_days
active_day_avg_events = feature.sample_count / active_days
max_daily_events = max(feature.daily_counts.values(), default=0)
```

这三个值后续可以用于判断：

```text
某用户当天访问量是否远高于历史平均
某用户是否突然爆发大量请求
```

---

### 9.13 最终基线示例

```json
{
  "username": "zhangsan",
  "sample_count": 12000,
  "is_reliable": true,
  "common_active_hours": [
    {"value": 9, "count": 2300, "ratio": 0.191667},
    {"value": 10, "count": 2100, "ratio": 0.175},
    {"value": 14, "count": 1800, "ratio": 0.15}
  ],
  "common_ips": [
    {"value": "10.0.0.1", "count": 8000, "ratio": 0.666667},
    {"value": "10.0.0.2", "count": 3000, "ratio": 0.25}
  ],
  "common_locations": [
    {"value": "北京", "count": 9500, "ratio": 0.791667}
  ],
  "common_endpoints": [
    {"value": "/api/login", "count": 3000, "ratio": 0.25},
    {"value": "/api/order/query", "count": 2400, "ratio": 0.2}
  ],
  "action_distribution": {
    "API_CALL": 0.81,
    "LOGIN_SUCCESS": 0.12,
    "LOGOUT": 0.05,
    "LOGIN_FAILED": 0.02
  },
  "status_distribution": {
    "SUCCESS": 0.96,
    "FAILED": 0.03,
    "ERROR": 0.01
  },
  "failed_rate": 0.026667,
  "avg_daily_events": 400.0,
  "active_day_avg_events": 444.444444,
  "max_daily_events": 812,
  "baseline_start_time": "2026-04-19 00:00:00",
  "baseline_end_time": "2026-05-19 00:00:00",
  "model_version": "ueba_baseline_v1"
}
```

---

## 10. `baseline_store.py` 详细设计

### 10.1 模块职责

`baseline_store.py` 负责将生成好的基线写入数据库。

它不负责读取日志，也不负责生成基线。

---

### 10.2 基线表建议

ClickHouse 表名建议：

```text
user_behavior_baselines
```

建表示例：

```sql
CREATE TABLE IF NOT EXISTS user_behavior_baselines
(
    username String,
    sample_count UInt64,
    is_reliable UInt8,

    common_active_hours String,
    common_ips String,
    common_locations String,
    common_endpoints String,

    action_distribution String,
    status_distribution String,

    failed_rate Float64,
    avg_daily_events Float64,
    active_day_avg_events Float64,
    max_daily_events UInt64,

    baseline_start_time DateTime,
    baseline_end_time DateTime,
    model_version String,

    baseline_json String,
    created_at DateTime DEFAULT now()
)
ENGINE = ReplacingMergeTree(created_at)
ORDER BY (username, model_version, baseline_start_time, baseline_end_time);
```

---

### 10.3 为什么使用 `ReplacingMergeTree`

第一版可能会重复运行基线构建脚本。

如果每次都插入相同 `username + model_version + baseline_start_time + baseline_end_time` 的记录，会产生重复数据。

使用：

```sql
ENGINE = ReplacingMergeTree(created_at)
ORDER BY (username, model_version, baseline_start_time, baseline_end_time)
```

可以让 ClickHouse 在后台合并时保留较新的记录。

注意：

```text
ReplacingMergeTree 不是强事务实时去重。
查询最新基线时最好配合 ORDER BY created_at DESC 或 FINAL。
```

第一版可以接受这一点。

---

### 10.4 为什么保留 `baseline_json`

基线表中既有结构化字段，也有完整 JSON 字段。

结构化字段用于常见查询：

```text
username
sample_count
is_reliable
failed_rate
avg_daily_events
model_version
```

完整 JSON 用于后续扩展：

```text
common_active_hours
common_ips
common_locations
common_endpoints
action_distribution
status_distribution
```

这样后期新增字段时，可以先写进 JSON，不必立刻改表结构。

---

### 10.5 写入方式

不要一条一条写入。

错误设计：

```python
for baseline in baselines:
    insert_one(baseline)
```

正确设计：

```python
insert_many(baselines, batch_size=config.write_batch_size)
```

接口建议：

```python
class BaselineStore:
    def __init__(self, client, database: str = "log_analysis"):
        self.client = client
        self.database = database

    def ensure_table(self):
        ...

    def save_baselines(self, baselines: list[UserBaseline]) -> int:
        ...

    def get_user_baseline(self, username: str, model_version: str | None = None) -> dict | None:
        ...
```

---

### 10.6 序列化规则

写入数据库前，需要把复杂字段序列化成 JSON 字符串。

示例：

```python
import json
from dataclasses import asdict


def baseline_to_row(baseline: UserBaseline) -> dict:
    baseline_dict = asdict(baseline)

    return {
        "username": baseline.username,
        "sample_count": baseline.sample_count,
        "is_reliable": 1 if baseline.is_reliable else 0,
        "common_active_hours": json.dumps(baseline_dict["common_active_hours"], ensure_ascii=False),
        "common_ips": json.dumps(baseline_dict["common_ips"], ensure_ascii=False),
        "common_locations": json.dumps(baseline_dict["common_locations"], ensure_ascii=False),
        "common_endpoints": json.dumps(baseline_dict["common_endpoints"], ensure_ascii=False),
        "action_distribution": json.dumps(baseline.action_distribution, ensure_ascii=False),
        "status_distribution": json.dumps(baseline.status_distribution, ensure_ascii=False),
        "failed_rate": baseline.failed_rate,
        "avg_daily_events": baseline.avg_daily_events,
        "active_day_avg_events": baseline.active_day_avg_events,
        "max_daily_events": baseline.max_daily_events,
        "baseline_start_time": baseline.baseline_start_time,
        "baseline_end_time": baseline.baseline_end_time,
        "model_version": baseline.model_version,
        "baseline_json": json.dumps(baseline_dict, ensure_ascii=False, default=str),
    }
```

---

## 11. `service.py` 详细设计

### 11.1 模块职责

`service.py` 是 UEBA 对外统一入口。

脚本、前端接口、AI 模块后续都应该优先调用 `UebaService`，而不是直接调用底层文件。

---

### 11.2 Service 类结构

```python
class UebaService:
    def __init__(
        self,
        repository: UebaRepository,
        aggregate_merger: AggregateMerger,
        baseline_builder: BaselineBuilder,
        baseline_store: BaselineStore,
        config: UebaBaselineConfig,
    ):
        self.repository = repository
        self.aggregate_merger = aggregate_merger
        self.baseline_builder = baseline_builder
        self.baseline_store = baseline_store
        self.config = config
```

---

### 11.3 一次性构建基线入口

核心接口：

```python
def build_baseline_once(self, start_time, end_time) -> BaselineBuildResult:
    ...
```

它应该完成：

```text
1. 确保基线表存在
2. 调用 repository 执行多个数据库侧聚合查询
3. 调用 aggregate_merger 合并聚合结果
4. 调用 baseline_builder 生成用户基线
5. 调用 baseline_store 批量写入基线
6. 返回构建统计信息
```

---

### 11.4 推荐伪代码

```python
import time


class UebaService:
    def build_baseline_once(self, start_time, end_time) -> BaselineBuildResult:
        begin = time.time()

        self.baseline_store.ensure_table()

        user_summary_rows = self.repository.fetch_user_summary(start_time, end_time)
        hour_rows = self.repository.fetch_hour_distribution(start_time, end_time)
        ip_rows = self.repository.fetch_top_ips(start_time, end_time, self.config.top_ip_limit)
        location_rows = self.repository.fetch_top_locations(start_time, end_time, self.config.top_location_limit)
        endpoint_rows = self.repository.fetch_top_endpoints(start_time, end_time, self.config.top_endpoint_limit)
        action_rows = self.repository.fetch_action_distribution(start_time, end_time)
        status_rows = self.repository.fetch_status_distribution(start_time, end_time)
        daily_rows = self.repository.fetch_daily_event_counts(start_time, end_time)

        features = self.aggregate_merger.merge(
            user_summary_rows=user_summary_rows,
            hour_rows=hour_rows,
            ip_rows=ip_rows,
            location_rows=location_rows,
            endpoint_rows=endpoint_rows,
            action_rows=action_rows,
            status_rows=status_rows,
            daily_rows=daily_rows,
        )

        baselines = self.baseline_builder.build_baselines(
            features=features,
            start_time=start_time,
            end_time=end_time,
        )

        saved_count = self.baseline_store.save_baselines(baselines)

        reliable_count = sum(1 for item in baselines if item.is_reliable)
        total_log_count = sum(item.sample_count for item in baselines)

        return BaselineBuildResult(
            success=True,
            baseline_start_time=start_time,
            baseline_end_time=end_time,
            total_user_count=len(baselines),
            reliable_user_count=reliable_count,
            unreliable_user_count=len(baselines) - reliable_count,
            total_log_count=total_log_count,
            model_version=self.config.model_version,
            duration_seconds=round(time.time() - begin, 3),
            message=f"saved {saved_count} user baselines",
        )
```

---

### 11.5 对外返回示例

```json
{
  "success": true,
  "baseline_start_time": "2026-04-19 00:00:00",
  "baseline_end_time": "2026-05-19 00:00:00",
  "total_user_count": 500,
  "reliable_user_count": 420,
  "unreliable_user_count": 80,
  "total_log_count": 120000,
  "model_version": "ueba_baseline_v1",
  "duration_seconds": 6.84,
  "message": "saved 500 user baselines"
}
```

---

## 12. `scripts/build_ueba_baseline.py` 详细设计

### 12.1 脚本职责

该脚本用于手动触发一次性基线构建。

它是第一版最直接的运行入口。

---

### 12.2 命令行参数建议

```bash
python scripts/build_ueba_baseline.py \
  --start-time "2026-04-19 00:00:00" \
  --end-time "2026-05-19 00:00:00" \
  --min-sample-count 20 \
  --top-ip-limit 10 \
  --top-endpoint-limit 20
```

如果不传 `start-time` 和 `end-time`，可以默认使用最近 30 天。

---

### 12.3 脚本逻辑

```text
1. 解析命令行参数
2. 构造 UebaBaselineConfig
3. 初始化 ClickHouse client
4. 初始化 repository / merger / builder / store / service
5. 调用 service.build_baseline_once()
6. 打印 JSON 格式结果
```

---

### 12.4 输出示例

```json
{
  "success": true,
  "baseline_start_time": "2026-04-19 00:00:00",
  "baseline_end_time": "2026-05-19 00:00:00",
  "total_user_count": 342,
  "reliable_user_count": 280,
  "unreliable_user_count": 62,
  "total_log_count": 124532,
  "model_version": "ueba_baseline_v1",
  "duration_seconds": 8.72,
  "message": "saved 342 user baselines"
}
```

---

## 13. 数据库侧聚合完整链路

第一版完整链路如下：

```text
UebaService.build_baseline_once(start_time, end_time, config)
    ↓
repository.fetch_user_summary()
    ↓
repository.fetch_hour_distribution()
    ↓
repository.fetch_top_ips()
    ↓
repository.fetch_top_locations()
    ↓
repository.fetch_top_endpoints()
    ↓
repository.fetch_action_distribution()
    ↓
repository.fetch_status_distribution()
    ↓
repository.fetch_daily_event_counts()
    ↓
aggregate_merger.merge()
    ↓
baseline_builder.build_baselines()
    ↓
baseline_store.save_baselines()
    ↓
返回 BaselineBuildResult
```

---

## 14. 为什么这种方案能支撑十万级日志

假设原始日志量为：

```text
100000 条
```

如果直接拉到 Python，则 Python 要处理 100000 条完整日志对象。

但采用数据库侧聚合后，Python 处理的是：

```text
用户数 × 各维度聚合结果
```

假设：

```text
用户数：500
每个用户最多：
- 24 个小时分布
- 10 个 IP
- 10 个地区
- 20 个接口
- 若干 action
- 若干 status
- 30 个每日统计
```

Python 侧处理的数据规模大约是：

```text
500 × (24 + 10 + 10 + 20 + 20 + 10 + 30)
≈ 62000 行以内
```

并且这些聚合行都很短，不包含完整日志正文。

因此相比直接拉取十万条原始日志，内存压力和处理压力都明显更低。

更重要的是，随着日志从十万增长到百万，Python 侧数据量不一定线性暴涨，而主要取决于：

```text
用户数
Top-N 限制
时间窗口天数
行为类型数量
```

这就是数据库侧聚合方案的核心优势。

---

## 15. 后期扩展方向

当前版本完成后，可以自然扩展以下能力。

### 15.1 外部配置接口

当前：

```text
config.py 默认配置
```

后期：

```text
前端页面 / API 请求 / 配置表
    ↓
UebaBaselineConfig
    ↓
UebaService
```

---

### 15.2 异常评分模块

后续可以新增：

```text
src/behavior/anomaly_scorer.py
```

输入：

```text
用户基线 + 新日志事件
```

输出：

```text
risk_score
risk_level
reasons
```

---

### 15.3 增量更新基线

当前：

```text
一次性构建基线
```

后期：

```text
每天定时更新
按时间窗口滚动更新
按新增日志增量更新
```

---

### 15.4 实时检测

当前不接 Kafka。

后期可以扩展为：

```text
Kafka 新日志
    ↓
实时结构化处理
    ↓
读取用户基线
    ↓
异常评分
    ↓
写入异常结果表
    ↓
前端 / 告警 / AI 分析
```

---

### 15.5 更复杂的行为特征

后续可以加入：

```text
User-Agent / 设备指纹
请求方法 GET / POST / DELETE
接口类别
IP ASN / 国家 / 城市
短时间多地登录
单位时间请求突增
敏感接口访问频率
用户组行为对比
```

---

## 16. 当前版本最终交付物

第一版 UEBA 模块完成后，应该至少包含：

```text
src/behavior/config.py
src/behavior/schemas.py
src/behavior/repository.py
src/behavior/aggregate_merger.py
src/behavior/baseline_builder.py
src/behavior/baseline_store.py
src/behavior/service.py
scripts/build_ueba_baseline.py
```

数据库中应该至少有：

```text
logs_structured
user_behavior_baselines
```

能够完成：

```text
1. 从 logs_structured 中按时间范围聚合日志
2. 生成每个用户的行为基线
3. 保存到 user_behavior_baselines
4. 返回本次构建统计信息
5. 支撑十万级日志数据，不把全部原始日志拉到 Python 内存
```

---

## 17. 给后续开发者 / Codex 的核心要求

开发该模块时必须遵守：

```text
1. 不要一次性 SELECT * 拉取所有原始日志。
2. 优先使用数据库侧 GROUP BY 聚合。
3. 每个维度使用独立、清晰、可调试的聚合 SQL。
4. Python 侧只合并聚合结果，不保存原始日志列表。
5. 所有 Top-N 维度必须限制数量。
6. endpoint 必须去掉查询参数后再聚合。
7. config.py 只是默认配置，业务逻辑应通过 UebaBaselineConfig 接收配置。
8. 复杂基线字段先用 JSON 字符串存储。
9. 基线写入必须批量插入。
10. Service 层作为唯一推荐对外入口。
```

---

## 18. 最终一句话总结

当前 UEBA 第一版要做的不是完整安全分析系统，而是：

```text
用数据库侧聚合扛住十万级结构化日志，
用 Python 合并聚合结果并生成用户行为基线，
将基线稳定写入数据库，
为后续异常检测、AI 分析和可视化展示提供可靠数据基础。
```
