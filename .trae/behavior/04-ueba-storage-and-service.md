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

## 3. 字段说明

| 字段 | 含义 |
|---|---|
| `username` | 用户名 |
| `sample_count` | 用户在基线窗口内的日志数量 |
| `is_reliable` | 基线是否可靠 |
| `common_active_hours` | 常用活跃小时 JSON |
| `common_ips` | 常用 IP JSON |
| `common_locations` | 常用地区 JSON |
| `common_endpoints` | 常用接口 JSON |
| `action_distribution` | 行为类型分布 JSON |
| `status_distribution` | 状态分布 JSON |
| `failed_rate` | 失败率 |
| `avg_daily_events` | 按基线窗口平均的每日事件数 |
| `active_day_avg_events` | 按活跃天数平均的每日事件数 |
| `max_daily_events` | 最大单日事件数 |
| `baseline_start_time` | 基线开始时间 |
| `baseline_end_time` | 基线结束时间 |
| `model_version` | 基线模型版本 |
| `baseline_json` | 完整基线 JSON |
| `created_at` | 写入时间 |

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
avg_daily_events
model_version
```

复杂字段用于展示和后续分析：

```text
common_ips
common_locations
common_endpoints
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

        "common_active_hours": json.dumps(
            baseline_dict["common_active_hours"],
            ensure_ascii=False,
        ),
        "common_ips": json.dumps(
            baseline_dict["common_ips"],
            ensure_ascii=False,
        ),
        "common_locations": json.dumps(
            baseline_dict["common_locations"],
            ensure_ascii=False,
        ),
        "common_endpoints": json.dumps(
            baseline_dict["common_endpoints"],
            ensure_ascii=False,
        ),

        "action_distribution": json.dumps(
            baseline.action_distribution,
            ensure_ascii=False,
        ),
        "status_distribution": json.dumps(
            baseline.status_distribution,
            ensure_ascii=False,
        ),

        "failed_rate": baseline.failed_rate,
        "avg_daily_events": baseline.avg_daily_events,
        "active_day_avg_events": baseline.active_day_avg_events,
        "max_daily_events": baseline.max_daily_events,

        "baseline_start_time": baseline.baseline_start_time,
        "baseline_end_time": baseline.baseline_end_time,
        "model_version": baseline.model_version,

        "baseline_json": json.dumps(
            baseline_dict,
            ensure_ascii=False,
            default=str,
        ),
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

如果数据量较大，按批次写：

```python
def chunks(items, batch_size):
    for i in range(0, len(items), batch_size):
        yield items[i:i + batch_size]
```

```python
for batch in chunks(rows, config.write_batch_size):
    client.insert("user_behavior_baselines", batch)
```

---

## 9. `service.py` 模块职责

`service.py` 是 UEBA 对外统一入口。

脚本、接口、AI 模块、前端适配层都应该调用：

```text
UebaService
```

而不是直接调用底层模块。

---

## 10. Service 初始化

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

## 11. 一次性构建入口

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
5. 查询 Top IP
6. 查询 Top 地区
7. 查询 Top 接口
8. 查询行为类型分布
9. 查询状态分布
10. 查询每日事件数
11. 合并聚合结果
12. 生成用户基线
13. 批量写入数据库
14. 返回构建统计信息
```

---

## 12. Service 伪代码

```python
import time


class UebaService:
    def build_baseline_once(self, start_time, end_time) -> BaselineBuildResult:
        begin = time.time()

        self.baseline_store.ensure_table()

        user_summary_rows = self.repository.fetch_user_summary(start_time, end_time)
        hour_rows = self.repository.fetch_hour_distribution(start_time, end_time)
        ip_rows = self.repository.fetch_top_ips(
            start_time,
            end_time,
            self.config.top_ip_limit,
        )
        location_rows = self.repository.fetch_top_locations(
            start_time,
            end_time,
            self.config.top_location_limit,
        )
        endpoint_rows = self.repository.fetch_top_endpoints(
            start_time,
            end_time,
            self.config.top_endpoint_limit,
        )
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

## 13. 返回结果示例

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

## 14. Service 层设计原则

```text
1. Service 是对外唯一推荐入口。
2. Service 负责编排流程，不承载过多业务细节。
3. Repository 只负责查数据。
4. AggregateMerger 只负责合并聚合结果。
5. BaselineBuilder 只负责生成基线。
6. BaselineStore 只负责存储。
7. Service 返回结构化构建结果，方便脚本、前端、AI 模块调用。
```
