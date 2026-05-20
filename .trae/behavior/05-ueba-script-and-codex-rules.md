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
  --min-sample-count 20 \
  --top-ip-limit 10 \
  --top-location-limit 10 \
  --top-endpoint-limit 20
```

如果没有传入 `start-time` 和 `end-time`，可以默认使用最近 30 天。

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
  "config": {
    "min_sample_count": 20,
    "top_ip_limit": 10,
    "top_location_limit": 10,
    "top_endpoint_limit": 20,
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

## 6. 给 Codex 的开发约束

开发 UEBA 模块时必须遵守以下约束。

### 6.1 不允许一次性读取全部原始日志

禁止：

```sql
SELECT * FROM logs_structured
```

除非只是用于极小规模调试。

正式基线构建必须使用数据库侧聚合。

---

### 6.2 不允许在 Python 中保存完整用户日志列表

禁止：

```python
user_logs[username].append(log)
```

允许：

```python
user_features[username].ip_counts[ip] = count
user_features[username].endpoint_counts[endpoint] = count
```

---

### 6.3 必须使用数据库侧 GROUP BY

至少要有以下聚合：

```text
用户总览统计
用户小时分布
用户常用 IP
用户常用地区
用户常用接口
用户行为类型分布
用户状态分布
用户每日事件数
```

---

### 6.4 每个维度必须限制 Top-N

尤其是：

```text
source_ip
location
endpoint
action
status
```

其中 endpoint 必须重点限制。

---

### 6.5 endpoint 必须去参数归一化

禁止直接用完整 URL 聚合：

```text
/api/order?id=1
/api/order?id=2
```

应该归一为：

```text
/api/order
```

ClickHouse 示例：

```sql
replaceRegexpOne(endpoint, '\\?.*$', '') AS endpoint_path
```

---

### 6.6 `config.py` 不能写死成长期配置中心

允许第一版有默认配置：

```python
UebaBaselineConfig()
```

但业务逻辑必须通过配置对象接收参数。

不推荐：

```python
from config import TOP_IP_LIMIT
```

推荐：

```python
self.config.top_ip_limit
```

---

### 6.7 基线写入必须批量插入

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

### 6.8 复杂字段先存 JSON 字符串

以下字段建议存 JSON：

```text
common_active_hours
common_ips
common_locations
common_endpoints
action_distribution
status_distribution
baseline_json
```

---

### 6.9 Service 层必须作为统一入口

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

## 7. 第一版交付物清单

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
3. 生成每个用户的常用时间、IP、地区、接口、行为分布、状态分布
4. 判断基线是否可靠
5. 批量写入基线表
6. 返回本次构建统计结果
```

---

## 8. 给 Codex 的一句话任务描述

```text
实现 UEBA 离线用户行为基线构建模块：
从 ClickHouse 结构化日志表 logs_structured 中按时间范围进行数据库侧聚合，
按用户生成 UserBaseline，
批量写入 user_behavior_baselines 表，
要求不一次性读取原始日志、不保存完整日志列表、支持十万级日志数据。
```
