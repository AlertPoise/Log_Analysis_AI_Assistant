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

当前登录主表不包含 `endpoint`、`status`、`location` 字段；基线构建应围绕 `source_ip`、`destination_ip`、`src_country`、`src_city`、`vpn_gateway`、`action`、`event_type`、`result`、`fail_reason`、`auth_method`、`client_software`、`protocol`、`session_duration_sec`、`bytes_sent`、`bytes_recv` 等字段的聚合结果展开。

---

## 2. AggregateMerger 职责

`aggregate_merger.py` 负责：

```text
1. 接收 repository.py 返回的多组聚合结果
2. 按 username 创建 UserAggregateFeature
3. 将小时、来源 IP、目标 IP、来源国家、来源城市、VPN 网关、行为、事件类型、结果、失败原因、认证方式、客户端软件、协议、每日事件数、会话和流量统计合并进去
4. 返回 dict[str, UserAggregateFeature]
```

---

## 3. 合并入口

```python
class AggregateMerger:
    def merge(
        self,
        user_summary_rows: list[dict],
        hour_rows: list[dict],
        source_ip_rows: list[dict],
        destination_ip_rows: list[dict],
        source_country_rows: list[dict],
        source_city_rows: list[dict],
        vpn_gateway_rows: list[dict],
        action_rows: list[dict],
        event_type_rows: list[dict],
        result_rows: list[dict],
        fail_reason_rows: list[dict],
        auth_method_rows: list[dict],
        client_software_rows: list[dict],
        protocol_rows: list[dict],
        daily_rows: list[dict],
        session_metric_rows: list[dict],
    ) -> dict[str, UserAggregateFeature]:
        ...
```

---

## 4. 合并规则

### 4.1 先合并用户总览

用户总览是骨架。

```python
features: dict[str, UserAggregateFeature] = {}

for row in user_summary_rows:
    username = row["username"]

    features[username] = UserAggregateFeature(
        username=username,
        sample_count=int(row["sample_count"]),
        failed_count=int(row["failed_count"]),
        off_hours_count=int(row.get("off_hours_count", 0)),
        unusual_ip_count=int(row.get("unusual_ip_count", 0)),
        active_days=int(row["active_days"]),
        first_seen=row.get("first_seen"),
        last_seen=row.get("last_seen"),
    )
```

---

### 4.2 合并计数字段

计数字段合并逻辑保持一致：不存在用户则跳过，`cnt` 为空则跳过或按 0 处理。

```python
for row in source_ip_rows:
    username = row["username"]
    if username not in features:
        continue
    features[username].source_ip_counts[str(row["source_ip"])] = int(row["cnt"])

for row in destination_ip_rows:
    username = row["username"]
    if username not in features:
        continue
    features[username].destination_ip_counts[str(row["destination_ip"])] = int(row["cnt"])

for row in source_country_rows:
    username = row["username"]
    if username not in features:
        continue
    features[username].source_country_counts[str(row["source_country"])] = int(row["cnt"])

for row in source_city_rows:
    username = row["username"]
    if username not in features:
        continue
    features[username].source_city_counts[str(row["source_city"])] = int(row["cnt"])

for row in vpn_gateway_rows:
    username = row["username"]
    if username not in features:
        continue
    features[username].vpn_gateway_counts[str(row["vpn_gateway"])] = int(row["cnt"])
```

同样方式合并：

```text
action_counts
event_type_counts
result_counts
fail_reason_counts
auth_method_counts
client_software_counts
protocol_counts
daily_counts
```

会话和流量统计应合并到：

```text
session_metric_summary
traffic_metric_summary
```

---

## 5. 异常聚合行处理原则

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

## 6. BaselineBuilder 职责

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
3. 计算常用来源 IP、目标 IP
4. 计算常用来源国家、来源城市
5. 计算常用 VPN 网关
6. 计算行为、事件类型、结果、失败原因、认证方式、客户端软件、协议分布
7. 计算失败率、非工作时间比例、异常 IP 标签比例
8. 计算每日事件统计
9. 计算会话时长和流量统计
```

---

## 7. 可靠性判断

第一版只根据样本数量判断基线是否可靠。

```python
is_reliable = feature.sample_count >= config.min_sample_count
```

建议默认：

```text
sample_count < 20：不可靠
sample_count >= 20：可靠
```

---

## 8. Top-N 通用函数

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
常用来源 IP
常用目标 IP
常用来源国家
常用来源城市
常用 VPN 网关
```

---

## 9. 常用维度

```python
common_active_hours = build_count_ratio_items(
    counts=feature.hour_counts,
    total=feature.sample_count,
    limit=24,
    min_ratio=config.common_hour_min_ratio,
)

common_source_ips = build_count_ratio_items(
    counts=feature.source_ip_counts,
    total=feature.sample_count,
    limit=config.top_source_ip_limit,
    min_ratio=config.common_source_ip_min_ratio,
)

common_destination_ips = build_count_ratio_items(
    counts=feature.destination_ip_counts,
    total=feature.sample_count,
    limit=config.top_destination_ip_limit,
)

common_source_countries = build_count_ratio_items(
    counts=feature.source_country_counts,
    total=feature.sample_count,
    limit=config.top_country_limit,
)

common_source_cities = build_count_ratio_items(
    counts=feature.source_city_counts,
    total=feature.sample_count,
    limit=config.top_city_limit,
    min_ratio=config.common_city_min_ratio,
)

common_vpn_gateways = build_count_ratio_items(
    counts=feature.vpn_gateway_counts,
    total=feature.sample_count,
    limit=config.top_vpn_gateway_limit,
)
```

当前登录主表没有 `endpoint` 字段，因此第一版不生成 `common_endpoints`。

---

## 10. 分布计算

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

用于生成：

```text
action_distribution
event_type_distribution
result_distribution
fail_reason_distribution
auth_method_distribution
client_software_distribution
protocol_distribution
```

当前登录主表没有 `status` 字段，因此第一版不生成 `status_distribution`；成功失败应基于 `result` / `event_type`。

---

## 11. 比例和数值统计

```python
failed_rate = 0.0 if feature.sample_count <= 0 else feature.failed_count / feature.sample_count
off_hours_rate = 0.0 if feature.sample_count <= 0 else feature.off_hours_count / feature.sample_count
unusual_ip_rate = 0.0 if feature.sample_count <= 0 else feature.unusual_ip_count / feature.sample_count
```

注意：

```text
off_hours_rate 来自 is_off_hours 聚合结果。
unusual_ip_rate 来自 is_unusual_ip 聚合结果。
它们是输入侧已有标签的统计结果，不等价于 UEBA 最终异常判定。
```

每日事件统计：

```python
window_days = max((end_time - start_time).days, 1)
active_days = max(feature.active_days, 1)

avg_daily_events = feature.sample_count / window_days
active_day_avg_events = feature.sample_count / active_days
max_daily_events = max(feature.daily_counts.values(), default=0)
```

会话和流量统计来自 `session_metric_summary` / `traffic_metric_summary`：

```text
session_duration_avg
session_duration_p50
session_duration_p95
bytes_sent_avg
bytes_recv_avg
```

---

## 12. 最终生成 UserBaseline

```python
baseline = UserBaseline(
    username=feature.username,
    sample_count=feature.sample_count,
    is_reliable=is_reliable,
    common_active_hours=common_active_hours,
    common_source_ips=common_source_ips,
    common_destination_ips=common_destination_ips,
    common_source_countries=common_source_countries,
    common_source_cities=common_source_cities,
    common_vpn_gateways=common_vpn_gateways,
    action_distribution=action_distribution,
    event_type_distribution=event_type_distribution,
    result_distribution=result_distribution,
    fail_reason_distribution=fail_reason_distribution,
    auth_method_distribution=auth_method_distribution,
    client_software_distribution=client_software_distribution,
    protocol_distribution=protocol_distribution,
    failed_rate=round(failed_rate, 6),
    off_hours_rate=round(off_hours_rate, 6),
    unusual_ip_rate=round(unusual_ip_rate, 6),
    avg_daily_events=round(avg_daily_events, 6),
    active_day_avg_events=round(active_day_avg_events, 6),
    max_daily_events=max_daily_events,
    session_duration_avg=session_duration_avg,
    session_duration_p50=session_duration_p50,
    session_duration_p95=session_duration_p95,
    bytes_sent_avg=bytes_sent_avg,
    bytes_recv_avg=bytes_recv_avg,
    baseline_start_time=start_time,
    baseline_end_time=end_time,
    model_version=config.model_version,
)
```

---

## 13. 基线构建原则

`baseline_builder.py` 必须遵守：

```text
1. 不读数据库。
2. 不写数据库。
3. 不处理原始日志。
4. 只处理聚合特征。
5. 所有 Top-N 都必须有数量限制。
6. 所有 ratio 都要防止除零。
7. 不可靠基线也可以保存，但要标记 is_reliable = false。
8. 不把 risk_score、risk_tags 当作 UEBA 第一版最终异常结论。
9. 不按 endpoint、status、location 设计当前登录主表基线。
```
