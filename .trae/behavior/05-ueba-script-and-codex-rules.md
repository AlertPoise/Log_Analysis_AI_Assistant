# UEBA 脚本入口与开发约束

## 1. 脚本入口

第一版建议提供一个命令行脚本：

```text
scripts/build_ueba_baseline.py
```

该脚本用于手动触发一次性基线构建。

---

## 2. 脚本职责

脚本只负责启动流程，不写业务逻辑。

它应该完成：

```text
1. 解析命令行参数
2. 构造 UebaBaselineConfig
3. 初始化数据库客户端
4. 初始化 Repository / Merger / Builder / Store / Service
5. 调用 service.build_baseline_once()
6. 打印 JSON 格式结果
```

---

## 3. 命令行参数建议

```bash
python scripts/build_ueba_baseline.py \
  --start-time "2026-04-19 00:00:00" \
  --end-time "2026-05-19 00:00:00" \
  --log-type vpn \
  --min-sample-count 20 \
  --top-source-ip-limit 10 \
  --top-destination-ip-limit 10 \
  --top-source-city-limit 10 \
  --top-vpn-gateway-limit 10
```

如果没有传入 `start-time` 和 `end-time`，可以默认使用最近 30 天。

`log_type` 当前默认可按 `vpn` 设计，但必须通过配置或参数传入，不能永久写死在 SQL 中。

---

## 4. 脚本输出示例

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

## 5. 后期接口扩展方向

当前第一版使用脚本触发。

后续可以提供 API：

```text
POST /api/behavior/baseline/build
```

请求示例：

```json
{
  "start_time": "2026-04-19 00:00:00",
  "end_time": "2026-05-19 00:00:00",
  "log_type": "vpn",
  "config": {
    "min_sample_count": 20,
    "top_source_ip_limit": 10,
    "top_destination_ip_limit": 10,
    "top_source_city_limit": 10,
    "top_vpn_gateway_limit": 10,
    "common_hour_min_ratio": 0.05,
    "model_version": "ueba_baseline_v1"
  }
}
```

接口层应该将请求体中的配置转为：

```python
UebaBaselineConfig
```

再传入：

```python
UebaService
```

不要让接口层直接操作底层模块。

---

## 6. 当前数据库字段约束

当前数据库说明以用户新提供的 `logs_structured` 登录数据主表为准。

必须明确：

```text
1. config/clickhouse.sql 仍然是过时文件，不得作为当前表结构依据。
2. 当前登录主表不包含 endpoint、status、location 字段。
3. 当前不得按 API endpoint 维度设计登录基线。
4. 当前应基于 result / event_type 统计成功失败。
5. 当前应基于 src_country / src_city 统计来源位置。
6. 当前应基于 source_ip、destination_ip、vpn_gateway、auth_method、client_software、protocol 等字段构建登录行为基线。
7. 当前可统计 is_off_hours、is_unusual_ip 的比例，但它们不能替代 UEBA 自己的基线统计。
8. 不得把 risk_score、risk_tags 作为 UEBA 第一版最终异常结论。
```

如果旧 README、旧 API 文档、旧 SQL 文件、旧 dashboard 逻辑与上述字段冲突，以用户当前要求和 `.trae/behavior/99-outdated-sources.md` 为准。

---

## 7. 给 Codex 的开发约束

开发 UEBA 模块时必须遵守以下约束。

### 7.1 不允许一次性读取全部原始日志

禁止：

```sql
SELECT * FROM logs_structured
```

除非只是用于极小规模调试。

正式基线构建必须使用数据库侧聚合。

---

### 7.2 不允许在 Python 中保存完整用户日志列表

禁止：

```python
user_logs[username].append(log)
```

允许：

```python
user_features[username].source_ip_counts[ip] = count
user_features[username].vpn_gateway_counts[gateway] = count
```

---

### 7.3 必须使用数据库侧 GROUP BY

至少要有以下聚合：

```text
用户总览统计
用户小时分布
用户常用来源 IP
用户常用目标 IP
用户常用来源国家
用户常用来源城市
用户常用 VPN 网关
用户行为类型分布
用户事件类型分布
用户结果分布
用户失败原因分布
用户认证方式、客户端软件、协议分布
用户每日事件数
用户会话时长和流量统计
```

---

### 7.4 每个维度必须限制 Top-N

尤其是：

```text
source_ip
destination_ip
src_country
src_city
vpn_gateway
fail_reason
client_software
```

---

### 7.5 当前不做 endpoint 归一化

当前登录主表没有 `endpoint` 字段。

因此第一版不得要求：

```text
API endpoint Top-N
endpoint 查询参数归一化
旧接口类基线字段
```

如果后续接入 API 日志，API endpoint 相关聚合应作为另一类 `log_type` 或另一张表的扩展，不属于当前登录主表第一版核心字段。

---

### 7.6 `config.py` 不能写死成长期配置中心

允许第一版有默认配置：

```python
UebaBaselineConfig()
```

但业务逻辑必须通过配置对象接收参数。

不推荐：

```python
from config import TOP_SOURCE_IP_LIMIT
```

推荐：

```python
self.config.top_source_ip_limit
```

---

### 7.7 基线写入必须批量插入

禁止：

```python
for baseline in baselines:
    insert_one(baseline)
```

推荐：

```python
insert_many(rows)
```

或按批次：

```python
for batch in chunks(rows, batch_size):
    insert_many(batch)
```

---

### 7.8 复杂字段先存 JSON 字符串

以下字段建议存 JSON：

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

### 7.9 Service 层必须作为统一入口

外部调用应该走：

```python
UebaService.build_baseline_once()
```

不要直接在脚本里写：

```python
repository.fetch_xxx()
builder.build_xxx()
store.save_xxx()
```

---

## 8. 第一版交付物清单

代码文件：

```text
src/behavior/__init__.py
src/behavior/config.py
src/behavior/schemas.py
src/behavior/repository.py
src/behavior/aggregate_merger.py
src/behavior/baseline_builder.py
src/behavior/baseline_store.py
src/behavior/service.py
scripts/build_ueba_baseline.py
```

数据库表：

```text
logs_structured
user_behavior_baselines
```

核心能力：

```text
1. 指定时间范围构建用户基线
2. 使用数据库侧聚合，不直接读取全部原始日志
3. 生成每个用户的常用时间、来源 IP、目标 IP、来源国家、来源城市、VPN 网关、行为分布、事件类型分布、结果分布、认证方式分布、客户端软件分布、协议分布、会话与流量统计
4. 判断基线是否可靠
5. 批量写入基线表
6. 返回本次构建统计结果
```

---

## 9. 给 Codex 的一句话任务描述

```text
实现 UEBA 离线用户行为基线构建模块：
从 ClickHouse 结构化日志表 logs_structured 中按时间范围和 log_type 进行数据库侧聚合，
按用户生成 UserBaseline，
批量写入 user_behavior_baselines 表，
要求不一次性读取原始日志、不保存完整日志列表、支持十万级日志数据，
并且当前登录主表不按 endpoint、status、location 作为核心字段设计。
```
