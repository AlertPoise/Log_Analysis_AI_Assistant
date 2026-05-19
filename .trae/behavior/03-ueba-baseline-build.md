# UEBA 基线构建逻辑设计

## 1. 基线构建目标

`baseline_builder.py` 的目标是：

```text
将每个用户的聚合特征转换成最终用户行为基线。
```

输入：

```text
dict[str, UserAggregateFeature]
```

输出：

```text
list[UserBaseline]
```

该模块不读数据库，不写数据库，只负责业务计算。

---

## 2. AggregateMerger 合并结果

在进入基线构建之前，需要先将多个 SQL 聚合结果合并。

合并模块建议命名为：

```text
aggregate_merger.py
```

---

## 3. AggregateMerger 职责

`aggregate_merger.py` 负责：

```text
1. 接收 repository.py 返回的多组聚合结果
2. 按 username 创建 UserAggregateFeature
3. 将小时、IP、地区、接口、行为、状态、每日事件数合并进去
4. 返回 dict[str, UserAggregateFeature]
```

---

## 4. 合并入口

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

## 5. 合并规则

### 5.1 先合并用户总览

用户总览是骨架。

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

---

### 5.2 合并小时分布

```python
for row in hour_rows:
    username = row["username"]
    if username not in features:
        continue

    features[username].hour_counts[int(row["active_hour"])] = int(row["cnt"])
```

---

### 5.3 合并 IP

```python
for row in ip_rows:
    username = row["username"]
    if username not in features:
        continue

    features[username].ip_counts[str(row["source_ip"])] = int(row["cnt"])
```

---

### 5.4 合并地区

```python
for row in location_rows:
    username = row["username"]
    if username not in features:
        continue

    features[username].location_counts[str(row["location"])] = int(row["cnt"])
```

---

### 5.5 合并接口

```python
for row in endpoint_rows:
    username = row["username"]
    if username not in features:
        continue

    features[username].endpoint_counts[str(row["endpoint_path"])] = int(row["cnt"])
```

---

### 5.6 合并行为类型

```python
for row in action_rows:
    username = row["username"]
    if username not in features:
        continue

    features[username].action_counts[str(row["action"])] = int(row["cnt"])
```

---

### 5.7 合并状态

```python
for row in status_rows:
    username = row["username"]
    if username not in features:
        continue

    features[username].status_counts[str(row["status"])] = int(row["cnt"])
```

---

### 5.8 合并每日事件数

```python
for row in daily_rows:
    username = row["username"]
    if username not in features:
        continue

    features[username].daily_counts[str(row["event_date"])] = int(row["cnt"])
```

---

## 6. 异常聚合行处理原则

合并时不要因为单条异常聚合结果导致整个构建失败。

建议策略：

```text
username 不存在：跳过
cnt 为空：跳过或按 0 处理
字段缺失：记录日志后跳过
某个维度完全为空：保留空 dict
```

但如果用户总览中的以下字段缺失：

```text
username
sample_count
```

应跳过该用户。

---

## 7. BaselineBuilder 职责

`baseline_builder.py` 负责把：

```text
UserAggregateFeature
```

转换成：

```text
UserBaseline
```

它主要完成：

```text
1. 判断基线可靠性
2. 计算常用活跃小时
3. 计算常用 IP
4. 计算常用地区
5. 计算常用接口
6. 计算行为类型分布
7. 计算状态分布
8. 计算失败率
9. 计算每日事件统计
```

---

## 8. BaselineBuilder 类结构

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

## 9. 可靠性判断

第一版只根据样本数量判断基线是否可靠。

```python
is_reliable = feature.sample_count >= config.min_sample_count
```

建议默认：

```text
sample_count < 20：不可靠
sample_count >= 20：可靠
```

后期可以扩展更多判断条件：

```text
活跃天数
基线时间跨度
行为种类数量
数据稳定性
```

---

## 10. Top-N 通用函数

建议实现一个通用函数：

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

该函数可用于：

```text
常用小时
常用 IP
常用地区
常用接口
```

---

## 11. 常用活跃小时

```python
common_active_hours = build_count_ratio_items(
    counts=feature.hour_counts,
    total=feature.sample_count,
    limit=24,
    min_ratio=config.common_hour_min_ratio,
)
```

注意：

```text
不要保存所有出现过的小时。
只有占比达到阈值的小时才算常用活跃小时。
```

---

## 12. 常用 IP

```python
common_ips = build_count_ratio_items(
    counts=feature.ip_counts,
    total=feature.sample_count,
    limit=config.top_ip_limit,
    min_ratio=config.common_ip_min_ratio,
)
```

保存时不要只保存 IP 字符串，应保存：

```text
IP
出现次数
占比
```

---

## 13. 常用地区

```python
common_locations = build_count_ratio_items(
    counts=feature.location_counts,
    total=feature.sample_count,
    limit=config.top_location_limit,
    min_ratio=config.common_location_min_ratio,
)
```

---

## 14. 常用接口

```python
common_endpoints = build_count_ratio_items(
    counts=feature.endpoint_counts,
    total=feature.sample_count,
    limit=config.top_endpoint_limit,
    min_ratio=config.common_endpoint_min_ratio,
)
```

接口必须在数据库侧或 Repository 层完成归一化。

例如：

```text
/api/order?id=1
/api/order?id=2
```

应该归一为：

```text
/api/order
```

---

## 15. 行为类型分布

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

## 16. 状态分布

同样使用 `build_distribution()`。

示例：

```json
{
  "SUCCESS": 0.96,
  "FAILED": 0.03,
  "ERROR": 0.01
}
```

---

## 17. 失败率

```python
if feature.sample_count <= 0:
    failed_rate = 0.0
else:
    failed_rate = feature.failed_count / feature.sample_count

failed_rate = round(failed_rate, 6)
```

---

## 18. 每日事件统计

需要生成：

```text
avg_daily_events
active_day_avg_events
max_daily_events
```

计算方式：

```python
window_days = max((end_time - start_time).days, 1)
active_days = max(feature.active_days, 1)

avg_daily_events = feature.sample_count / window_days
active_day_avg_events = feature.sample_count / active_days
max_daily_events = max(feature.daily_counts.values(), default=0)
```

含义：

```text
avg_daily_events：
按整个基线窗口平均。

active_day_avg_events：
只按用户实际活跃天数平均。

max_daily_events：
基线窗口内单日最大日志数量。
```

---

## 19. 最终生成 UserBaseline

```python
baseline = UserBaseline(
    username=feature.username,
    sample_count=feature.sample_count,
    is_reliable=is_reliable,

    common_active_hours=common_active_hours,
    common_ips=common_ips,
    common_locations=common_locations,
    common_endpoints=common_endpoints,

    action_distribution=action_distribution,
    status_distribution=status_distribution,

    failed_rate=failed_rate,
    avg_daily_events=round(avg_daily_events, 6),
    active_day_avg_events=round(active_day_avg_events, 6),
    max_daily_events=max_daily_events,

    baseline_start_time=start_time,
    baseline_end_time=end_time,
    model_version=config.model_version,
)
```

---

## 20. 基线构建原则

`baseline_builder.py` 必须遵守：

```text
1. 不读数据库。
2. 不写数据库。
3. 不处理原始日志。
4. 只处理聚合特征。
5. 所有 Top-N 都必须有数量限制。
6. 所有 ratio 都要防止除零。
7. 不可靠基线也可以保存，但要标记 is_reliable = false。
```
