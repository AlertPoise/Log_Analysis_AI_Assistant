# UEBA Baseline Acceptance Tool

本目录提供 `behavior / UEBA` baseline 人工验收工具。

## 当前能力

第 1 部分生成理论准线文件：

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

第 4 部分会读取 `expected_baselines.json`，并从 ClickHouse `user_behavior_baselines` 读取实际 baseline，只验证 `fixture_user_%` 用户，生成：

```text
.tox/ueba_baseline_acceptance/actual_baselines.json
.tox/ueba_baseline_acceptance/validation_report.json
.tox/ueba_baseline_acceptance/failed_diff.json
```

`expected_baselines.json` 只是理论准线描述，用于后续人工/程序对账；UEBA baseline 模块仍然必须从数据库 `logs_structured` 分析数据，不读取 expected JSON 作为输入。

默认不会保存 30000+ 条原始模拟日志 JSONL；只有显式传入 `--dump-logs-jsonl` 才会写出 `fixture_logs.jsonl`。

## Fixture v2 数据覆盖

默认 fixture 已增强为：

```text
fixture_id = ueba_fixture_v2_monthly_seed_42
model_version = ueba_baseline_fixture_v2_monthly
default users = 26
default logs = 66130
```

模拟数据不再几乎全是“中国 / 北京”：stable、高失败、非工作时间、多地登录和 IP 长尾用户覆盖北京、上海、广州、深圳、杭州、成都，并包含新加坡、日本、德国等跨国家场景。

行为维度不再是单值：`action` 覆盖 `LOGIN`、`REAUTH`、`LOGOUT`、`VPN_CONNECT`；`auth_method` 覆盖 `password+mfa`、`sso+mfa`、`certificate`、`password_only`；`client_software` 覆盖 `OpenVPN Connect`、`Cisco AnyConnect`、`Windows VPN Client`、`Tunnelblick`；`protocol` 覆盖 `SSLVPN`、`IPSec`、`WireGuard`。

失败日志保留 `FAILED` 和 `FAIL` 两种结果，失败原因覆盖 `PASSWORD_ERROR`、`MFA_DENIED`、`ACCOUNT_LOCKED`、`TIMEOUT`；成功日志的 `fail_reason` 仍为空字符串。

风险和长尾场景包括：

```text
multi_location / high_failure / offhour / iptail 用户包含非 0 unusual_ip_rate
fixture_user_iptail_0001: 5 个高频来源 IP + 100 个低频来源 IP
fixture_user_iptail_0002: 300 个来源 IP 均匀分布，通常没有 IP 达到 common 阈值
stable 用户: 2~3 个稳定 destination_ip 为主，少量其他目标资源
iptail 用户: destination_ip 长尾分散
VPN gateway: vpn-gw-cn-01 / vpn-gw-cn-02 / vpn-gw-hk-01 / vpn-gw-sg-01
```

时间字段继续输出普通字符串：

```text
YYYY-MM-DD HH:MM:SS
```

不引入 UTC、`Z`、`+08:00`、timezone-aware datetime 或 ISO 8601 字符串格式。

## 运行菜单

```bash
.venv/bin/python -m tests.behavior.ueba_baseline_acceptance.runner
```

菜单项：

```text
1. 生成 expected_baselines.json 和 fixture_summary.json
2. 生成模拟数据并写入 ClickHouse
3. 执行 UEBA baseline 构建
4. 对比 expected_baselines.json 与数据库实际 baseline
5. 查看最近一次验收摘要
0. 退出
```

## 月度训练表更新闭环

非交互执行 5月初始化训练表、5月 baseline 构建、6月训练表替换、baseline 不变校验、6月 baseline 重建和差异验证：

```bash
.venv/bin/python -m tests.behavior.ueba_baseline_acceptance.monthly_training_update_runner
```

默认只保留 monthly runner 自己的三个核心产物：

```text
.tox/ueba_baseline_acceptance/monthly_training_update_report.json
.tox/ueba_baseline_acceptance/monthly_training_update_state.json
.tox/ueba_baseline_acceptance/baseline_change_diff.json
```

每次启动都会清理 monthly runner 自己负责的旧产物，包括历史 debug 中间文件；不会删除 `expected_baselines.json`、`fixture_summary.json`、`load_result.json`、`run_state.json`、`validation_report.json`、`failed_diff.json` 等其他验收模块产物。

`monthly_training_update_state.json` 会合并保存训练表更新结果、baseline 构建结果、fixture 统计、训练表统计、baseline fingerprint 摘要和 diff 摘要，用于替代默认模式下拆散的中间 JSON。

需要排查细节时显式开启 debug 产物：

```bash
.venv/bin/python -m tests.behavior.ueba_baseline_acceptance.monthly_training_update_runner --debug-artifacts
```

debug 模式额外输出：

```text
.tox/ueba_baseline_acceptance/may_training_update_result.json
.tox/ueba_baseline_acceptance/may_baseline_build_result.json
.tox/ueba_baseline_acceptance/june_training_update_result.json
.tox/ueba_baseline_acceptance/june_baseline_build_result.json
.tox/ueba_baseline_acceptance/baseline_before_june_update.json
.tox/ueba_baseline_acceptance/baseline_after_june_training_update.json
.tox/ueba_baseline_acceptance/baseline_after_june_rebuild.json
```

## 训练表手动更新交互式验收

交互式执行训练表手动更新演示：

```bash
.venv/bin/python -m tests.behavior.ueba_baseline_acceptance.manual_training_update_runner
```

菜单项：

```text
1. 清空测试样本数据
2. 生成 5 月测试数据
3. 用 5 月数据初始化训练表
4. 用训练表构建 5 月 baseline
5. 生成 6 月测试数据
6. 用 6 月数据更新训练表
7. 检查更新训练表后 baseline 是否不变
8. 用训练表构建 6 月 baseline
9. 比较 5 月和 6 月 baseline 是否变化
10. 查看当前状态
11. 一键执行完整演示流程
0. 退出
```

5 月 / 6 月只是验收测试用的两段固定窗口，用于演示“初始化训练表 -> 手动更新训练表 -> 确认后重建 baseline”的机制。正式使用时不要求每个月都生成数据，也不要求每个月都更新 baseline；生产中应由人工或策略选择一段可信训练数据，写入 `ueba_baseline_training_logs`，确认后再手动构建 baseline。

本工具只操作测试范围：

```text
logs_structured:
username LIKE 'fixture_user_%'
log_type = 'vpn'
timestamp >= '2026-05-01 00:00:00'
timestamp < '2026-07-01 00:00:00'

ueba_baseline_training_logs:
dataset_id = 'ueba_training_monthly_acceptance'

user_behavior_baselines:
username LIKE 'fixture_user_%'
model_version IN (
  'ueba_monthly_acceptance_may_init',
  'ueba_monthly_acceptance_june_updated'
)
```

默认只保留三个核心产物：

```text
.tox/ueba_baseline_acceptance/manual_training_update_report.json
.tox/ueba_baseline_acceptance/manual_training_update_state.json
.tox/ueba_baseline_acceptance/baseline_change_diff.json
```

如需排查每一步中间结果，可显式开启 debug 产物：

```bash
.venv/bin/python -m tests.behavior.ueba_baseline_acceptance.manual_training_update_runner --debug-artifacts
```

也可以非交互执行完整演示流程：

```bash
.venv/bin/python -m tests.behavior.ueba_baseline_acceptance.manual_training_update_runner --run-all
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
AND timestamp < '2026-07-01 00:00:00'
```

不会清空整表，也不会删除非 fixture 用户数据。ClickHouse `ALTER TABLE ... DELETE` 是 mutation，可能异步完成；如果重复运行后看到行数短暂异常，可以等待 mutation 完成后重新执行入库。

## 检查 load_result.json

```bash
cat .tox/ueba_baseline_acceptance/load_result.json
```

成功时关键字段应类似：

```text
success = true
expected_rows = 66130
inserted_rows = 66130
database_rows = 66130
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
total_log_count = 66130
total_user_count = 26
model_version = ueba_baseline_fixture_v2_monthly
```

baseline 结果写入 ClickHouse 的 `log_analysis.user_behavior_baselines`。

如果同一时间窗口中存在非 fixture 历史数据，`build_result.json` 中的 `total_log_count` / `total_user_count` 可能包含这些额外数据；这不代表第 3 项失败。

如果构建失败，先查看 `build_result.json` 中的 `error`、`stdout`、`stderr`。

第 3 项不做 `expected_baselines.json` 与数据库实际 baseline 的对比；第 4 项负责对比验证。

## UEBA baseline 对比验证说明

第 4 项用于对比 `expected_baselines.json` 与数据库实际 baseline。验证器只读取：

```text
model_version = ueba_baseline_fixture_v2_monthly
username LIKE 'fixture_user_%'
```

为避免 `ReplacingMergeTree` 后台合并前存在多版本记录，验证器从 `user_behavior_baselines FINAL` 读取实际结果，查询口径为 `model_version + fixture_user_% + FINAL`。

验证器不会使用 `baseline_start_time` / `baseline_end_time` 做精确过滤；ClickHouse `DateTime` 显示可能受服务端或客户端时区影响，精确匹配这些展示值可能误过滤真实 fixture baseline。`build_result.json` 中的 `total_log_count` / `total_user_count` 可能包含同窗口非 fixture 数据，不作为严格验证依据。

验证器会对比：`sample_count`、`is_reliable`、`failed_rate`、`off_hours_rate`、`unusual_ip_rate`、`common_active_hours`、`common_source_ips`、`common_destination_ips`、`common_source_countries`、`common_source_cities`、`common_vpn_gateways`、`result_distribution`、`event_type_distribution`、`action_distribution`、`fail_reason_distribution`、`auth_method_distribution`、`client_software_distribution`、`protocol_distribution`、`active_day_avg_events`、`max_daily_events`。

`common_*` 字段只验证 expected 中达到 `validation_common_min_ratio` 的高频主要值必须出现在 actual common 结果中，不要求长尾低频值全部进入 common。对于 `fixture_user_iptail_0002` 这类没有任何来源 IP 达到阈值的用户，`common_source_ips` 允许为空。

输出文件含义：

```text
actual_baselines.json      数据库实际 baseline 摘要
validation_report.json     对比汇总报告
failed_diff.json           失败项明细；成功时为 []
```

验证失败时优先查看 `failed_diff.json`。常见排查方向：

1. `sample_count` 不一致：检查入库数量、时间窗口、`log_type`。
2. `failed_rate` 不一致：检查 `FAILED` / `FAIL` / `LOGIN_FAIL` 统计口径。
3. `is_reliable` 不一致：检查 `min_sample_count`。
4. `common_*` 不一致：检查 Top-N、min_ratio 或 expected 高频值。
5. distribution 不一致：检查 action、fail_reason、auth_method、client_software、protocol 等聚合口径。
6. 重复记录：检查查询是否使用 `FINAL` 或最新 `created_at` 口径。

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
  AND timestamp < '2026-07-01 00:00:00'
"
```

期望：

```text
cnt = 66130
users = 26
```
