# UEBA 基线存储与服务入口设计

## 1. `baseline_store.py` 模块职责

`baseline_store.py` 负责将生成好的用户行为基线写入数据库。

它不负责：

```text
读取结构化日志
执行聚合 SQL
合并聚合结果
生成基线
```

它只负责：

```text
1. 确保基线表存在
2. 将 UserBaseline 序列化为数据库行
3. 批量写入数据库
4. 查询用户基线
```

---

## 2. 基线表设计

建议表名：

```text
user_behavior_baselines
```

ClickHouse 建表示例：

```sql
CREATE TABLE IF NOT EXISTS user_behavior_baselines
(
    username String,
    sample_count UInt64,
    is_reliable UInt8,

    common_active_hours String,
    common_source_ips String,
    common_destination_ips String,
    common_source_countries String,
    common_source_cities String,
    common_vpn_gateways String,

    action_distribution String,
    event_type_distribution String,
    result_distribution String,
    fail_reason_distribution String,
    auth_method_distribution String,
    client_software_distribution String,
    protocol_distribution String,

    failed_rate Float64,
    off_hours_rate Float64,
    unusual_ip_rate Float64,
    avg_daily_events Float64,
    active_day_avg_events Float64,
    max_daily_events UInt64,

    session_metric_summary String,
    traffic_metric_summary String,

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

## 3. 字段说明

| 字段 | 含义 |
|---|---|
| `username` | 用户名 |
| `sample_count` | 用户在基线窗口内的日志数量 |
| `is_reliable` | 基线是否可靠 |
| `common_active_hours` | 常用活跃小时 JSON |
| `common_source_ips` | 常用来源 IP JSON |
| `common_destination_ips` | 常用目标 IP JSON |
| `common_source_countries` | 常用来源国家 JSON |
| `common_source_cities` | 常用来源城市 JSON |
| `common_vpn_gateways` | 常用 VPN 网关 JSON |
| `action_distribution` | 行为类型分布 JSON |
| `event_type_distribution` | 事件类型分布 JSON |
| `result_distribution` | 登录结果分布 JSON |
| `fail_reason_distribution` | 失败原因分布 JSON |
| `auth_method_distribution` | 认证方式分布 JSON |
| `client_software_distribution` | 客户端软件分布 JSON |
| `protocol_distribution` | 协议分布 JSON |
| `failed_rate` | 失败率，基于 result / event_type 聚合 |
| `off_hours_rate` | 非工作时间比例，来自 is_off_hours 聚合结果 |
| `unusual_ip_rate` | 异常 IP 标签比例，来自 is_unusual_ip 聚合结果 |
| `avg_daily_events` | 按基线窗口平均的每日事件数 |
| `active_day_avg_events` | 按活跃天数平均的每日事件数 |
| `max_daily_events` | 最大单日事件数 |
| `session_metric_summary` | 会话时长统计 JSON |
| `traffic_metric_summary` | 发送 / 接收字节数统计 JSON |
| `baseline_start_time` | 基线开始时间 |
| `baseline_end_time` | 基线结束时间 |
| `model_version` | 基线模型版本 |
| `baseline_json` | 完整基线 JSON |
| `created_at` | 写入时间 |

当前登录主表没有 `endpoint`、`status`、`location` 字段，因此基线表不建议再保留 `common_endpoints`、`status_distribution`、`common_locations` 作为第一版核心字段。

`risk_score`、`risk_tags` 是输入日志中的历史风险参考字段，不作为第一版基线核心输出。

---

## 4. 为什么使用 JSON 字符串

第一版建议复杂字段用 JSON 字符串存储。

原因：

```text
1. 基线字段后期可能频繁变化
2. 不想每次新增字段都修改表结构
3. 前端和 AI 模块可以直接读取完整 JSON
4. ClickHouse 中 String 存 JSON 最简单稳定
```

结构化字段用于常见查询：

```text
username
sample_count
is_reliable
failed_rate
off_hours_rate
unusual_ip_rate
avg_daily_events
model_version
```

复杂字段用于展示和后续分析：

```text
common_active_hours
common_source_ips
common_destination_ips
common_source_countries
common_source_cities
common_vpn_gateways
action_distribution
event_type_distribution
result_distribution
fail_reason_distribution
auth_method_distribution
client_software_distribution
protocol_distribution
session_metric_summary
traffic_metric_summary
baseline_json
```

---

## 5. 为什么使用 ReplacingMergeTree

第一版可能会多次运行构建脚本。

如果重复运行同一时间窗口的基线构建，会插入相同 key 的记录。

使用：

```sql
ENGINE = ReplacingMergeTree(created_at)
ORDER BY (username, model_version, baseline_start_time, baseline_end_time)
```

可以在 ClickHouse 后台合并时保留较新的记录。

注意：

```text
ReplacingMergeTree 不是强事务实时去重。
查询最新基线时建议按 created_at DESC 取最新记录。
```

---

## 6. BaselineStore 类结构

```python
class BaselineStore:
    def __init__(self, client, database: str = "log_analysis"):
        self.client = client
        self.database = database

    def ensure_table(self):
        ...

    def save_baselines(self, baselines: list[UserBaseline]) -> int:
        ...

    def get_user_baseline(
        self,
        username: str,
        model_version: str | None = None,
    ) -> dict | None:
        ...
```

---

## 7. 基线序列化

写入数据库前，需要将 `UserBaseline` 转成数据库行。

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
        "common_source_ips": json.dumps(baseline_dict["common_source_ips"], ensure_ascii=False),
        "common_destination_ips": json.dumps(baseline_dict["common_destination_ips"], ensure_ascii=False),
        "common_source_countries": json.dumps(baseline_dict["common_source_countries"], ensure_ascii=False),
        "common_source_cities": json.dumps(baseline_dict["common_source_cities"], ensure_ascii=False),
        "common_vpn_gateways": json.dumps(baseline_dict["common_vpn_gateways"], ensure_ascii=False),
        "action_distribution": json.dumps(baseline.action_distribution, ensure_ascii=False),
        "event_type_distribution": json.dumps(baseline.event_type_distribution, ensure_ascii=False),
        "result_distribution": json.dumps(baseline.result_distribution, ensure_ascii=False),
        "fail_reason_distribution": json.dumps(baseline.fail_reason_distribution, ensure_ascii=False),
        "auth_method_distribution": json.dumps(baseline.auth_method_distribution, ensure_ascii=False),
        "client_software_distribution": json.dumps(baseline.client_software_distribution, ensure_ascii=False),
        "protocol_distribution": json.dumps(baseline.protocol_distribution, ensure_ascii=False),
        "failed_rate": baseline.failed_rate,
        "off_hours_rate": baseline.off_hours_rate,
        "unusual_ip_rate": baseline.unusual_ip_rate,
        "avg_daily_events": baseline.avg_daily_events,
        "active_day_avg_events": baseline.active_day_avg_events,
        "max_daily_events": baseline.max_daily_events,
        "session_metric_summary": json.dumps(baseline.session_metric_summary, ensure_ascii=False),
        "traffic_metric_summary": json.dumps(baseline.traffic_metric_summary, ensure_ascii=False),
        "baseline_start_time": baseline.baseline_start_time,
        "baseline_end_time": baseline.baseline_end_time,
        "model_version": baseline.model_version,
        "baseline_json": json.dumps(baseline_dict, ensure_ascii=False, default=str),
    }
```

---

## 8. 批量写入要求

不要逐条写入：

```python
for baseline in baselines:
    insert_one(baseline)
```

应该批量写入：

```python
rows = [baseline_to_row(item) for item in baselines]
client.insert("user_behavior_baselines", rows)
```

如果数据量较大，按批次写。

---

## 9. `service.py` 模块职责

`service.py` 是 UEBA 对外统一入口。

脚本、接口、AI 模块、前端适配层都应该调用：

```text
UebaService
```

而不是直接调用底层模块。

---

## 10. 一次性构建入口

核心方法：

```python
def build_baseline_once(self, start_time, end_time) -> BaselineBuildResult:
    ...
```

该方法负责：

```text
1. 记录开始时间
2. 确保基线表存在
3. 查询用户总览
4. 查询小时分布
5. 查询 Top 来源 IP、目标 IP、来源国家、来源城市、VPN 网关
6. 查询 action、event_type、result、fail_reason、auth_method、client_software、protocol 分布
7. 查询每日事件数、会话时长和流量统计
8. 合并聚合结果
9. 生成用户基线
10. 批量写入数据库
11. 返回构建统计信息
```

---

## 11. Service 层设计原则

```text
1. Service 是对外唯一推荐入口。
2. Service 负责编排流程，不承载过多业务细节。
3. Repository 只负责查数据。
4. AggregateMerger 只负责合并聚合结果。
5. BaselineBuilder 只负责生成基线。
6. BaselineStore 只负责存储。
7. Service 返回结构化构建结果，方便脚本、前端、AI 模块调用。
```
