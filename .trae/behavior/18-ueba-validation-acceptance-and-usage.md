# 18 - UEBA Validation 验收与使用说明

## 1. 阶段定位

本阶段（19-L）不是新增功能阶段，而是**文档固化阶段**。

本阶段只整理当前 UEBA validation 已完成能力、验收方式、使用方式、表结构事实、边界约束和后续接入建议，不新增、不修改任何代码。

本阶段明确不做：

- 不修复 dashboard（`src/visualization/dashboard.py` 中的 `from src.behavior.api import ...` 属于后续事项）
- 不调整 CLI 行为
- 不新增测试
- 不修改 ClickHouse schema（不修改 `config/clickhouse.sql`）
- 不改 `src/`、`scripts/`、`tests/` 任何文件
- 不修复已知挂起问题（S1–S10）

---

## 2. 当前 UEBA validation 链路概览

截至 19-J2，UEBA validation 已完成如下完整链路：

```text
logs_structured（待评分日志）
    ↓ UebaValidationRepository.fetch_target_logs()     ← 只读
list[ValidationTargetLog]
    ↓ BaselineStore.get_user_baseline()                ← 只读 user_behavior_baselines
    ↓ UebaScoreCalculator.calculate()                  ← 纯 Python 规则评分
list[UebaValidationResult]
    ↓ UebaValidationRepository.save_validation_results() ← 仅 --write 时执行
ueba_validation_results (MergeTree, append-only)
    ↓ UebaValidationRepository.query_validation_results() ← 只读
export_ueba_validation_results.py                      ← CSV / JSON 导出
```

当前链路不包含以下任何内容：

- 不读取 `ueba_baseline_training_logs`
- 不修改 `user_behavior_baselines`
- 不修改 `ueba_baseline_training_logs`
- 不写回 `logs_structured`
- 不接 Kafka / Flink
- 不做实时检测
- 不接 dashboard

---

## 3. 核心文件与脚本职责

### 源码文件（`src/behavior/`）

| 文件 | 职责 | 读库 | 写库 |
|---|---|---|---|
| `validation_schemas.py` | 定义 `ValidationTargetLog`、`ScoreReason`、`UebaValidationResult`、`ValidationRunResult` | 否 | 否 |
| `score_calculator.py` | 纯 Python 可解释规则评分引擎（0–100 分），含 `build_source_identity()` | 否 | 否 |
| `validation_repository.py` | 从 `logs_structured` 读取目标日志，读写 `ueba_validation_results` 表，datetime 序列化/反序列化 | **是** | **是** |
| `validation_service.py` | 编排：读取目标日志 → 查 baseline → 评分 → 可选保存，生成 JSON 摘要 | 否 | 否（编排层） |
| `baseline_store.py` | `user_behavior_baselines` DDL / 批量写入 / 查询（validation 链路只使用只读查询） | **是** | **是** |

### CLI 脚本（`scripts/`）

| 脚本 | 用途 | 读库 | 写库 | 默认行为 |
|---|---|---|---|---|
| `run_ueba_validation.py` | 运行 validation 评分并输出 JSON | 是 | 仅 `--write` | **dry-run（不写库）** |
| `export_ueba_validation_results.py` | 导出 `ueba_validation_results` 为 CSV/JSON | 是 | **否** | CSV 输出到 stdout |

### 配套测试（`tests/behavior/`）

| 测试文件 | 覆盖内容 |
|---|---|
| `test_ueba_validation_schemas.py` | Schema 字段、默认值、实例隔离 |
| `test_ueba_score_calculator.py` | 9 种评分场景、分值 clamp、source_identity、validation_id 确定性 |
| `test_ueba_validation_repository.py` | DDL 断言、序列化、fetch_target_logs、save/query、datetime 处理、SQL 安全约束 |
| `test_ueba_validation_service.py` | dry-run / write 编排、计数统计、异常处理、baseline 缺失场景 |
| `test_run_ueba_validation_cli.py` | CLI help 不连库、参数校验、--write/--dry-run 互斥、password redaction |
| `test_export_ueba_validation_results_cli.py` | CSV/JSON 输出、文件写入、源码不含写库关键字、password redaction |

---

## 4. 相关表职责说明

当前 UEBA 模块涉及三张核心 ClickHouse 表：

| 表名 | 引擎 | 职责 | validation 关系 |
|---|---|---|---|
| `logs_structured` | (由项目全局定义) | 实时结构化日志主表，保存解析后的登录/VPN 行为日志 | **只读输入** |
| `user_behavior_baselines` | `ReplacingMergeTree(created_at)` | 用户行为基线输出表 | **只读查询**（通过 `BaselineStore.get_user_baseline()`） |
| `ueba_baseline_training_logs` | `MergeTree()` | 手动维护的基线训练数据表，按 `dataset_id` 管理 | **不读取、不修改** |
| `ueba_validation_results` | `MergeTree()` | UEBA validation 评分结果表，append-only | **写入和只读查询** |

---

## 5. ueba_validation_results 表结构

表由 `UebaValidationRepository.ensure_table()` 创建，以下为当前代码中的真实字段：

| 字段 | 类型 | 含义 |
|---|---|---|
| `validation_id` | `String` | 单条 validation 结果唯一标识（SHA256 前 32 位） |
| `validation_run_id` | `String` | 一次批量 validation 运行的批次标识 |
| `source_identity` | `String` | 来源日志标识（`request_id:xxx` 或 `source_hash:xxx`） |
| `source_log_id` | `UInt64` | 来源 `logs_structured.id` |
| `timestamp` | `DateTime` | 原日志时间 |
| `username` | `String` | 用户名 |
| `log_type` | `String` | 日志类型（如 `vpn`） |
| `request_id` | `Nullable(String)` | 请求 ID（辅助定位） |
| `baseline_model_version` | `String` | 使用的 baseline 版本 |
| `baseline_created_at` | `Nullable(DateTime)` | 读取到的 baseline 创建时间（当前未填充，保留字段） |
| `baseline_is_reliable` | `UInt8` | baseline 是否可靠（0/1） |
| `ueba_score` | `UInt8` | UEBA 评分（0–100） |
| `ueba_risk_level` | `LowCardinality(String)` | 风险等级：`LOW` / `MEDIUM` / `HIGH` / `CRITICAL` |
| `ueba_anomaly_reasons` | `String` | JSON 数组，保存异常原因 code、分值、证据 |
| `validation_status` | `LowCardinality(String)` | 验证状态：`VALIDATED` / `NO_BASELINE` / `UNRELIABLE_BASELINE` |
| `validated_at` | `DateTime` | 评分执行时间 |
| `error` | `Nullable(String)` | 异常时记录错误信息 |
| `created_at` | `DateTime DEFAULT now()` | 结果入库时间 |

引擎与分区：

```text
ENGINE = MergeTree()
PARTITION BY toYYYYMM(timestamp)
ORDER BY (baseline_model_version, validation_run_id, log_type, timestamp, username, source_identity)
```

说明：

- `MergeTree`（非 `ReplacingMergeTree`）：append-only，保留所有评分历史，便于审计和回滚。
- `source_identity` 非 Nullable，在 ORDER BY 中用于区分同一用户同一秒的多条日志。
- `baseline_created_at` 是 DDL 中的保留字段，当前代码中实际写入为 NULL（`_build_result` 中硬编码 `baseline_created_at=None`）。后续可扩展为填充实际 baseline 的 `created_at`。
- `ueba_anomaly_reasons` 是 JSON 字符串，每个元素包含 `code`、`message`、`score_delta`、`evidence` 四个字段。

---

## 6. source_identity 设计说明

### 设计目的

- 为每条待评分日志生成稳定标识，不依赖 `source_log_id`（ClickHouse 中 `id` 不是唯一约束）
- 便于跨表关联（`logs_structured` ↔ `ueba_validation_results`）
- 便于导出、审计、复查
- 后续 dashboard 可通过 `source_identity` 关联展示

### 生成规则（`score_calculator.py:473–489`）

```python
def build_source_identity(target_log):
    # 优先使用 request_id
    if request_id is not None and request_id.strip():
        return f"request_id:{request_id}"

    # 回退到 source_identity（如果已存在）
    if target_log.source_identity is not None and target_log.source_identity.strip():
        return target_log.source_identity

    # 最终回退：对 15 个关键字段做 SHA256
    payload = [[field_name, str(getattr(target_log, field_name, None))]
               for field_name in SOURCE_IDENTITY_FIELDS]
    digest = hashlib.sha256(json.dumps(payload)).hexdigest()[:32]
    return f"source_hash:{digest}"
```

参与 hash 的 15 个字段：`timestamp`、`username`、`log_type`、`source_ip`、`destination_ip`、`src_country`、`src_city`、`vpn_gateway`、`action`、`event_type`、`result`、`auth_method`、`client_software`、`protocol`、`raw_log`。

### 边界说明

- 如果同一条 `logs_structured` 日志被两次 validation run 评分，`source_identity` 保持不变，但 `validation_id` 和 `validation_run_id` 不同。
- `source_identity` 生成发生在 `score_calculator._build_result()` 中，**每次评分都会重新计算**（不依赖 `ValidationTargetLog.source_identity` 是否已填充）。

---

## 7. validation_run_id 设计说明

### 设计目的

- 标识一次 validation 批量执行批次
- 把同一轮运行产生的多条 validation 结果归组
- 便于查询"最近一次运行的所有结果"
- 便于对比不同 `model_version` 的评分差异
- 便于排查重复运行

### 生成规则（`validation_service.py:391–416`）

用户可通过 `--validation-run-id` 显式传入。若不传，自动生成：

```python
created_at = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
raw = "|".join([start_time, end_time, log_type, model_version, "dry" or "write", created_at])
short_hash = hashlib.sha256(raw.encode()).hexdigest()[:8]
return f"ueba_validation_{created_at}_{short_hash}"
```

格式：`ueba_validation_YYYYmmddHHMMSS_xxxxxxxx`

### 边界说明

- dry-run 和 `--write` 模式均生成 `validation_run_id`，在 id 中包含 `"dry"` / `"write"` 标记
- 同一个 `validation_run_id` 可能对应多条 `validation_id`（每条被评分的日志一条）

---

## 8. validation_id 设计说明

### 设计目的

- 标识**单条** validation 结果
- 支持幂等追踪、导出和测试断言
- 同一 run 内对同一条源日志产生可复现的 `validation_id`

### 生成规则（`score_calculator.py:366–384`）

```python
raw = "|".join([
    validation_run_id,
    source_identity,
    model_version or "",
    str(target_log.timestamp),
    target_log.username,
])
return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:32]
```

### 边界说明

- 不同 `validation_run_id` 对同一条源日志产生不同的 `validation_id`（满足审计需求）
- 同一 `validation_run_id` 内同一条源日志的 `validation_id` 是确定性的（支持去重查询）
- 使用 SHA256 前 32 位十六进制，碰撞概率极低

---

## 9. 时间处理规则

### 输入时间解析

CLI 接收 `--start-time` / `--end-time` 字符串，直接传递到 `validation_repository`，由 ClickHouse 进行时间比较。未在 Python 侧解析 CLI 时间（与 `build_ueba_baseline.py` 不同）。

### ClickHouse DateTime 写入

`validation_repository._to_clickhouse_datetime()` 采用 **wall-clock 保护策略**：

1. `_parse_unlabeled_datetime()` 剥离时区标记（`Z`、`+08:00` 等），保持 wall-clock 值不变
2. 将解析后的 naive datetime 附加 `timezone.utc` 作为**传输编码**（防止 clickhouse-connect 在 `datetime.timestamp()` 转换时应用 Python 进程本地时区）
3. 代码注释明确："UTC tzinfo is transport encoding only"

### ClickHouse DateTime 查询输出

`_format_unlabeled_datetime()` 移除时区标签，输出 `YYYY-MM-DD HH:MM:SS` 格式。

### `validated_at`

由 `score_calculator._now_string()` 生成，使用 `datetime.now(timezone.utc).isoformat()`，格式为 ISO 8601（如 `2026-05-29T10:30:00+00:00`）。

### 注意事项

- 项目业务时间均为无时区标记的 wall-clock 值
- 如果 ClickHouse 服务器时区与业务时区不一致，DateTime 字段展示可能偏移，但**业务语义不受影响**（存储的是 wall-clock 值）
- 此设计在 `test_ueba_validation_repository.py` 中有完整测试覆盖（naive/aware datetime、ISO 字符串、时区标记字符串、空值处理）

---

## 10. run_ueba_validation.py 用法

### 脚本路径

```text
scripts/run_ueba_validation.py
```

### 用途

从 `logs_structured` 读取指定时间窗口的目标日志，查找对应 `user_behavior_baselines`，计算 UEBA 评分，默认 **dry-run（不写库）**，输出 JSON 摘要。

### 前置条件

1. ClickHouse 中 `log_analysis.logs_structured` 存在且有数据
2. ClickHouse 中 `log_analysis.user_behavior_baselines` 存在（至少有一个对应 `model_version` 的 baseline）
3. Python 虚拟环境已激活，`clickhouse_connect` 已安装

### 参数说明

| 参数 | 必选 | 默认值 | 说明 |
|---|---|---|---|
| `--start-time` | 是 | — | 目标日志时间窗口开始 |
| `--end-time` | 是 | — | 目标日志时间窗口结束 |
| `--model-version` | 是 | — | 要使用的 baseline 模型版本 |
| `--log-type` | 否 | `vpn` | 日志类型过滤 |
| `--limit` | 否 | `1000` | 最多读取的目标日志数（正整数） |
| `--sample-size` | 否 | `5` | 摘要中样例结果数量（非负整数） |
| `--write` | 否 | `False` | 实际写入 `ueba_validation_results` |
| `--dry-run` | 否 | `False` | 显式声明 dry-run（与 `--write` 互斥） |
| `--validation-run-id` | 否 | 自动生成 | 自定义 validation 运行批次 ID |
| `--host` / `--port` / `--username` / `--password` / `--database` / `--secure` | 否 | 环境变量 / localhost:8123 | ClickHouse 连接参数 |

### 典型命令

查看帮助（不连接数据库）：

```bash
PYTHONPATH=$(pwd) .venv/bin/python scripts/run_ueba_validation.py --help
```

Dry-run 评分（默认安全行为，不写库）：

```bash
PYTHONPATH=$(pwd) .venv/bin/python scripts/run_ueba_validation.py \
  --start-time "2026-06-01 00:00:00" \
  --end-time "2026-06-02 00:00:00" \
  --log-type vpn \
  --model-version "ueba_baseline_v1" \
  --limit 500
```

写入结果（需显式指定 `--write`）：

```bash
PYTHONPATH=$(pwd) .venv/bin/python scripts/run_ueba_validation.py \
  --start-time "2026-06-01 00:00:00" \
  --end-time "2026-06-02 00:00:00" \
  --log-type vpn \
  --model-version "ueba_baseline_v1" \
  --limit 500 \
  --write
```

指定自定义 validation_run_id：

```bash
PYTHONPATH=$(pwd) .venv/bin/python scripts/run_ueba_validation.py \
  --start-time "2026-06-01 00:00:00" \
  --end-time "2026-06-02 00:00:00" \
  --model-version "ueba_baseline_v1" \
  --validation-run-id "manual_review_20260601" \
  --write
```

### 输出结构

JSON 格式，包含以下关键字段：

```json
{
  "success": true,
  "dry_run": true,
  "validation_run_id": "ueba_validation_20260601120000_a1b2c3d4",
  "processed_count": 100,
  "scored_count": 95,
  "written_count": 0,
  "no_baseline_count": 2,
  "unreliable_baseline_count": 3,
  "failed_count": 0,
  "risk_level_counts": {"LOW": 80, "MEDIUM": 10, "HIGH": 5, "CRITICAL": 0},
  "validation_status_counts": {"VALIDATED": 95, "NO_BASELINE": 2, "UNRELIABLE_BASELINE": 3},
  "sample_results": [{"log_id": 1, "username": "alice", "score": 12, "risk_level": "LOW", ...}, ...],
  "message": "dry-run scored 95 of 100 target logs"
}
```

`sample_results` 中不包含 `raw_log`。

### 不适合在以下情况运行

- 生产高峰期（会读取 `logs_structured` 并可能产生 ClickHouse 查询负载）
- 未确认 `user_behavior_baselines` 已构建的时间窗口
- 没有可用的 baseline 版本时（所有日志会标记为 `NO_BASELINE`）

---

## 11. export_ueba_validation_results.py 用法

### 脚本路径

```text
scripts/export_ueba_validation_results.py
```

### 用途

从 `ueba_validation_results` 表只读查询 UEBA validation 结果，导出为 CSV（默认）或 JSON。

**严格只读。** 脚本源码经测试验证不含 `INSERT`、`UPDATE`、`ALTER`、`DELETE`、`TRUNCATE` 关键字。

### 前置条件

1. ClickHouse 中 `log_analysis.ueba_validation_results` 表存在且至少有数据
2. Python 虚拟环境已激活，`clickhouse_connect` 已安装

### 参数说明

| 参数 | 必选 | 默认值 | 说明 |
|---|---|---|---|
| `--start-time` | 是 | — | 查询时间窗口开始 |
| `--end-time` | 是 | — | 查询时间窗口结束 |
| `--model-version` | 是 | — | baseline 模型版本过滤 |
| `--log-type` | 否 | `vpn` | 日志类型过滤 |
| `--format` | 否 | `csv` | 输出格式：`csv` 或 `json` |
| `--output` | 否 | stdout | 输出文件路径（自动创建父目录） |
| `--limit` | 否 | `1000` | 最大返回行数（正整数） |
| `--validation-run-id` | 否 | — | 按 validation 批次过滤 |
| `--risk-level` | 否 | — | 按风险等级过滤（`LOW`/`MEDIUM`/`HIGH`/`CRITICAL`） |
| `--validation-status` | 否 | — | 按验证状态过滤（`VALIDATED`/`NO_BASELINE`/`UNRELIABLE_BASELINE`） |
| `--username-filter` | 否 | — | 按用户名过滤 |
| `--source-identity` | 否 | — | 按 source_identity 过滤 |
| `--host` / `--port` / `--username` / `--password` / `--database` / `--secure` | 否 | 环境变量 / localhost:8123 | ClickHouse 连接参数 |

### 导出字段

```text
validation_id, validation_run_id, source_identity, source_log_id,
timestamp, username, log_type, request_id,
baseline_model_version, baseline_is_reliable,
ueba_score, ueba_risk_level, ueba_anomaly_reasons,
validation_status, validated_at, error, created_at
```

**不包含 `raw_log`。**

### 典型命令

查看帮助（不连接数据库）：

```bash
PYTHONPATH=$(pwd) .venv/bin/python scripts/export_ueba_validation_results.py --help
```

CSV 输出到 stdout：

```bash
PYTHONPATH=$(pwd) .venv/bin/python scripts/export_ueba_validation_results.py \
  --start-time "2026-06-01 00:00:00" \
  --end-time "2026-06-15 00:00:00" \
  --model-version "ueba_baseline_v1"
```

JSON 输出到文件：

```bash
PYTHONPATH=$(pwd) .venv/bin/python scripts/export_ueba_validation_results.py \
  --start-time "2026-06-01 00:00:00" \
  --end-time "2026-06-15 00:00:00" \
  --model-version "ueba_baseline_v1" \
  --format json \
  --output reports/validation_20260601_20260615.json
```

按风险等级和批次过滤：

```bash
PYTHONPATH=$(pwd) .venv/bin/python scripts/export_ueba_validation_results.py \
  --start-time "2026-06-01 00:00:00" \
  --end-time "2026-06-15 00:00:00" \
  --model-version "ueba_baseline_v1" \
  --risk-level HIGH \
  --validation-run-id "ueba_validation_20260601120000_a1b2c3d4" \
  --format csv \
  --output reports/high_risk_results.csv
```

---

## 12. update_ueba_baseline_training_logs.py 用法

### 脚本路径

```text
scripts/update_ueba_baseline_training_logs.py
```

### 用途

从 `logs_structured` 手动复制数据到 `ueba_baseline_training_logs` 训练表。支持 **append**（追加）和 **replace**（先删除 dataset_id 旧数据再写入）两种模式。

### 参数说明

| 参数 | 必选 | 默认值 | 说明 |
|---|---|---|---|
| `--mode` | 是 | — | `append` 或 `replace` |
| `--dataset-id` | 是 | — | 训练数据集标识 |
| `--baseline-purpose` | 是 | — | 训练数据用途（如 `initial_build`） |
| `--import-batch-id` | 是 | — | 本次导入批次标识 |
| `--start-time` | 是 | — | 筛选开始时间 |
| `--end-time` | 是 | — | 筛选结束时间 |
| `--log-type` | 否 | `vpn` | 日志类型 |
| `--inactive` | 否 | `False` | 将 `is_active` 置为 0 |
| `--remark` | 否 | — | 备注 |
| `--created-by` | 否 | — | 创建人标识 |
| `--clickhouse-host` / `--clickhouse-port` / `--clickhouse-user` / `--clickhouse-password` / `--clickhouse-database` / `--clickhouse-secure` | 否 | 环境变量 / localhost:8123 | ClickHouse 连接参数 |

### 典型命令

```bash
PYTHONPATH=$(pwd) .venv/bin/python scripts/update_ueba_baseline_training_logs.py \
  --mode append \
  --dataset-id "initial_may_2026" \
  --baseline-purpose "initial_build" \
  --import-batch-id "batch_001" \
  --start-time "2026-05-01 00:00:00" \
  --end-time "2026-06-01 00:00:00" \
  --log-type vpn
```

**注意**：`replace` 模式使用 `ALTER TABLE DELETE` + `mutations_sync = 1`，在大数据量情况下会阻塞等待 mutation 完成。当前无 `--dry-run` 选项，执行前应确认 `dataset_id` 正确。

---

## 13. 真实库 schema 重建说明

### 建表方式

UEBA 专用表由各 Store / Repository 类的 `ensure_table()` 方法幂等创建：

- `user_behavior_baselines`：`BaselineStore.ensure_table()`
- `ueba_baseline_training_logs`：`TrainingLogStore.ensure_table()`
- `ueba_validation_results`：`UebaValidationRepository.ensure_table()`

每个方法使用 `CREATE TABLE IF NOT EXISTS`，可在已有 ClickHouse 实例上安全重复执行。

### 注意

- **不要直接依赖 `config/clickhouse.sql`** 作为 UEBA 专用表的 schema 来源。UEBA 表由 `ensure_table()` 保证存在，字段以当前代码为准。
- 重建前应备份或确认是测试库。
- 不得在生产/重要库中直接执行 `DROP TABLE` 或手动 `ALTER TABLE DELETE`。
- `config/clickhouse.sql` 可作为通用参考（如 `logs_structured` 通用字段），但 UEBA 专用表不应以它为准。

---

## 14. 验收记录

### 前置阶段已通过的验收

以下验收来自阶段 13–15（UEBA baseline 验收）和阶段 19-G–19-J2（UEBA validation 验收），本文档仅整理，非本轮重新执行：

| 验收项 | 阶段 | 结果 |
|---|---|---|
| `pytest tests/behavior -v`（baseline 链路） | 阶段 13 | 66 passed |
| `scripts/build_ueba_baseline.py --help` | 阶段 13 | 通过 |
| 真实 ClickHouse 100k 日志 baseline 构建 | 阶段 14 | 通过（0.5s，200 用户） |
| 真实 ClickHouse 完整 baseline 链路验收 | 阶段 15 | 通过（输入/输出/基线行数三者对账一致） |
| validation 模块单元测试 | 阶段 19-C–19-J2 | 通过（覆盖所有 validation 测试文件） |
| validation CLI `--help` 不连库 | 阶段 19-F | 通过 |
| export CLI `--help` 不连库 | 阶段 19-J2 | 通过 |
| export CLI 源码不含写库关键字 | 阶段 19-J2 | 测试断言通过 |

### 本轮文档级检查

以下检查在本轮（19-L）执行，仅做只读验证：

```bash
# 语法编译检查
PYTHONPATH=$(pwd) .venv/bin/python -m compileall src/behavior scripts tests/behavior

# Git 状态检查
git branch --show-current
git status --short
git diff --stat
git diff --check

# CLI help 检查（不连库）
PYTHONPATH=$(pwd) .venv/bin/python scripts/run_ueba_validation.py --help
PYTHONPATH=$(pwd) .venv/bin/python scripts/export_ueba_validation_results.py --help
PYTHONPATH=$(pwd) .venv/bin/python scripts/build_ueba_baseline.py --help
PYTHONPATH=$(pwd) .venv/bin/python scripts/update_ueba_baseline_training_logs.py --help
```

**本轮未执行数据库写入命令，未运行 pytest。**

---

## 15. 安全边界

### 本阶段边界

- 不触发真实 validation 运行
- 不写数据库
- 不删除数据
- 不修改 dashboard
- 不修改 training update 逻辑
- 不修复已知挂起问题（S1–S10）

### 运行 CLI 时的安全约束

- 运行任何 CLI 前必须先执行 `--help` 确认参数
- `run_ueba_validation.py` 默认 dry-run，**不指定 `--write` 绝不写库**
- `export_ueba_validation_results.py` 严格只读，无法写库
- `update_ueba_baseline_training_logs.py` 的 `replace` 模式需格外谨慎，确认 `dataset_id` 后再执行
- `build_ueba_baseline.py` 写入 `user_behavior_baselines`，使用 `ReplacingMergeTree`，重复构建会产生多版本记录

### 数据保护约束

- 不直接修改 `logs_structured`（UEBA validation 结果写入独立表 `ueba_validation_results`）
- 不修改 `user_behavior_baselines`（validation 链路只读）
- 不修改 `ueba_baseline_training_logs`（validation 链路不触碰）
- 不把 validation 逻辑混入 baseline 训练表更新

---

## 16. 后续开发建议

以下为已知挂起问题，19-L 不做修复，记录为后续阶段参考。

### 19-M：dashboard 接入前设计（建议下一步）

**核心原则**：dashboard 第一版建议**只读** `ueba_validation_results`，不触发 validation、不重跑 baseline、不写库、不接训练表、不修改 `logs_structured`。

涉及决策：

- 是否需要创建 `src/behavior/api.py` 作为 dashboard 适配层（当前 `dashboard.py:73-76` 导入不存在的模块）
- 如何按 `validation_run_id` 展示"最新一次 validation 结果"
- 是否需要为 dashboard 增加分页查询

### CLI 安全性增强

| 编号 | 事项 | 说明 |
|---|---|---|
| S1 | `update_ueba_baseline_training_logs.py` 增加 `--dry-run` | 当前 replace 模式无预览 |
| S2/S3/S4 | `limit` 增加最大值限制 | 当前仅限制为正整数，无上限 |
| S9 | `build_ueba_baseline.py` 增加 `--dry-run` | 当前执行即写入 |

### 代码质量改进

| 编号 | 事项 | 说明 |
|---|---|---|
| S5 | 提取公共 `_rows_to_dicts` | 三处重复实现 |
| S6 | 清理 `_to_float` 死代码 | `aggregate_merger.py:170-173` |
| S7 | `delete_dataset` 返回 deleted count | 审计不完整 |
| S8 | 评分权重提取到配置对象 | 当前硬编码在 `score_calculator.py` 类中 |

### 已知但本轮不处理

| 编号 | 事项 | 说明 |
|---|---|---|
| S10 | `dashboard.py` 导入不存在的 `src.behavior.api` | dashboard 阶段处理 |

---

## 17. 常用手动检测命令

以下命令仅做**只读检查**，不写数据库：

```bash
# Git 状态检查
git branch --show-current
git status --short
git diff --stat
git diff --check

# 语法编译检查
PYTHONPATH=$(pwd) .venv/bin/python -m compileall src/behavior scripts tests/behavior

# CLI help 检查（不连库）
PYTHONPATH=$(pwd) .venv/bin/python scripts/run_ueba_validation.py --help
PYTHONPATH=$(pwd) .venv/bin/python scripts/export_ueba_validation_results.py --help
PYTHONPATH=$(pwd) .venv/bin/python scripts/build_ueba_baseline.py --help
PYTHONPATH=$(pwd) .venv/bin/python scripts/update_ueba_baseline_training_logs.py --help

# 全部单元测试（不连库）
PYTHONPATH=$(pwd) .venv/bin/python -m pytest tests/behavior -v

# 仅 validation 链路测试
PYTHONPATH=$(pwd) .venv/bin/python -m pytest tests/behavior/test_ueba_validation_schemas.py \
  tests/behavior/test_ueba_score_calculator.py \
  tests/behavior/test_ueba_validation_repository.py \
  tests/behavior/test_ueba_validation_service.py \
  tests/behavior/test_run_ueba_validation_cli.py \
  tests/behavior/test_export_ueba_validation_results_cli.py -v
```

**注意**：涉及数据库写入的命令（如 `--write`、`--mode replace`）只能作为参考说明，不要在未确认目标库的情况下执行。

---

## 18. 文档维护说明

- 本文档随 UEBA validation 链路更新而更新
- 若新增 CLI 参数、表字段或评分规则，应同步更新对应章节
- 若 dashboard 完成接入，应新增 19-M 验收文档，并在本文档"后续开发建议"中更新状态
- `.trae/behavior/99-outdated-sources.md` 中的过时来源清单应在新阶段启动前复核
