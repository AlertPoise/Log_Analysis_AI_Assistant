# UEBA Shell 测试脚本使用手册

## 1. 使用前提

先按 `docs/ClickhouseManual.md` 启动并检查 ClickHouse。

```bash
docker compose -f tests/collectors/docker-compose-full.yml up -d
docker ps | grep clickhouse
curl http://localhost:8123/ping
docker logs clickhouse-server --tail 20
```

`curl http://localhost:8123/ping` 应返回：

```text
Ok.
```

确认 Python 虚拟环境可用：

```bash
.venv/bin/python --version
```

如使用非默认 ClickHouse 连接，先设置环境变量：

```bash
export CLICKHOUSE_HOST=localhost
export CLICKHOUSE_PORT=8123
export CLICKHOUSE_USERNAME=default
export CLICKHOUSE_USER=default
export CLICKHOUSE_PASSWORD=
export CLICKHOUSE_DATABASE=log_analysis
```

## 2. 启动测试脚本

在项目根目录执行：

```bash
bash tests/behavior/ueba_baseline_acceptance/run_ueba_demo.sh
```

进入菜单后，先执行：

```text
1. 环境检查
```

如果 ClickHouse 检查失败，回到 `docs/ClickhouseManual.md`，重新执行启动、端口检查和日志检查命令。

## 3. 主菜单速查

| 菜单 | 用途 | 是否写库 | 确认词 |
|---:|---|---:|---|
| 1 | 环境检查 | 否 | 无 |
| 2 | 基础准线分步流程 | 部分写库 | 写库步骤需 `YES` |
| 3 | 训练表更新流程 | 是 | `YES` |
| 4 | 持续流量与 Validation | 部分写库 | `YES` / `DRYRUN` / `DELETE` |
| 5 | 查看整体状态 | 否 | 无 |
| 6 | 一键基础准线完整流程 | 是 | `YES` |
| 7 | 一键持续流量联动验收 | 是 | `YES`，cleanup 需 `DELETE` |
| 8 | 退出 | 否 | 无 |
| 9 | 全流程闭环 | 是 | 各阶段按提示输入 |

确认词说明：

| 输入 | 含义 |
|---|---|
| `YES` | 允许执行写库或验收流程 |
| `DRYRUN` | 只执行评分预览，不写入评分结果 |
| `DELETE` | 允许清理本轮持续流量验收数据 |
| 其他输入或 EOF | 取消当前操作 |

`YES` 和 `DELETE` 大小写不敏感。

## 4. 推荐演示路径

### 4.1 只读预检

按顺序执行：

```text
1 → 5
```

用途：

- 检查 ClickHouse 连接。
- 检查基础表是否存在。
- 查看当前状态目录和最近结果。
- 不写入 ClickHouse。

### 4.2 基础准线演示

执行：

```text
6
```

按提示输入：

```text
YES
```

该流程依次执行：

```text
生成理论准线文件
写入 fixture 日志到 logs_structured
构建 baseline 并写入 user_behavior_baselines
对比理论准线与实际 baseline
```

完成后执行：

```text
5
```

查看 `build_result` 和 `validation_report`。

### 4.3 训练表更新演示

执行：

```text
3
```

按提示输入：

```text
YES
```

该流程验证：

```text
5 月训练样本初始化
5 月 baseline 构建
6 月训练样本替换
训练样本替换后 baseline 不自动变化
6 月 baseline 重建
重建前后 baseline 差异
```

完成后执行：

```text
5
```

查看 `manual_training_update_report`。

### 4.4 持续流量与评分演示

推荐直接执行一键流程：

```text
7
```

按提示输入：

```text
YES
```

如允许成功后自动清理本轮数据，再输入：

```text
DELETE
```

如需要保留数据用于复查，不输入 `DELETE`。

完成后执行：

```text
5
```

查看 `continuous_validation_acceptance_report` 和 `last_validation_result`。

### 4.5 完整闭环演示

执行：

```text
9
```

脚本按顺序执行：

```text
菜单 3：训练表更新流程
菜单 6：基础准线完整流程
菜单 7：持续流量联动验收
```

该路径会连续写入 ClickHouse。执行前必须确认：

- ClickHouse 环境可写。
- 当前数据库允许本轮演示写入。
- 是否需要在持续流量验收成功后 cleanup。

## 5. 菜单 2：基础准线分步流程

进入：

```text
2
```

子菜单说明：

| 子菜单 | 操作 | 是否写库 | 结果 |
|---:|---|---:|---|
| 1 | 生成理论准线文件 | 否 | expected baselines |
| 2 | 写入 fixture 日志 | 是 | `logs_structured` 增加 fixture 数据 |
| 3 | 构建 baseline | 是 | `user_behavior_baselines` 增加 baseline |
| 4 | 对比理论准线与实际 baseline | 否 | validation report |

推荐分步顺序：

```text
2 → 1 → 2(YES) → 3(YES) → 4
```

如果只是对外演示，优先使用菜单 6，一次完成上述流程。

## 6. 菜单 4：持续流量与 Validation

进入：

```text
4
```

常用子菜单：

| 子菜单 | 操作 | 说明 |
|---:|---|---|
| 1 | 启动持续流量 Server | 开始生成持续登录/VPN 流量 |
| 2 | 停止持续流量 Server | 停止当前 Server |
| 5 | 调速到 20 条/秒 | 设置流量速率 |
| 6 | 调速到 50 条/秒 | 设置流量速率 |
| 8 | 切换流量模式 | 选择异常场景 |
| 9 | 对当前窗口执行 Validation | 输入 `YES` 写库，输入 `DRYRUN` 不写库 |
| 10 | 查看状态、日志与结果 | 只读查看 |
| 11 | 清理本轮数据 | 输入 `DELETE` 后执行 cleanup |
| 12 | 一键持续流量联动验收 | 推荐演示入口 |

流量模式：

| 模式 | 场景 |
|---|---|
| `normal` | 正常行为 |
| `new_ip` | 新来源 IP |
| `new_country` | 新来源国家 |
| `new_city` | 新来源城市 |
| `failed_login` | 失败登录 |
| `off_hours` | 非工作时间访问 |
| `combo_anomaly` | 多异常叠加 |
| `mixed` | 混合场景 |

手动持续流量演示顺序：

```text
4 → 1 → 8(选择模式) → 5或6(设置速率) → 2 → 9(DRYRUN或YES) → 10
```

如果需要清理本轮持续流量数据：

```text
4 → 11 → DELETE
```

## 7. 输出位置

测试脚本产物保存在项目内：

```text
.tox/manual/ueba_demo_menu/
```

常看文件：

| 文件 | 用途 |
|---|---|
| `baseline_acceptance/build_result.json` | baseline 构建结果 |
| `baseline_acceptance/validation_report.json` | 理论准线与实际 baseline 对比结果 |
| `training_update/manual_training_update_report.json` | 训练表更新闭环结果 |
| `continuous_validation_acceptance_report.json` | 持续流量联动验收结果 |
| `last_validation_result.json` | 最近一次 Validation CLI 输出 |
| `demo_summary.log` | Shell 菜单执行摘要 |

也可以直接用菜单查看：

```text
5. 查看整体状态
```

## 8. 结果检查

### 8.1 baseline 构建结果

查看 `build_result.json`，重点确认：

| 字段 | 期望 |
|---|---|
| `success` | `true` |
| `total_user_count` | 大于 0 |
| `reliable_user_count` | 大于 0 |
| `total_log_count` | 大于 0 |
| `model_version` | 与本轮演示一致 |

### 8.2 训练表更新结果

查看 `manual_training_update_report.json`，重点确认：

| 检查项 | 期望 |
|---|---|
| 5 月训练样本 | 已初始化 |
| 6 月训练样本 | 已替换 |
| baseline 不变校验 | 通过 |
| baseline 重建差异校验 | 通过 |

### 8.3 Validation 评分结果

查看 `last_validation_result.json` 或 `continuous_validation_acceptance_report.json`，重点确认：

| 字段 | 期望 |
|---|---|
| `success` | `true` |
| `selected_count` | 大于 0 |
| `scored_count` | 大于 0 |
| `risk_level_counts` | 存在风险等级分布 |
| `validation_status_counts` | 存在评分状态分布 |
| `sample_results` | 包含样例评分结果 |

单条评分重点看：

```text
ueba_score
ueba_risk_level
ueba_anomaly_reasons
validation_status
validation_run_id
source_identity
```

## 9. 常见问题处理

### 9.1 ClickHouse 不可达

按 `docs/ClickhouseManual.md` 重新检查：

```bash
docker compose -f tests/collectors/docker-compose-full.yml up -d
docker ps | grep clickhouse
curl http://localhost:8123/ping
docker logs clickhouse-server --tail 20
```

确认 `/ping` 返回 `Ok.` 后，重新执行菜单 1。

### 9.2 8765 端口被占用

先执行：

```text
1. 环境检查
```

如果脚本提示端口被其他进程占用，不要强行停止未知进程。更换环境或先处理占用进程后再启动持续流量 Server。

### 9.3 Validation 没有结果

检查：

```text
菜单 5：当前窗口状态
菜单 4 → 10：持续流量状态、日志与结果
```

常见原因：

- 当前窗口没有日志。
- 未先生成 baseline。
- `model_version` 与 baseline 不一致。
- 评分时选择了 `DRYRUN`，因此 `written_count` 为 0。

### 9.4 需要清理持续流量数据

执行：

```text
4 → 11 → DELETE
```

脚本只清理当前 CLOSED 窗口内、本菜单生成的持续流量日志和 validation 结果。

## 10. 最短演示路线

只读检查：

```text
1 → 5
```

基础准线演示：

```text
1 → 6(YES) → 5
```

持续流量评分演示：

```text
1 → 7(YES, DELETE 可选) → 5
```

完整闭环演示：

```text
1 → 9 → 按提示输入 YES / DELETE → 5
```
