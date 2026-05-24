# UEBA v1 最终验收固化

本文档固化 `behavior-new` 分支中 UEBA v1 阶段 2-14 的最终验收结论、运行方式、测试方式、字段事实、已知边界和后续注意事项。

本文件只记录当前 UEBA v1 的离线一次性用户行为基线构建范围，不扩展新功能，不作为前端联调或实时检测设计。

---

## 1. 当前完成范围

UEBA v1 已完成如下离线构建链路：

```text
UebaRepository
-> AggregateMerger
-> BaselineBuilder
-> BaselineStore
-> UebaService
-> scripts/build_ueba_baseline.py
```

当前版本只做：

1. 从 `logs_structured` 按 `log_type` 和 `timestamp` 时间窗口读取数据库侧聚合结果。
2. 在 Python 侧按 `username` 合并聚合 rows。
3. 由聚合特征构建 `UserBaseline`。
4. 批量写入 `user_behavior_baselines`。
5. 通过 `UebaService.build_baseline_once()` 返回 `BaselineBuildResult`。
6. 通过 `scripts/build_ueba_baseline.py` 提供一次性 CLI 构建入口。

当前版本明确不做：

1. 实时检测。
2. 动态基线。
3. Kafka / Flink 流处理。
4. 前端 dashboard 联调。
5. 旧 `behavior_api` 兼容。
6. 将 `risk_score` / `risk_tags` 作为最终异常检测闭环。

---

## 2. 核心文件职责

| 文件 | 当前职责 |
|---|---|
| `src/behavior/config.py` | 定义 `UebaBaselineConfig`，控制窗口、Top-N、ratio、`model_version`、`write_batch_size` 等配置。 |
| `src/behavior/schemas.py` | 定义 `CountRatioItem`、`UserAggregateFeature`、`UserBaseline`、`BaselineBuildResult`。 |
| `src/behavior/repository.py` | 只负责数据库侧聚合查询；使用 `logs_structured`；使用 `log_type` 和 `timestamp` 时间窗口过滤；不 `SELECT *`；不拉取大量原始日志。 |
| `src/behavior/aggregate_merger.py` | 只合并 Repository 返回的聚合 rows；不访问数据库；不生成 `UserBaseline`；不保存原始日志列表。 |
| `src/behavior/baseline_builder.py` | 将 `UserAggregateFeature` 转成 `UserBaseline`；计算可靠性、Top-N、ratio、失败率、非工作时间比例、异常 IP 比例、日均事件、会话和流量指标；不访问数据库。 |
| `src/behavior/baseline_store.py` | 确保 `user_behavior_baselines` 存在、序列化、批量写入、查询 baseline；复杂字段使用 JSON 字符串；使用 `ReplacingMergeTree(created_at)`。 |
| `src/behavior/service.py` | 只做流程编排；不直接写 SQL；不直接访问 ClickHouse client。 |
| `scripts/build_ueba_baseline.py` | CLI 入口；解析参数、创建 client、组装组件、调用 `UebaService`、输出 JSON；`--help` 不应依赖 ClickHouse 连接。 |

`repository.py` 中失败登录统计口径已统一为：

```sql
result IN ('FAILED', 'FAIL') OR event_type = 'LOGIN_FAIL'
```

---

## 3. 字段事实

当前字段事实如下：

1. `logs_raw` Kafka 表中存在 `raw_message`。
2. `logs_structured` 表中实际使用 `raw_log`。
3. `raw_message` / `raw_log` 不作为 UEBA v1 核心聚合维度。
4. `logs_structured` 可以保留 `location` 作为通用扩展字段。
5. UEBA v1 来源位置使用 `src_country` / `src_city`。
6. 登录结果使用 `result`。
7. 事件类型使用 `event_type`。
8. UEBA v1 不使用独立旧字段 `status` 作为登录结果字段。
9. `status_code` 可以作为通用字段存在，但不是 UEBA 登录结果字段。
10. UEBA v1 不使用 `endpoint` 作为核心字段。
11. `result = 'FAILED'` 与 `result = 'FAIL'` 都纳入失败统计。
12. `risk_score` / `risk_tags` 只作为已有风险参考字段，不作为 UEBA v1 最终异常结论。

---

## 4. 推荐验收命令

### 4.1 Python 环境

```bash
source .venv/bin/activate
python --version
python -m pytest --version
```

### 4.2 单元测试

```bash
python -m compileall src/behavior tests/behavior scripts/build_ueba_baseline.py
python -m pytest tests/behavior -v
```

### 4.3 CLI help

```bash
python scripts/build_ueba_baseline.py --help
```

`--help` 应只解析参数并打印帮助信息，不应连接 ClickHouse。

### 4.4 空窗口构建示例

```bash
python scripts/build_ueba_baseline.py \
  --start-time "2026-05-01 00:00:00" \
  --end-time "2026-05-02 00:00:00" \
  --log-type vpn
```

### 4.5 非空窗口构建示例

```bash
python scripts/build_ueba_baseline.py \
  --start-time "2026-05-01 00:00:00" \
  --end-time "2026-05-10 00:00:00" \
  --log-type vpn \
  --model-version ueba_baseline_v1_stage13 \
  --min-sample-count 20
```

### 4.6 性能验证说明

阶段 14 本地环境验证过 100000 条日志、200 用户、每用户 500 条的构建：

```text
第一次构建 duration_seconds 约 0.485，shell time 约 0.663s
第二次构建 duration_seconds 约 0.462，shell time 约 0.643s
```

上述结果只是本机本次验证结果，不是生产 SLA 或绝对性能承诺。

---

## 5. 阶段验收摘要

### 5.1 阶段 12

阶段 12 已完成本地 ClickHouse 非空构建验证：

1. 插入 50 条 `parser = 'ueba_stage12_seed'` 测试日志。
2. 3 个用户：
   - `zhangsan`: 25 条，可靠基线。
   - `lisi`: 5 条，不可靠基线。
   - `wangwu`: 20 条，可靠基线。
3. `total_log_count = 50`。
4. 生成 3 条 `user_behavior_baselines` 结果。
5. `pytest tests/behavior -v`: 61 passed。

### 5.2 阶段 13

阶段 13 已完成本地端到端链路验证：

1. 第一次 CLI 构建成功。
2. 第二次重复 CLI 构建成功。
3. 每用户 `versions = 2` 属于 `ReplacingMergeTree` 后台合并前的多版本现象。
4. `BaselineStore.get_user_baseline` 能按 `created_at DESC` 查询最新记录。
5. `pytest tests/behavior -v`: 61 passed。

### 5.3 阶段 14

阶段 14 已完成性能与约束检查：

1. 插入 100000 条 `parser = 'ueba_stage14_perf_seed'` 性能数据。
2. 200 用户，每用户 500 条。
3. 第一次构建成功：
   - `total_user_count = 200`
   - `reliable_user_count = 200`
   - `total_log_count = 100000`
   - `duration_seconds` 约 0.485
   - shell time 约 0.663s
4. 第二次构建成功：
   - `duration_seconds` 约 0.462
   - shell time 约 0.643s
5. baseline 聚合结果：
   - `baseline_rows = 200`
   - `total_sample_count = 100000`
   - `reliable_rows = 200`
6. 每用户 `versions = 2`，为 `ReplacingMergeTree` 后台合并前的正常多版本现象。
7. `get_user_baseline` 在多版本下仍能读取最新记录。
8. `pytest tests/behavior -v`: 61 passed。

阶段 14 性能数据写入本地 ClickHouse，不属于 Git 仓库内容。

---

## 6. 真实 ClickHouse 读写链路补充验收

### 6.1 验收范围

本次补充验收验证 `behavior / UEBA` 离线 baseline CLI 可连接真实 ClickHouse，从 `log_analysis.logs_structured` 读取 `vpn` 聚合数据，并写入 `log_analysis.user_behavior_baselines`。

本次验收不包含：

1. dashboard 前端联调。
2. 实时异常检测。
3. Kafka / Flink 流处理。
4. 旧 `behavior/api.py` 兼容。
5. storage 模块重构。
6. ClickHouse schema 修改。

### 6.2 环境与服务状态

- 当前分支：`behavior-new`
- 工作区状态：`git status --short` 为空
- 最新相关提交：`dd20afe 完善 UEBA CLI ClickHouse 连接入口`
- ClickHouse 容器：`clickhouse-server`
- ClickHouse 镜像：`clickhouse/clickhouse-server:latest`
- 端口映射：`8123->8123`，`9000->9000`
- `curl http://localhost:8123/ping` 返回：`Ok.`

### 6.3 数据库与表结构

已确认：

1. `log_analysis` 数据库存在。
2. `log_analysis.logs_structured` 表存在。
3. `log_analysis.user_behavior_baselines` 表存在。
4. `logs_structured` 字段满足 `UebaRepository` 查询需求。

关键字段包括：

```text
timestamp
log_type
username
source_ip
destination_ip
src_country
src_city
vpn_gateway
action
event_type
result
fail_reason
auth_method
client_software
protocol
session_duration_sec
bytes_sent
bytes_recv
is_off_hours
is_unusual_ip
```

### 6.4 输入数据

`logs_structured` 中 `vpn` 数据分布：

```text
count = 100050
min(timestamp) = 2026-05-01 00:00:00
max(timestamp) = 2026-06-14 23:59:40
```

用户分布检查显示存在可用 `username`，前 20 个用户多为每用户 500 条，例如 `user_0010`、`user_0002`、`user_0088` 等；失败计数 `failed_cnt` 约 13-14 条/用户。

构建前，本次 `model_version = ueba_baseline_v1_clickhouse_test` 的 baseline 记录为：

```text
rows = 0
users = 0
total_sample_count = 0
```

### 6.5 CLI 真实构建命令

```bash
python scripts/build_ueba_baseline.py \
  --clickhouse-host localhost \
  --clickhouse-port 8123 \
  --clickhouse-user default \
  --clickhouse-password "" \
  --clickhouse-database log_analysis \
  --start-time "2026-05-01 00:00:00" \
  --end-time "2026-06-15 00:00:00" \
  --log-type vpn \
  --model-version ueba_baseline_v1_clickhouse_test
```

执行结果摘要：

```text
success = true
total_user_count = 203
reliable_user_count = 202
unreliable_user_count = 1
total_log_count = 100050
model_version = ueba_baseline_v1_clickhouse_test
duration_seconds = 1.334
message = saved 203 user baselines
```

### 6.6 baseline 写入检查

写入 `log_analysis.user_behavior_baselines` 后，本次 `model_version` 查询结果：

```text
rows = 203
users = 203
total_sample_count = 100050
min_created_at = 2026-05-24 12:18:50
max_created_at = 2026-05-24 12:18:50
```

明细查询可返回本次 `model_version` 记录，例如 `user_0126`、`user_0127` 等；`sample_count = 500`，`is_reliable = 1`。

### 6.7 测试结果

```bash
python scripts/build_ueba_baseline.py --help
```

结果：通过，正常输出 `usage/options`。

```bash
python -m pytest tests/behavior -v
```

结果：`66 passed`。

本阶段未修改业务代码，未修改 `src/storage/clickhouse.py`，因此未额外执行 `compileall` 和 repository 专项测试。

### 6.8 验收结论

真实 ClickHouse 读写链路验收通过。

本次验证确认：

```text
log_analysis.logs_structured
-> scripts/build_ueba_baseline.py
-> UebaRepository
-> AggregateMerger
-> BaselineBuilder
-> BaselineStore
-> UebaService
-> log_analysis.user_behavior_baselines
```

链路可用。

最终对账结果：

```text
输入 logs_structured vpn 日志数：100050
CLI total_log_count：100050
user_behavior_baselines total_sample_count：100050
```

三者一致。

---

## 7. 已知边界和后续事项

1. dashboard / `behavior/api` 前端兼容不属于 UEBA v1 当前验收范围。
2. `docs/ClickhouseManual.md` 不作为当前 UEBA v1 验收修改对象。
3. `setup_project.sh` 不作为 UEBA 最小验证路径的唯一依据。
4. `ReplacingMergeTree` 不是强事务实时去重；重复构建后可能在后台合并前看到多版本记录。
5. 如后续需要强一致展示，可考虑 `FINAL`、`OPTIMIZE`、按 `created_at` 取最新，或更严格的读查询策略；是否使用需另行评估成本。
6. `config/clickhouse.sql` 可作为当前全局初始化参考，但 UEBA 运行时 baseline 表由 `BaselineStore.ensure_table()` 保证存在。
7. 后续若进入前端展示，应单独设计 dashboard 兼容层，不得让旧 `behavior_api` 反向污染 UEBA v1 核心架构。
8. 后续若接入 API 日志，`endpoint` 相关聚合应作为新的 `log_type` 或独立扩展设计，不属于当前登录 / VPN 主表第一版核心字段。
