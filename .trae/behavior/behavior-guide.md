# UEBA 离线用户行为基线构建任务分步执行约束

## 0. 总体定位

当前 UEBA 模块第一版的目标是：

> 从结构化日志表 `logs_structured` 中按时间范围做数据库侧聚合，Python 侧按用户合并聚合结果，生成用户行为基线，并批量写入 `user_behavior_baselines` 表。

当前阶段不是完整实时 UEBA 系统，不是旧版 Behavior 异常检测接口，也不是旧 dashboard 的数据适配层。

当前第一版只做：

1. 数据库侧聚合结构化日志；
2. Python 侧合并聚合特征；
3. 构建用户行为基线；
4. 批量写入用户基线表；
5. 提供统一 Service 入口；
6. 提供一次性构建脚本；
7. 提供可验证的单元测试、SQL 约束测试和端到端验证。

当前第一版不做：

1. Kafka 实时消费；
2. Flink / 实时流处理；
3. 实时基线更新；
4. 滑动窗口增量更新；
5. 复杂机器学习模型；
6. 深度用户画像系统；
7. 实时告警闭环；
8. 旧前端接口兼容；
9. 根据旧 dashboard 表名反向修改 UEBA 核心设计；
10. 直接读取十万级原始日志到 Python。

---

## 0.1 当前登录数据主表阶段依据

当前数据库说明以用户新提供的 `logs_structured` 登录 / VPN 行为数据主表为准。

当前主表字段包括：

```text
timestamp, log_type, username, dept, role, action, event_type, result, fail_reason, source_ip, destination_ip, vpn_gateway, src_country, src_city, protocol, auth_method, client_software, session_id, is_off_hours, is_unusual_ip, session_duration_sec, bytes_sent, bytes_recv, risk_score, risk_tags, raw_message, parser, parse_status, collected_at
```

阶段性约束：

```text
1. 当前表是登录 / VPN 行为数据主表。
2. 当前表没有 endpoint、status、location 字段，不得按这些旧字段设计登录基线。
3. 登录结果使用 result，事件类型使用 event_type，来源位置使用 src_country、src_city。
4. is_off_hours、is_unusual_ip 是输入侧已有标签，只能统计比例，不能替代 UEBA 自己的基线统计。
5. risk_score、risk_tags 只能作为历史风险参考，不作为 UEBA 第一版最终异常结论。
6. 查询应优先使用 log_type 和 timestamp 时间范围过滤；默认 log_type 可按 vpn 设计，但必须通过配置或参数传入。
```

---

## 1. 执行任务前必须阅读的文件

在执行任何代码修改前，GPT / Codex 必须先阅读以下文件：

```text
.trae/behavior/UEBA-module-guide.md
.trae/behavior/00-ueba-overview.md
.trae/behavior/01-ueba-module-structure.md
.trae/behavior/02-ueba-database-aggregation.md
.trae/behavior/03-ueba-baseline-build.md
.trae/behavior/04-ueba-storage-and-service.md
.trae/behavior/05-ueba-script-and-codex-rules.md
.trae/behavior/99-outdated-sources.md
```

执行规则：

1. 先阅读 `.trae/behavior` 下的 prompt 文件，再检查真实代码文件树；
2. 不允许未阅读 `.trae/behavior` 约束文件就直接写代码；
3. 不需要优先阅读旧设计文件、旧 API 文档、旧 dashboard 文档；
4. 如果旧代码、旧 README、旧 SQL、旧前端接口与 `.trae/behavior` 当前要求冲突，以 `.trae/behavior` 当前要求为准；
5. 如果用户当前明确要求与 prompt 文件冲突，以用户当前明确要求为准。

推荐优先级：

```text
用户当前明确要求
> 当前本约束文件
> .trae/behavior/99-outdated-sources.md
> .trae/behavior/UEBA-module-guide.md
> .trae/behavior/00~05
> 根目录通用 prompt
> 当前真实代码文件树
> 旧 README / 旧 API / 旧 dashboard / 旧 SQL
```

---

## 2. 总体数据流

当前 UEBA 第一版的数据流必须保持为：

```text
logs_structured
↓
数据库侧 GROUP BY 聚合
↓
UserAggregateFeature
↓
UserBaseline
↓
user_behavior_baselines
↓
BaselineBuildResult
```

禁止改成：

```text
logs_structured
↓
SELECT * 拉取原始日志
↓
Python 按用户保存完整日志列表
↓
Python 全量循环计算
```

---

## 3. 推荐文件结构

当前第一版推荐结构如下：

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

scripts/
└── build_ueba_baseline.py
```

各文件职责必须保持清晰：

| 文件 | 职责 |
|---|---|
| `config.py` | UEBA 基线构建配置 |
| `schemas.py` | 内部数据结构 |
| `repository.py` | ClickHouse / 数据库聚合查询 |
| `aggregate_merger.py` | 多组聚合结果按用户合并 |
| `baseline_builder.py` | 聚合特征转用户行为基线 |
| `baseline_store.py` | 建表、序列化、批量写入基线 |
| `service.py` | 对外统一编排入口 |
| `scripts/build_ueba_baseline.py` | 命令行一次性构建入口 |

---

# 4. 分阶段执行计划

## 阶段 0：开发前约束确认

### 目标

确认当前任务边界，防止被旧代码、旧接口、旧 dashboard、旧 SQL 文件带偏。

### 输入

```text
.trae/behavior/UEBA-module-guide.md
.trae/behavior/00-ueba-overview.md
.trae/behavior/01-ueba-module-structure.md
.trae/behavior/02-ueba-database-aggregation.md
.trae/behavior/03-ueba-baseline-build.md
.trae/behavior/04-ueba-storage-and-service.md
.trae/behavior/05-ueba-script-and-codex-rules.md
.trae/behavior/99-outdated-sources.md
当前真实文件树
```

### 输出

```text
开发边界确认结果：
1. 当前只做离线一次性用户行为基线构建
2. 不做实时异常检测
3. 不做旧前端接口兼容
4. 不根据旧 config/clickhouse.sql 固化当前设计
5. 不根据旧 dashboard 表名反向设计 UEBA
6. 外部入口最终走 UebaService.build_baseline_once()
```

### 验收标准

1. 已明确当前任务不是完整实时 UEBA；
2. 已明确旧 Behavior 异常检测逻辑不是当前核心目标；
3. 已明确旧 dashboard 表名不能反向约束当前输出表；
4. 已明确正式流程禁止 `SELECT * FROM logs_structured`。

---

## 阶段 1：确认当前代码结构与缺口

### 目标

检查当前仓库 `src/behavior`、`scripts`、`tests` 中已有文件，避免重复创建、误删、覆盖已有代码。

### 输入

```text
当前仓库真实文件树
src/behavior/
scripts/
tests/
.trae/behavior/01-ueba-module-structure.md
```

### 输出

```text
文件差距清单：
1. 已存在文件
2. 缺失文件
3. 需要重构的文件
4. 不应修改的文件
5. 当前阶段不处理的历史异常检测文件
```

### 需要使用的设计文件

```text
.trae/behavior/01-ueba-module-structure.md
.trae/behavior/UEBA-module-guide.md
.trae/behavior/99-outdated-sources.md
```

### 验收标准

确认是否需要新增或重构：

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

---

## 阶段 2：实现配置对象 `config.py`

### 目标

建立统一配置对象，后续模块通过 `UebaBaselineConfig` 获取配置，不直接依赖全局常量。

### 输入

```text
.trae/behavior/01-ueba-module-structure.md
.trae/behavior/UEBA-module-guide.md
```

### 输出

```text
src/behavior/config.py
```

### 配置对象建议包含

```text
baseline_window_days
min_sample_count
top_source_ip_limit
top_destination_ip_limit
top_source_country_limit
top_source_city_limit
top_vpn_gateway_limit
top_fail_reason_limit
top_client_software_limit
common_hour_min_ratio
common_source_ip_min_ratio
common_source_city_min_ratio
model_version
write_batch_size
```

### 约束

1. 不允许把 `config.py` 写死成长期最终配置中心；
2. 后续应允许脚本参数、API 参数、环境变量或数据库配置覆盖；
3. 业务模块通过 `self.config.xxx` 读取配置；
4. 不允许到处散落硬编码阈值。

### 验收标准

1. 存在 `UebaBaselineConfig`；
2. Builder、Repository、Store、Service 可通过配置对象控制行为；
3. CLI 参数可覆盖默认配置。

---

## 阶段 3：实现稳定数据结构 `schemas.py`

### 目标

定义 UEBA 内部统一数据结构，使数据库字段变化只影响 Repository，不影响 Merger、Builder、Store、Service。

### 输入

```text
.trae/behavior/01-ueba-module-structure.md
.trae/behavior/03-ueba-baseline-build.md
.trae/behavior/04-ueba-storage-and-service.md
```

### 输出

```text
src/behavior/schemas.py
```

### 必须包含的数据结构

```text
CountRatioItem
UserAggregateFeature
UserBaseline
BaselineBuildResult
```

### `UserAggregateFeature` 至少包含

```text
username
sample_count
failed_count
off_hours_count
unusual_ip_count
active_days
first_seen
last_seen
hour_counts
source_ip_counts
destination_ip_counts
source_country_counts
source_city_counts
vpn_gateway_counts
action_counts
event_type_counts
result_counts
fail_reason_counts
auth_method_counts
client_software_counts
protocol_counts
daily_counts
session_metric_summary
traffic_metric_summary
```

### `UserBaseline` 至少包含

```text
username
sample_count
is_reliable
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
failed_rate
off_hours_rate
unusual_ip_rate
avg_daily_events
active_day_avg_events
max_daily_events
baseline_start_time
baseline_end_time
model_version
```

### `BaselineBuildResult` 至少包含

```text
success
baseline_start_time
baseline_end_time
total_user_count
reliable_user_count
unreliable_user_count
total_log_count
model_version
duration_seconds
message
```

### 验收标准

1. `schemas.py` 不访问数据库；
2. `schemas.py` 不写业务流程；
3. `UserAggregateFeature` 不保存原始日志列表；
4. 所有后续模块统一引用这些数据结构。

---

## 阶段 4：实现数据库聚合读取层 `repository.py`

### 目标

从 `logs_structured` 中按时间窗口读取聚合结果。Repository 只负责数据库侧聚合查询，不生成基线，不做异常判断。

### 输入

```text
ClickHouse client 或项目现有数据库客户端
logs_structured 表
start_time
end_time
UebaBaselineConfig
.trae/behavior/02-ueba-database-aggregation.md
```

### 输出

```text
src/behavior/repository.py
```

### 必须提供的方法

```text
fetch_user_summary(start_time, end_time)
fetch_hour_distribution(start_time, end_time)
fetch_top_source_ips(start_time, end_time, limit, log_type)
fetch_top_destination_ips(start_time, end_time, limit, log_type)
fetch_top_source_countries(start_time, end_time, limit, log_type)
fetch_top_source_cities(start_time, end_time, limit, log_type)
fetch_top_vpn_gateways(start_time, end_time, limit, log_type)
fetch_action_distribution(start_time, end_time, log_type)
fetch_event_type_distribution(start_time, end_time, log_type)
fetch_result_distribution(start_time, end_time, log_type)
fetch_fail_reason_distribution(start_time, end_time, limit, log_type)
fetch_auth_method_distribution(start_time, end_time, log_type)
fetch_client_software_distribution(start_time, end_time, limit, log_type)
fetch_protocol_distribution(start_time, end_time, log_type)
fetch_daily_event_counts(start_time, end_time, log_type)
fetch_session_metric_summary(start_time, end_time, log_type)
```

### 必须实现的聚合维度

```text
1. 用户总览统计
2. 用户小时分布
3. 用户常用来源 IP Top-N
4. 用户常用目标 IP Top-N
5. 用户常用来源国家 Top-N
6. 用户常用来源城市 Top-N
7. 用户常用 VPN 网关 Top-N
8. 用户行为类型分布
9. 用户事件类型分布
10. 用户结果分布
11. 用户失败原因分布
12. 用户认证方式、客户端软件、协议分布
13. 用户每日事件数
14. 会话时长与流量统计
```

### 强制约束

1. 禁止正式流程出现 `SELECT * FROM logs_structured`；
2. 禁止 Python 逐条循环十万级原始日志；
3. 每个维度使用独立 SQL，不写一个巨型 SQL；
4. 所有 SQL 使用同一个 `start_time ~ end_time` 时间窗口；
5. `start_time`、`end_time`、`limit` 必须参数化；
6. 过滤空用户名；
7. 字段名差异只允许在 `repository.py` 中通过 SQL 别名适配；
8. 当前登录主表不包含 endpoint、status、location，不得按这些旧字段设计登录基线；
9. Top-N 维度必须限制数量，尤其是 source_ip、destination_ip、src_city、vpn_gateway、client_software。

### 验收标准

1. 所有查询函数只返回聚合结果；
2. SQL 中不存在正式流程 `SELECT *`；
3. SQL 中存在 `GROUP BY`；
4. 查询参数不是字符串拼接；
5. Top-N 查询有数量限制；
6. 当前登录主表不要求 endpoint 归一化；API endpoint 聚合属于后续扩展。

---

## 阶段 5：实现聚合结果合并层 `aggregate_merger.py`

### 目标

把 Repository 返回的多组聚合结果按 `username` 合并成：

```text
dict[str, UserAggregateFeature]
```

### 输入

```text
user_summary_rows
hour_rows
source_ip_rows
destination_ip_rows
source_country_rows
source_city_rows
vpn_gateway_rows
action_rows
event_type_rows
result_rows
fail_reason_rows
auth_method_rows
client_software_rows
protocol_rows
daily_rows
session_metric_rows
```

### 输出

```text
src/behavior/aggregate_merger.py
dict[str, UserAggregateFeature]
```

### 需要使用的设计文件

```text
.trae/behavior/03-ueba-baseline-build.md
.trae/behavior/UEBA-module-guide.md
```

### 执行逻辑

1. 根据 `user_summary_rows` 创建用户骨架；
2. 合并小时分布到 `hour_counts`；
3. 合并来源 IP 分布到 `source_ip_counts`；
4. 合并目标 IP 分布到 `destination_ip_counts`；
5. 合并来源国家分布到 `source_country_counts`；
6. 合并来源城市分布到 `source_city_counts`；
7. 合并 VPN 网关分布到 `vpn_gateway_counts`；
8. 合并 action、event_type、result、fail_reason、auth_method、client_software、protocol 分布；
9. 合并每日事件数、会话时长和流量统计；
10. 对异常聚合行做跳过或降级处理。

### 异常处理策略

```text
1. username 不存在：跳过
2. 某个维度为空：保留空 dict
3. cnt 为空或无法转换：跳过或按 0
4. 字段缺失：记录日志并跳过该行
5. user_summary 中缺失 username 或 sample_count：跳过该用户
```

### 验收标准

1. `aggregate_merger.py` 不读数据库；
2. `aggregate_merger.py` 不写数据库；
3. `aggregate_merger.py` 不生成 `UserBaseline`；
4. 缺少某个维度的数据时不会导致整体失败；
5. 返回值稳定为 `dict[str, UserAggregateFeature]`。

---

## 阶段 6：实现基线生成层 `baseline_builder.py`

### 目标

将：

```text
dict[str, UserAggregateFeature]
```

转换为：

```text
list[UserBaseline]
```

### 输入

```text
dict[str, UserAggregateFeature]
start_time
end_time
UebaBaselineConfig
```

### 输出

```text
src/behavior/baseline_builder.py
list[UserBaseline]
```

### 需要使用的设计文件

```text
.trae/behavior/03-ueba-baseline-build.md
.trae/behavior/UEBA-module-guide.md
```

### 必须计算的内容

```text
1. is_reliable
2. common_active_hours
3. common_source_ips
4. common_destination_ips
5. common_source_countries
6. common_source_cities
7. common_vpn_gateways
8. action_distribution
9. event_type_distribution
10. result_distribution
11. fail_reason_distribution
12. auth_method_distribution
13. client_software_distribution
14. protocol_distribution
15. failed_rate
16. off_hours_rate
17. unusual_ip_rate
18. avg_daily_events
19. active_day_avg_events
20. max_daily_events
21. session_duration_avg
22. session_duration_p50
23. session_duration_p95
24. bytes_sent_avg
25. bytes_recv_avg
26. baseline_start_time
27. baseline_end_time
28. model_version
```

### 关键规则

#### 可靠性判断

第一版可靠性判断保持简单：

```text
sample_count >= config.min_sample_count
```

#### Top-N + ratio

以下字段需要按 count 排序，并计算 ratio：

```text
common_active_hours
common_source_ips
common_destination_ips
common_source_countries
common_source_cities
common_vpn_gateways
```

每项至少包含：

```text
value
count
ratio
```

#### 分布计算

```text
action_distribution = action_count / sample_count
result_distribution = result_count / sample_count
event_type_distribution = event_type_count / sample_count
```

#### 失败率

```text
failed_rate = failed_count / sample_count
```

必须防止除零。

#### 每日事件统计

```text
avg_daily_events = sample_count / 基线窗口天数
active_day_avg_events = sample_count / 用户实际活跃天数
max_daily_events = max(daily_counts)
```

### 验收标准

1. `baseline_builder.py` 不读取数据库；
2. `baseline_builder.py` 不写入数据库；
3. `baseline_builder.py` 不处理原始日志；
4. 所有 ratio 保留合理精度；
5. 所有除法防止除零；
6. 不可靠基线也保存，但 `is_reliable = false`；
7. Top-N 均有数量限制。

---

## 阶段 7：实现基线存储层 `baseline_store.py`

### 目标

将 `UserBaseline` 序列化为数据库行，确保基线表存在，并批量写入 `user_behavior_baselines`。

### 输入

```text
list[UserBaseline]
ClickHouse client
UebaBaselineConfig.write_batch_size
```

### 输出

```text
src/behavior/baseline_store.py
user_behavior_baselines 表
写入行数 saved_count
```

### 需要使用的设计文件

```text
.trae/behavior/04-ueba-storage-and-service.md
.trae/behavior/UEBA-module-guide.md
```

### 输出表

```text
user_behavior_baselines
```

### 表字段至少包含

```text
username
sample_count
is_reliable
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
failed_rate
off_hours_rate
unusual_ip_rate
session_metric_summary
traffic_metric_summary
avg_daily_events
active_day_avg_events
max_daily_events
baseline_start_time
baseline_end_time
model_version
baseline_json
created_at
```

### 存储规则

1. 复杂字段第一版使用 JSON 字符串保存；
2. 写入必须批量插入；
3. 不允许逐条 insert；
4. 建议使用 `ReplacingMergeTree(created_at)`；
5. 查询最新基线时不能假设 ReplacingMergeTree 已经强实时去重，应按 `created_at DESC` 或 `FINAL` 处理。

### 必须提供的方法

```text
ensure_table()
baseline_to_row(baseline)
save_baselines(baselines)
get_user_baseline(username, model_version=None)  # 可选
```

### 验收标准

1. `baseline_store.py` 不读取 `logs_structured`；
2. `baseline_store.py` 不生成基线；
3. 批量写入，不逐条插入；
4. 复杂字段全部 JSON 序列化；
5. 返回实际写入数量；
6. 表结构不依赖旧 dashboard 表名。

---

## 阶段 8：实现统一服务入口 `service.py`

### 目标

把 Repository、Merger、Builder、Store 串成完整一次性构建链路，对外只暴露统一入口：

```text
UebaService.build_baseline_once(start_time, end_time)
```

### 输入

```text
start_time
end_time
UebaBaselineConfig
UebaRepository
AggregateMerger
BaselineBuilder
BaselineStore
```

### 输出

```text
src/behavior/service.py
BaselineBuildResult
```

### 需要使用的设计文件

```text
.trae/behavior/01-ueba-module-structure.md
.trae/behavior/04-ueba-storage-and-service.md
.trae/behavior/UEBA-module-guide.md
```

### 执行流程

```text
1. 记录开始时间
2. baseline_store.ensure_table()
3. repository.fetch_user_summary()
4. repository.fetch_hour_distribution()
5. repository.fetch_top_source_ips()
6. repository.fetch_top_destination_ips()
7. repository.fetch_top_source_countries()
8. repository.fetch_top_source_cities()
9. repository.fetch_top_vpn_gateways()
10. repository.fetch_action_distribution()
11. repository.fetch_event_type_distribution()
12. repository.fetch_result_distribution()
13. repository.fetch_fail_reason_distribution()
14. repository.fetch_auth_method_distribution()
15. repository.fetch_client_software_distribution()
16. repository.fetch_protocol_distribution()
17. repository.fetch_daily_event_counts()
18. repository.fetch_session_metric_summary()
19. aggregate_merger.merge()
20. baseline_builder.build_baselines()
21. baseline_store.save_baselines()
22. 统计 reliable / unreliable / total_log_count
23. 返回 BaselineBuildResult
```

### 验收标准

1. 外部入口统一为 `UebaService.build_baseline_once()`；
2. Service 只编排流程，不写复杂 SQL；
3. Service 只编排流程，不承载复杂基线算法；
4. 成功时返回结构化 `BaselineBuildResult`；
5. 出错时返回或抛出可定位错误，不静默失败；
6. 脚本中不重复实现 Service 内部流程。

---

## 阶段 9：实现命令行脚本 `scripts/build_ueba_baseline.py`

### 目标

提供第一版可手动运行的入口，用于触发一次性基线构建并打印 JSON 结果。

### 输入参数

```text
--start-time
--end-time
--min-sample-count
--top-source-ip-limit
--top-destination-ip-limit
--top-source-city-limit
--top-vpn-gateway-limit
--model-version
```

### 输出

```text
scripts/build_ueba_baseline.py
JSON 格式 BaselineBuildResult
```

### 需要使用的设计文件

```text
.trae/behavior/05-ueba-script-and-codex-rules.md
.trae/behavior/UEBA-module-guide.md
```

### 脚本职责

脚本只允许做：

1. 解析 CLI 参数；
2. 构造 `UebaBaselineConfig`；
3. 初始化数据库 client；
4. 初始化 Repository、Merger、Builder、Store、Service；
5. 调用 `service.build_baseline_once()`；
6. 打印 JSON 结果。

脚本不允许做：

1. 不直接写 SQL；
2. 不直接调用多个 repository 方法拼流程；
3. 不直接生成基线；
4. 不直接写数据库；
5. 不写业务逻辑。

### 建议运行命令

```bash
python scripts/build_ueba_baseline.py \
  --start-time "2026-04-19 00:00:00" \
  --end-time "2026-05-19 00:00:00" \
  --min-sample-count 20 \
  --top-source-ip-limit 10 \
  --top-destination-ip-limit 10 \
  --top-source-city-limit 10 \
  --top-vpn-gateway-limit 10
```

### 验收标准

1. 脚本只调用 `service.build_baseline_once()`；
2. 输出为 JSON；
3. 可被 CI、前端适配层或 AI 模块读取。

---

## 阶段 10：编写单元测试

### 目标

不依赖真实 ClickHouse，先验证配置、数据结构、聚合合并、基线生成、序列化、Service 编排逻辑正确。

### 输入

```text
mock 聚合结果
mock repository
mock store
UebaBaselineConfig
```

### 输出

```text
tests/behavior/test_ueba_config.py
tests/behavior/test_ueba_aggregate_merger.py
tests/behavior/test_ueba_baseline_builder.py
tests/behavior/test_ueba_baseline_store.py
tests/behavior/test_ueba_service.py
```

### 测试重点

```text
1. sample_count < min_sample_count 时 is_reliable = false
2. sample_count >= min_sample_count 时 is_reliable = true
3. common_active_hours 按 ratio 阈值过滤
4. common_source_ips、common_destination_ips、common_source_countries、common_source_cities、common_vpn_gateways 按 Top-N 和 ratio 过滤
5. result_distribution、event_type_distribution 正确
6. off_hours_rate、unusual_ip_rate 正确
7. session_duration_sec、bytes_sent、bytes_recv 统计正确
8. failed_rate 正确
7. avg_daily_events 正确
8. active_day_avg_events 正确
9. max_daily_events 正确
10. 空维度不会崩溃
11. 缺失 username/sample_count 的 summary 行被跳过
12. baseline_to_row 能正确 JSON 序列化
13. service 调用顺序正确
```

### 验收标准

```bash
pytest tests/behavior -v
```

至少通过：

```text
config 测试
schema 测试
merger 测试
builder 测试
store 序列化测试
service mock 编排测试
```

---

## 阶段 11：编写 Repository SQL 层测试

### 目标

验证 Repository 生成和执行的是聚合查询，而不是原始日志查询；验证参数化查询、Top-N、log_type 与时间范围过滤等约束。

### 输入

```text
mock ClickHouse client
repository.py
```

### 输出

```text
tests/behavior/test_ueba_repository.py
```

### 需要使用的设计文件

```text
.trae/behavior/02-ueba-database-aggregation.md
.trae/behavior/05-ueba-script-and-codex-rules.md
```

### 测试重点

```text
1. query 中不出现 SELECT *
2. query 中出现 GROUP BY
3. query 中包含时间过滤
4. query 使用 parameters
5. Top-N 查询包含 LIMIT 或等价限制
6. 查询包含 log_type 过滤
7. 使用 result / event_type 统计成功失败
8. 使用 src_country / src_city 统计来源位置
9. 空 username 被过滤
```

### 验收标准

1. 所有 Repository 方法被测试覆盖；
2. SQL 约束检查通过；
3. 不把 `start_time`、`end_time`、`limit` 直接拼进 SQL；
4. 不存在正式流程读取完整原始日志。

---

## 阶段 12：准备本地或测试库验证数据

### 目标

准备能覆盖正常用户、不可靠用户、多来源 IP、多目标 IP、多来源国家城市、多 VPN 网关、多认证方式、多客户端软件、多协议、多结果和多日期的结构化登录日志数据，用于真实链路验证。

### 输入

```text
logs_structured 测试表
测试日志数据
```

### 输出

```text
可运行的测试数据集
```

### 测试数据至少覆盖

```text
1. zhangsan：样本数 >= 20，可靠基线
2. lisi：样本数 < 20，不可靠基线
3. wangwu：多个 IP、多个地区
4. zhaoliu：多个 vpn_gateway、auth_method、client_software、protocol
5. 空 username：应被过滤
6. result 包含 SUCCESS、FAIL，event_type 包含 LOGIN_SUCCESS、LOGIN_FAIL
7. src_country、src_city 覆盖多个来源位置
8. session_duration_sec、bytes_sent、bytes_recv 覆盖会话与流量统计
9. 多日期数据：验证 avg_daily_events、active_day_avg_events、max_daily_events
```

### 验收标准

1. Repository 聚合查询能查出结果；
2. result / event_type 能正确统计成功失败；
3. src_country / src_city 能正确统计来源位置；
4. 空 username 不进入基线；
5. 至少出现可靠和不可靠两类基线。

---

## 阶段 13：端到端运行验证

### 目标

使用脚本执行完整链路，从 `logs_structured` 聚合数据，生成基线，写入 `user_behavior_baselines`，输出构建结果。

### 输入

```text
logs_structured
scripts/build_ueba_baseline.py
ClickHouse 连接配置
```

### 输出

```text
user_behavior_baselines 表中的基线记录
脚本 JSON 输出
```

### 建议命令

```bash
python scripts/build_ueba_baseline.py \
  --start-time "2026-04-19 00:00:00" \
  --end-time "2026-05-19 00:00:00" \
  --min-sample-count 20 \
  --top-source-ip-limit 10 \
  --top-destination-ip-limit 10 \
  --top-source-city-limit 10 \
  --top-vpn-gateway-limit 10
```

### 预期输出结构

```json
{
  "success": true,
  "baseline_start_time": "2026-04-19 00:00:00",
  "baseline_end_time": "2026-05-19 00:00:00",
  "total_user_count": 3,
  "reliable_user_count": 2,
  "unreliable_user_count": 1,
  "total_log_count": 1000,
  "model_version": "ueba_baseline_v1",
  "duration_seconds": 1.23,
  "message": "saved 3 user baselines"
}
```

### 验收标准

1. 脚本可运行；
2. 输出 JSON；
3. 数据库中存在 `user_behavior_baselines`；
4. 表中能查到各用户基线；
5. 可靠和不可靠用户统计正确；
6. 新登录字段对应的复杂字段是 JSON 字符串；
7. endpoint 不作为当前登录主表核心字段。

---

## 阶段 14：性能与约束检查

### 目标

确认实现没有违背“支撑十万级日志”的核心原则。

### 输入

```text
repository.py
aggregate_merger.py
baseline_builder.py
baseline_store.py
service.py
测试数据或模拟大数据聚合结果
```

### 输出

```text
约束检查结果
性能风险清单
需要优化的问题
```

### 必查项

```text
1. 是否存在正式流程 SELECT *
2. 是否存在 user_logs[username].append(log)
3. 是否保存每个用户完整历史日志列表
4. 是否所有 Top-N 都有限制
5. 是否错误按 endpoint 设计当前登录基线
6. 是否逐条 insert
7. 是否把旧 dashboard 表名作为当前输出表
8. 是否把 config.py 写成不可覆盖的全局配置中心
9. 是否把异常检测/实时检测塞进第一版
10. 是否绕过 UebaService 直接拼流程
```

### 验收标准

所有检查项必须通过。

如不通过，必须先修复，不得继续进入下一阶段。

---

## 阶段 15：文档与 Codex 提词固化

### 目标

把实现方式、运行方式、测试方式、约束和未完成项固化，避免后续开发再次偏离当前第一版目标。

### 输入

```text
已实现代码
测试命令
实际运行结果
.trae/behavior/05-ueba-script-and-codex-rules.md
.trae/behavior/99-outdated-sources.md
```

### 输出

```text
运行说明
测试说明
已完成清单
未完成清单
Codex 后续开发约束
```

### 文档必须包含

```text
1. Codex 执行任务前必须阅读 .trae/behavior prompt 文件
2. 旧 config/clickhouse.sql 不是当前核心依据
3. 旧 docs/behavior_api.md 不是当前核心依据
4. 旧 dashboard 表名不是当前核心依据
5. 当前运行命令
6. 当前测试命令
7. 当前第一版不做事项
8. 当前模块真实数据流
```

### 验收标准

1. 后续 Codex 能根据文档继续开发；
2. 后续 Codex 不会回退到旧 Behavior 异常检测接口；
3. 后续 Codex 不会把 dashboard 旧需求当成 UEBA 核心设计；
4. 后续 Codex 不会绕过数据库侧聚合。

---

# 5. 推荐执行顺序

```text
阶段 0：开发前约束确认
阶段 1：确认当前代码结构与缺口
阶段 2：实现 config.py
阶段 3：实现 schemas.py
阶段 4：实现 repository.py
阶段 5：实现 aggregate_merger.py
阶段 6：实现 baseline_builder.py
阶段 7：实现 baseline_store.py
阶段 8：实现 service.py
阶段 9：实现 scripts/build_ueba_baseline.py
阶段 10：编写单元测试
阶段 11：编写 Repository SQL 层测试
阶段 12：准备测试数据
阶段 13：端到端运行验证
阶段 14：性能与约束检查
阶段 15：文档与 Codex 提词固化
```

---

# 6. 每次开发任务完成后的强制自检

每完成一个阶段，必须至少检查以下内容：

```text
1. 是否违背当前阶段目标
2. 是否引入了第一版不做的功能
3. 是否读取了原始日志
4. 是否绕过数据库侧聚合
5. 是否保存了完整日志列表
6. 是否绕过 UebaService
7. 是否让脚本承担了业务逻辑
8. 是否根据旧 dashboard / 旧 API / 旧 SQL 反向改了设计
9. 是否缺少对应测试
10. 是否输出可验证结果
```

如果任一项不通过，必须先修复，不得继续进入下一阶段。

---

# 7. 当前最容易出错的地方

必须重点避免：

```text
1. 直接 SELECT * FROM logs_structured
2. Python 保存每个用户的完整日志列表
3. 把旧 Behavior 异常检测接口当成当前目标
4. 为了旧 dashboard 表名修改 UEBA 输出表
5. 把实时检测、异常评分、Kafka、Flink 提前塞进第一版
6. 在 scripts/build_ueba_baseline.py 中写业务逻辑
7. 在 service.py 中堆复杂 SQL 或复杂算法
8. 在 repository.py 之外适配数据库字段差异
9. 把 endpoint、status、location 当作当前登录主表核心字段
10. 对 user_behavior_baselines 逐条 insert
```

---

# 8. 最终验收目标

当前第一版完成后，应满足：

```text
1. 存在清晰的 src/behavior 模块分层
2. 能从 logs_structured 按时间窗口聚合日志
3. 能生成 UserAggregateFeature
4. 能生成 UserBaseline
5. 能批量写入 user_behavior_baselines
6. 能通过 UebaService.build_baseline_once() 一次性构建
7. 能通过 scripts/build_ueba_baseline.py 手动运行
8. 能通过单元测试、SQL 约束测试、端到端测试
9. 不依赖旧 dashboard 表名
10. 不读取完整原始日志
11. 支持十万级日志数据的设计原则
```

最终目标链路必须保持为：

```text
logs_structured
↓
数据库侧聚合
↓
UserAggregateFeature
↓
UserBaseline
↓
user_behavior_baselines
↓
BaselineBuildResult
```