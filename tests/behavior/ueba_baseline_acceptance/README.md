# UEBA Baseline Acceptance Tool

本目录提供 `behavior / UEBA` baseline 人工验收工具。

## 当前能力

第 1 部分只生成理论准线文件：

```text
.tox/ueba_baseline_acceptance/expected_baselines.json
.tox/ueba_baseline_acceptance/fixture_summary.json
.tox/ueba_baseline_acceptance/run_state.json
```

第 2 部分会复用同一套 fixture generator，将模拟日志直接批量写入 ClickHouse 的 `log_analysis.logs_structured`，并生成：

```text
.tox/ueba_baseline_acceptance/load_result.json
```

第 3 部分会调用正式入口 `scripts/build_ueba_baseline.py`，从 ClickHouse `logs_structured` 读取第 2 项写入的 fixture 数据，生成 `user_behavior_baselines`，并保存 CLI 输出摘要：

```text
.tox/ueba_baseline_acceptance/build_result.json
```

`expected_baselines.json` 只是理论准线描述，用于后续人工/程序对账；UEBA baseline 模块后续必须从数据库 `logs_structured` 分析数据，不读取 expected JSON 作为输入。

默认不会保存 30000+ 条原始模拟日志 JSONL；只有显式传入 `--dump-logs-jsonl` 才会写出 `fixture_logs.jsonl`。

## 运行菜单

```bash
.venv/bin/python -m tests.behavior.ueba_baseline_acceptance.runner
```

菜单项：

```text
1. 生成 expected_baselines.json 和 fixture_summary.json
2. 生成模拟数据并写入 ClickHouse
3. 执行 UEBA baseline 构建
4. 对比 expected_baselines.json 与数据库实际 baseline（后续阶段）
5. 查看最近一次验收摘要
0. 退出
```

## ClickHouse 写入说明

第 2 项执行时会：

1. 重新生成 `expected_baselines.json` 和 `fixture_summary.json`；
2. 连接 ClickHouse 并执行 `SELECT 1` 探测；
3. 默认清理旧 fixture 数据；
4. 将模拟日志按显式列名批量写入 `logs_structured`；
5. 查询本次 fixture 行数；
6. 写入 `load_result.json` 并更新 `run_state.json`。

清理范围限定为：

```sql
username LIKE 'fixture_user_%'
AND log_type = 'vpn'
AND timestamp >= '2026-05-01 00:00:00'
AND timestamp < '2026-06-01 00:00:00'
```

不会清空整表，也不会删除非 fixture 用户数据。ClickHouse `ALTER TABLE ... DELETE` 是 mutation，可能异步完成；如果重复运行后看到行数短暂异常，可以等待 mutation 完成后重新执行入库。

## 检查 load_result.json

```bash
cat .tox/ueba_baseline_acceptance/load_result.json
```

成功时关键字段应类似：

```text
success = true
expected_rows = 30065
inserted_rows = 30065
database_rows = 30065
error = null
```

## UEBA baseline 构建说明

第 3 项执行前必须先完成第 2 项入库，并保证 `load_result.json` 中 `success = true`、`database_rows = expected_rows` 且 `expected_rows > 0`。

第 3 项只通过 subprocess 调用：

```bash
.venv/bin/python scripts/build_ueba_baseline.py
```

不会直接 import `UebaService`、`UebaRepository` 或 `BaselineStore`，也不会在验收工具中重写 baseline 构建逻辑。

执行结果会写入：

```text
.tox/ueba_baseline_acceptance/build_result.json
```

成功时关键字段包括：

```text
success = true
total_log_count = 30065
total_user_count = 24
reliable_user_count = 22
unreliable_user_count = 2
model_version = ueba_baseline_fixture_v1
```

baseline 结果写入 ClickHouse 的 `log_analysis.user_behavior_baselines`。

如果同一时间窗口中存在非 fixture 历史数据，`build_result.json` 中的 `total_log_count` / `total_user_count` 可能包含这些额外数据；这不代表第 3 项失败。

如果构建失败，先查看 `build_result.json` 中的 `error`、`stdout`、`stderr`。

本阶段不做 `expected_baselines.json` 与数据库实际 baseline 的对比；第 4 项将在后续阶段实现。第 4 阶段验证器应按 `fixture_user_%` 和最新记录口径做严格验证。

## SQL 检查入库数量

```bash
docker exec -i clickhouse-server clickhouse-client --query "
SELECT
    count() AS cnt,
    uniqExact(username) AS users,
    min(timestamp) AS min_time,
    max(timestamp) AS max_time
FROM log_analysis.logs_structured
WHERE username LIKE 'fixture_user_%'
  AND timestamp >= '2026-05-01 00:00:00'
  AND timestamp < '2026-06-01 00:00:00'
"
```

期望：

```text
cnt = 30065
users = 24
```
