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

## Shell 全流程演示入口

统一 Shell 演示入口：

```bash
tests/behavior/ueba_baseline_acceptance/run_ueba_demo.sh
```

该脚本只用于测试与演示编排，不重写 baseline、Validation、持续流量生成器或 ClickHouse 写入逻辑。基础准线流程复用 `tests.behavior.ueba_baseline_acceptance.runner`，训练表更新复用 `tests.behavior.ueba_baseline_acceptance.manual_training_update_runner`，持续流量联动验收复用 `tests.behavior.ueba_baseline_acceptance.run_continuous_validation_acceptance`，手动评分调用正式 CLI `scripts/run_ueba_validation.py`。

Shell 根菜单：

```text
UEBA 全流程演示工具

1. 环境检查
2. 基础准线流程
3. 训练表更新流程（暂未开放）
4. 持续流量与 Validation
5. 查看整体状态
6. 一键执行基础准线完整流程（已禁用）
7. 一键执行持续流量联动验收（暂未开放）
8. 退出
```

新增 Shell 工具自己的状态、日志、PID 与报告统一写入：

```text
.tox/manual/ueba_demo_menu/
```

其中至少包括：

```text
baseline_acceptance/                           基础准线 runner 自定义输出目录
training_update/                              训练表更新 runner 自定义输出目录
continuous_login_http_server.pid              持续流量 HTTP Server PID
continuous_login_http_server.log              持续流量 HTTP Server 日志
current_window_start                          当前持续流量窗口起点
current_window_end                            当前持续流量窗口终点
current_window_state                          当前持续流量窗口状态（IDLE / ACTIVE / CLOSED）
last_validation_result.json                   手动执行正式 Validation CLI 的最近一次结果
validation_history.tsv                        本轮 menu_% run_id 历史
continuous_validation_acceptance_report.json  一键持续流量联动验收报告
```

时间字段统一使用无标注字符串：

```text
YYYY-MM-DD HH:MM:SS
```

禁止使用 `Z`、`+00:00` 和 timezone-aware datetime。

持续流量子菜单支持：

```text
1. 启动持续流量 HTTP Server
2. 停止持续流量 HTTP Server
3. 暂停持续流量
4. 恢复持续流量
5. 调速到 20/秒
6. 调速到 50/秒
7. 自定义调速
8. 切换持续流量模式
9. 使用正式 Validation CLI 评分当前持续流量
10. 查看持续流量状态、日志与结果
11. 精确清理本轮持续流量验收数据
12. 一键持续流量联动验收（暂未开放）
13. 返回上一级
```

持续流量窗口规则：

```text
1. Server 默认以 0 条/秒启动，并在 ready 后立即暂停。
2. 恢复流量前先记录 window_start，再 POST /start。
3. 暂停后先确认 paused=true，等待固定缓冲，再记录 window_end。
4. 正式评分只接受 CLOSED 窗口。
```

真实 Validation 写库前会对同一个 ClickHouse 实例执行窗口隔离检查：

```text
total_count   = 指定窗口内全部 vpn 日志
fixture_count = 指定窗口内 username = fixture_user_stable_0001 且 raw_log 带 ueba_continuous_fixture marker 的日志
```

只有 `fixture_count > 0` 且 `total_count == fixture_count` 时才允许继续；窗口被污染时会拒绝写库。`DRYRUN` 只用于查看，不会附带 `--write`。

精确 cleanup 只删除当前 CLOSED 窗口内的精确目标数据：

```text
logs_structured:
username = fixture_user_stable_0001
position(raw_log, 'ueba_continuous_fixture') > 0

ueba_validation_results:
username = fixture_user_stable_0001
startsWith(validation_run_id, 'menu_')
baseline_model_version = ueba_baseline_fixture_v2_monthly
log_type = vpn
```

不会删除 baseline、训练表或其他非菜单数据。

ClickHouse 连接参数可通过环境变量覆盖。脚本统一读取并显式透传：

```text
CLICKHOUSE_HOST
CLICKHOUSE_PORT
CLICKHOUSE_USER / CLICKHOUSE_USERNAME
CLICKHOUSE_PASSWORD
CLICKHOUSE_DATABASE
```

EOF 安全规则：主菜单或子菜单遇到 EOF 会安全退出或返回上一级；不会自动停止 Server，不会自动清理数据。

## 安全门禁规则

### 查询失败与非数字响应

所有 ClickHouse 标量查询（`clickhouse_scalar`）使用 `curl --fail` 确保 HTTP 非 2xx 返回非零退出码。查询失败或返回非数字文本时，隔离检查和 cleanup 会硬拒绝继续：

- `total_count` / `fixture_count` 非数字 → 拒绝 Validation CLI 调用
- cleanup 前/后计数查询失败或非数字 → 拒绝执行 DELETE
- DELETE 执行失败 → cleanup 中止，不删除窗口状态文件
- 只有两类数据（validation_results、logs_structured）均确认清理成功（计数归零），才会删除窗口状态文件

### 空窗口与污染窗口

Validation 写库前窗口隔离检查强制：
- `fixture_count > 0`（空窗口拒绝）
- `total_count == fixture_count`（污染窗口拒绝）
- 两种失败使用不同的错误信息

### 一键流程 YES 确认

所有真实写库操作执行前必须输入大写 `YES`（仅接受大写）：

**单项写库**：
- 基础准线子菜单 2：生成模拟数据并写入 ClickHouse logs_structured
- 基础准线子菜单 3：执行正式 baseline 构建并写入 user_behavior_baselines

**手动 Validation 评分**：
- 输入 `YES` 附加 `--write` 执行正式写库
- 输入 `DRYRUN` 仅查看不写库

**根菜单 6 (已禁用)**：基础准线一键流程不再可用 — 交互 Runner pipe 注入已废弃，共享表现场未恢复。请使用基础准线子菜单中的分步入口。

输入不是 `YES` 或遇到 EOF → 取消操作，不写库，不 cleanup。

### 启动失败回滚

Server 启动过程中任一步骤失败（ready 超时、立即暂停失败、状态读取失败、paused 未确认），会自动回滚刚启动的进程：
- 向本次启动的 PID 发送 SIGINT
- 有限次数等待退出（最多 20 次 × 0.25s）
- 进程退出后删除 PID 文件
- 退出失败时保留 PID 文件供人工排查，不升级为强杀

### PID 安全

所有 `kill` 操作前强制校验 PID 为正整数（`^[1-9][0-9]*$`）：
- PID 文件为空、非数字、0、负数时拒绝 kill
- 历史 PID 文件无法验证归属时只报警，不自动 kill
- 端口被未知进程占用时只报警，不 kill
- 仅当前菜单会话内启动的 PID 可以安全管理

### HTTP 错误码

所有 HTTP 调用使用 `curl --fail` 语义：
- HTTP 非 2xx 返回非零退出码
- `/start`、`/stop`、`/rate`、`/mode` 失败时拒绝记录成功
- 所有 HTTP 调用有有限 timeout（3-5 秒）

### PID 归属

PID 文件（`.tox/manual/ueba_demo_menu/continuous_login_http_server.pid`）只用于状态记录和排查，**不是自动 kill 的授权依据**。

仅当前菜单会话本次通过 `$!` 启动的 Server 才允许自动停止：
- 会话 PID 变量 `CURRENT_SESSION_SERVER_PID` 仅在本次菜单进程内有效
- `jobs -pr` 确认 PID 仍属于当前 Shell 会话的后台 job
- 历史 PID 文件无法验证归属 → 只报警，不 kill
- 端口被未知进程占用 → 只报警，拒绝启动/停止，不 kill
- 脚本重启后即失去对历史 PID 的自动管理权

### ClickHouse 环境参数

Shell 顶层定义的 `CLICKHOUSE_HOST`、`CLICKHOUSE_PORT`、`CLICKHOUSE_USERNAME`、`CLICKHOUSE_PASSWORD`、`CLICKHOUSE_DATABASE` 通过 `export` 统一透传给所有子进程：
- 同时设置 `CLICKHOUSE_USER`（兼容部分脚本使用不同变量名）
- 手动 Validation CLI 继续显式传入所有 5 个参数
- 密码不出现在日志、summary 或 debug 输出中

Python 侧 `AcceptanceConfig` 通过 `default_factory` 读取环境变量：
- `CLICKHOUSE_USERNAME` 优先级高于 `CLICKHOUSE_USER`
- `CLICKHOUSE_PORT` 必须为 1..65535 的正整数
- 基础准线、训练表、持续流量 Server 和一键联动 Runner 使用同一组配置

### 状态 JSON 严格解析

Server `paused` 状态解析严格区分：
- JSON 解析失败 → 错误，不伪装成 `paused=false`
- 缺少 `paused` 字段 → 错误
- `paused` 非布尔值 → 错误
- 只有明确的 `true` 或 `false` 才被接受

### 暂未开放的功能

以下功能因下游工具链待独立加固或安全前提未满足而暂时禁用：

- **训练表更新流程**（根菜单 3）：下游 Runner 缺少 timeout，长时间挂起风险
- **一键执行基础准线完整流程**（根菜单 6）：交互 Runner pipe 注入已废弃，共享表现场未恢复。请使用基础准线子菜单中的分步入口
- **一键持续流量联动验收**（根菜单 7 / 持续流量子菜单 12）：Python cleanup helper 精确用户名语义待加固

手动持续流量流程仍然开放：
- 启动 / 停止 / 暂停 / 恢复 Server
- 调速 / 切换模式
- CLOSED 窗口 Validation 评分（含 YES 确认 + 窗口隔离）
- 精确 cleanup（含 DELETE 确认）

### 临时文件

所有测试临时文件均保存在项目内部 `.tox/manual/ueba_demo_shell_tests/`，不使用系统 `/tmp`。

### 停止 Server 窗口关闭

停止 Server 路径在关闭活动窗口前：
1. POST /stop
2. 读取 Server 状态
3. 确认 `paused=true`
4. 只有确认成功后才等待缓冲并记录 `end_time`

状态读取失败或 `paused != true` 时，窗口保持 ACTIVE 状态，需人工检查。

### 时间格式

时间字段统一使用无标注字符串：
`YYYY-MM-DD HH:MM:SS`
禁止使用 `Z`、`+00:00` 和 timezone-aware datetime。

### 写共享表声明

基础准线流程中的写库操作直接写入共享 ClickHouse 表，**不是隔离沙箱**：

- 菜单项 2（生成模拟数据）→ 写入 `logs_structured`
- 菜单项 3（baseline 构建）→ 写入 `user_behavior_baselines`

操作前菜单会显示明确提示。**不会自动 cleanup**。操作前请确认当前数据库现场。

### 验收范围

本轮验收仅执行**第一层只读冒烟验收**：
- 菜单启动、环境检查、查看整体状态
- 各禁用入口验证
- 非法输入、EOF、安全退出
- 8765 端口空闲、无残留进程
- 不写库、不自动 cleanup

**第二层受控业务验收未执行**。原因：数据库恢复安全前提未满足 —
logs_structured 存在额外 fixture 数据（+58 行，来源待核验）、
user_behavior_baselines 的 model_version 污染到 203 个 non-fixture 用户、
ueba_baseline_training_logs 混入 non-fixture 数据。

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
