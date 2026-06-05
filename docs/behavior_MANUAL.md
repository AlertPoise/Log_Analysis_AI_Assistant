# Behavior / UEBA 一键演示操作手册

## 1. 功能说明

本脚本用于演示 UEBA 完整流程：

```text
环境检查
→ 更新训练数据并构建 baseline
→ 生成持续流量并执行 Validation
→ 校验结果
→ 自动清理本轮演示数据
```

脚本会向本机 ClickHouse 写入演示数据，并在流程结束时自动清理。

---

## 2. 运行前准备

进入项目目录并激活虚拟环境：

```bash
cd /home/admin1/work/aaaprojects/Log_Analysis_AI_Assistant-new
source .venv/bin/activate
export PYTHONPATH="$PWD"
```

确认 ClickHouse 已启动：

```bash
curl --max-time 5 http://localhost:8123/ping
```

正常输出：

```text
Ok.
```

---

## 3. 启动脚本

执行：

```bash
bash tests/behavior/ueba_baseline_acceptance/run_ueba_demo.sh
```

主菜单：

```text
1. 环境检查
2. 一键执行 UEBA 完整演示
3. 查看最近一次运行结果
4. 退出
```

首次使用建议先输入：

```text
1
```

环境检查通过后，输入：

```text
2
```

脚本将自动完成全部流程，不需要再输入 `YES` 或 `DELETE`。

---

## 4. 查看运行结果

完整演示包含 5 个阶段：

```text
[1/5] 检查运行环境
[2/5] 更新训练数据并构建 baseline
[3/5] 生成持续流量并执行 Validation
[4/5] 校验运行结果
[5/5] 清理本轮演示数据
```

正常结束时应看到：

```text
CONTINUOUS VALIDATION ACCEPTANCE PASSED
清理完成。
```

脚本返回主菜单后，可以输入：

```text
3
```

查看最近一次运行结果。

退出脚本：

```text
4
```

---

## 5. 关于 timeout

运行时出现：

```text
timeout=600s
```

表示该子流程最长允许运行 600 秒。

正常情况下脚本会提前完成，不会等待满 600 秒。只有子流程异常卡住时，才会触发超时保护。

---

## 6. 可选：检查清理结果

演示结束后，可以执行：

```bash
docker exec clickhouse-server clickhouse-client --query "
SELECT 'logs_structured' AS table_name, count() AS residue
FROM log_analysis.logs_structured
WHERE username LIKE 'fixture_user_%'

UNION ALL

SELECT 'user_behavior_baselines', count()
FROM log_analysis.user_behavior_baselines
WHERE username LIKE 'fixture_user_%'

UNION ALL

SELECT 'ueba_baseline_training_logs', count()
FROM log_analysis.ueba_baseline_training_logs
WHERE dataset_id = 'ueba_training_monthly_acceptance'

UNION ALL

SELECT 'ueba_validation_results', count()
FROM log_analysis.ueba_validation_results
WHERE username LIKE 'fixture_user_%'
"
```

正常情况下，四项残留数量均为：

```text
0
```

检查是否存在未完成的 ClickHouse mutation：

```bash
docker exec clickhouse-server clickhouse-client --query "
SELECT count()
FROM system.mutations
WHERE is_done = 0
"
```

正常输出：

```text
0
```

检查端口和残留进程：

```bash
ss -lntp | grep ':8765' || echo "PORT_8765_FREE"

pgrep -af \
  'run_ueba_demo.sh|continuous_login_http_server|tests.behavior.ueba_baseline_acceptance' \
  || echo "NO_MATCHING_PROCESS"
```

正常输出：

```text
PORT_8765_FREE
NO_MATCHING_PROCESS
```

---

## 7. 常见问题

### ClickHouse 无法连接

先确认容器状态：

```bash
docker ps
```

再检查：

```bash
curl --max-time 5 http://localhost:8123/ping
```

### 脚本被中断

重新运行脚本并再次选择：

```text
2
```

脚本会执行受控清理并重新完成演示。

### 不要执行的操作

不要手动执行宽泛数据库删除，例如：

```sql
DELETE WHERE username LIKE 'fixture_user_%'
```

正常使用时，直接通过菜单 `2` 运行完整流程即可。
