# UEBA 准线验证与评分模块设计文档

## 1. 文档目的

本文档用于设计下一阶段 UEBA 准线验证 / 行为评分模块。该模块尚未实现，本文档只固化编码前的设计边界、推荐方案和待用户确认事项，不代表相关代码、表结构或 CLI 已经存在。

本文档在编码前明确以下内容：

- 数据表边界
- 字段方案
- 写回策略
- 模块拆分
- 评分规则
- CLI 设计
- 安全约束
- 测试计划
- 分阶段开发路线

本文档的核心结论是：第一版准线验证应优先输出到独立结果表 `ueba_validation_results`，不直接高频更新 `logs_structured`。如果后续业务坚持把评分写回主表，也只能作为受控批处理能力实现，并必须提供 dry-run、安全 WHERE、limit 和数量对账。

## 2. 当前 UEBA 数据链路回顾

当前 UEBA baseline 构建链路已经围绕三张核心表形成职责边界。

| 表 | 当前职责 | validation 模块关系 |
|---|---|---|
| `logs_structured` | 实时结构化日志主表，保存解析后的登录 / VPN 行为日志 | 后续评分模块的待验证日志来源，只读输入 |
| `ueba_baseline_training_logs` | 手动维护的 baseline 训练数据表，只用于初始化 / 手动更新 baseline | validation 模块不读取、不修改 |
| `user_behavior_baselines` | baseline 输出表，保存每个用户的行为基线 | validation 模块只读，不修改 |

当前 baseline 构建链路如下：

```text
logs_structured 或 ueba_baseline_training_logs
-> UebaRepository
-> AggregateMerger
-> BaselineBuilder
-> BaselineStore
-> user_behavior_baselines
```

当前链路的职责已经较清晰：

- `UebaRepository` 只做数据库侧聚合读取。
- `AggregateMerger` 只合并聚合结果。
- `BaselineBuilder` 只把聚合特征转成 `UserBaseline`。
- `BaselineStore` 只建表、序列化、批量写入和查询 baseline。
- `UebaService` 只做一次性 baseline 构建编排。
- `scripts/build_ueba_baseline.py` 只做 CLI 参数解析、组件组装和 JSON 输出。

准线验证 / 行为评分模块应沿用这种分层原则，不能把训练表更新、baseline 构建、评分计算、结果写入和 CLI 逻辑混在一起。

## 3. 新模块定位

准线验证模块的目标链路为：

```text
logs_structured 待评分日志
-> 读取 user_behavior_baselines 中对应 baseline
-> 规则评分
-> 输出 UEBA validation result
-> 优先写入 ueba_validation_results
-> 后续必要时再安全回填 logs_structured 的 UEBA 字段
```

第一版目标是单次批处理评分，不是实时流处理。建议先在小时间窗口内读取待评分日志，按用户读取 baseline，计算分数、等级和原因，再把结果写入独立结果表。这样可以先验证评分规则、数据边界、SQL 安全和对账能力，再评估是否需要把部分字段回填到 `logs_structured`。

该模块不是：

- 不是重新训练 baseline。
- 不是更新训练表。
- 不是修改 baseline 表。
- 不是 dashboard 接入。
- 不是实时流处理第一版。
- 不是异常告警闭环第一版。
- 不是 Kafka 消费器。
- 不是旧 dashboard 或旧 behavior API 的兼容层。

## 4. 当前 logs_structured 字段核查结论

当前 `logs_structured` 已有以下风险相关字段：

- `risk_score Nullable(UInt8)`
- `risk_tags Nullable(String)`

这些字段技术上可以写入数值和标签，但不建议作为 UEBA validation 输出字段。原因如下：

- 语义混淆：这些字段更像输入侧历史风险参考或 parser 从原始日志解析出的风险，不是 baseline validation 输出。
- 无 baseline model_version：无法判断评分基于哪个 baseline 版本。
- 无 validated_at：无法判断评分生成时间。
- 无 validation_status：无法区分已评分、无 baseline、baseline 不可靠、跳过和错误。
- 无结构化 anomaly reasons：`risk_tags` 只能保存粗粒度标签，不适合保存原因 code、分值和证据。
- 可能与 parser / 其他模块风险字段冲突：parser、旧 dashboard、异常检测表、AI 报告表都已经存在不同语义的风险字段。

当前 `logs_structured` 缺少以下 UEBA 专用字段：

- `ueba_score`
- `ueba_risk_level`
- `ueba_anomaly_reasons`
- `ueba_baseline_model_version`
- `ueba_validated_at`
- `ueba_validation_status`

因此，第一版不应直接把 `risk_score/risk_tags` 当作 UEBA validation 的正式输出。推荐新增独立结果表，或在后续用户确认后新增 UEBA 专用字段。

## 5. 字段方案

### 方案 A：复用现有 risk_score / risk_tags

该方案不新增字段，直接将 UEBA 评分写入 `logs_structured.risk_score`，将偏离原因写入 `logs_structured.risk_tags`。

优点：

- 不需要 schema 变更。
- dashboard 可能已有 `risk_score` 展示逻辑。
- 查询主表时可以直接看到风险分。

缺点：

- 风险语义污染。
- 无法区分 parser 风险与 UEBA 风险。
- 无法追踪 baseline 版本。
- 无法判断是否已验证。
- 不利于审计和回滚。
- 不利于多模型版本、多次评分结果并存。
- 无法表达 `NO_BASELINE`、`UNRELIABLE_BASELINE`、`SKIPPED`、`ERROR` 等状态。

结论：

- 不推荐作为正式方案。
- 最多只能作为临时展示适配。
- 即便用于临时展示，也应由结果表生成后的展示层适配，而不是直接覆盖 parser 输入侧字段。

### 方案 B：新增 UEBA 专用字段或结果表

推荐字段如下：

| 字段 | 类型建议 | 是否必须 | 含义 |
|---|---|---:|---|
| `ueba_score` | `Nullable(UInt8)` | 是 | 0-100 UEBA 评分 |
| `ueba_risk_level` | `Nullable(String)` 或 `LowCardinality(Nullable(String))` | 是 | `LOW` / `MEDIUM` / `HIGH` / `CRITICAL` |
| `ueba_anomaly_reasons` | `Nullable(String)` | 是 | JSON 数组，保存原因 code、分值、证据 |
| `ueba_baseline_model_version` | `Nullable(String)` | 是 | 使用的 baseline 版本 |
| `ueba_validated_at` | `Nullable(DateTime)` | 是 | 评分写入时间 |
| `ueba_validation_status` | `Nullable(String)` | 是 | `VALIDATED` / `NO_BASELINE` / `UNRELIABLE_BASELINE` / `SKIPPED` / `ERROR` |

说明：

- 如果写回 `logs_structured`，需要新增这些字段。
- 如果新增结果表，这些字段应作为结果表字段。
- 新环境可以通过 `config/clickhouse.sql` 初始化。
- 已存在环境必须通过 migration 或 ensure 方法幂等补齐。
- 只改 `config/clickhouse.sql` 不能更新已有 ClickHouse 实例。
- 是否修改 `config/clickhouse.sql`、是否新增 migration / ensure 方法，必须由用户后续确认。

## 6. 写回主表 vs 新增结果表对比

| 方案 | 优点 | 缺点 | 当前推荐 |
|---|---|---|---|
| 复用 `risk_score/risk_tags` | 零 schema 成本 | 语义污染严重 | 不推荐 |
| 新增 UEBA 字段后写回 `logs_structured` | 查询主表方便 | ClickHouse mutation 成本高，误更新风险高 | 条件推荐 |
| 新增 `ueba_validation_results` | 追加写入安全，便于审计和回滚 | 查询时需要 join 或二次查询 | 推荐 |
| 先写结果表，后续异步回填主表 | 安全、可验收、可回滚 | 多一步同步逻辑 | 最推荐 |

本文档推荐：

第一版优先新增结果表 `ueba_validation_results`，不直接高频更新 `logs_structured`。

如果后续必须写回 `logs_structured`，只能做批处理，不做逐行实时 `UPDATE`。写回必须具备：

- dry-run
- 安全 WHERE
- 时间窗口
- `log_type`
- `limit`
- 最大更新条数限制
- selected / scored / updated / skipped / failed 数量对账

ClickHouse 的 `ALTER TABLE ... UPDATE` 属于 mutation，不适合高频逐行实时评分。主表写回应该视为后续可选优化，而不是第一版默认输出路径。

## 7. 推荐结果表设计

推荐新增 `ueba_validation_results` 表作为第一版 validation 输出表。该表是追加型结果表，不修改 `logs_structured` 原记录。

字段草案：

| 字段 | 类型建议 | 含义 |
|---|---|---|
| `validation_id` | `String` | 本次验证结果 ID，可由 log 定位字段、model_version 和 validated_at 组合生成 |
| `source_log_id` | `UInt64` | 来源 `logs_structured.id` |
| `timestamp` | `DateTime` | 原日志时间 |
| `username` | `String` | 用户名 |
| `log_type` | `String` | 日志类型，例如 `vpn` |
| `source_ip` | `Nullable(String)` | 来源 IP |
| `action` | `String` | 行为动作 |
| `event_type` | `Nullable(String)` | 事件类型 |
| `result` | `Nullable(String)` | 登录结果 |
| `baseline_model_version` | `String` | 使用的 baseline 版本 |
| `baseline_created_at` | `Nullable(DateTime)` | 读取到的 baseline 创建时间 |
| `baseline_is_reliable` | `UInt8` | baseline 是否可靠 |
| `ueba_score` | `UInt8` | 0-100 UEBA 评分 |
| `ueba_risk_level` | `String` | `LOW` / `MEDIUM` / `HIGH` / `CRITICAL` |
| `ueba_anomaly_reasons` | `String` | JSON 数组，保存原因 code、分值、证据 |
| `validation_status` | `String` | `VALIDATED` / `NO_BASELINE` / `UNRELIABLE_BASELINE` / `SKIPPED` / `ERROR` |
| `validated_at` | `DateTime` | 评分时间 |
| `raw_log_ref` | `Nullable(String)` | 原始日志引用，不建议复制完整 raw_log |
| `request_id` | `Nullable(String)` | 请求 ID，用于辅助定位 |
| `created_at` | `DateTime DEFAULT now()` | 结果入库时间 |

引擎取舍：

- `MergeTree`：推荐作为第一版默认。保留所有评分结果，便于审计、回滚和比较不同 model_version。
- `ReplacingMergeTree`：适合希望同一条日志、同一 model_version 只保留最新评分结果的场景。但 ClickHouse 后台合并不是强实时去重，查询最新结果仍需按 `validated_at` 或 `created_at` 取最新。

当前推荐：

- 第一版使用追加型 `MergeTree` 或带明确去重 key 的 `ReplacingMergeTree` 都可接受。
- 如果目标是审计优先，选择 `MergeTree`。
- 如果目标是幂等重跑优先，选择 `ReplacingMergeTree(validated_at)`，但必须在查询时明确“最新结果”的读取口径。

查询与排序建议：

- 应支持按 `baseline_model_version` 查询。
- 应支持按 `timestamp` 时间窗口查询。
- 应支持按 `username` 查询。
- 应支持按 `ueba_risk_level` 查询。
- 可按 `toYYYYMMDD(timestamp)` 或 `toYYYYMM(timestamp)` 分区，具体粒度需要结合生产数据量确认。
- `ORDER BY` 可优先考虑 `(baseline_model_version, log_type, timestamp, username)` 或 `(log_type, timestamp, username, baseline_model_version)`。

后续 dashboard 可以优先读该表，或按 `source_log_id` / `timestamp` / `username` / `request_id` 与主表做二次查询展示。dashboard 接入不是第一版 validation 的交付内容。

## 8. 如果坚持写回 logs_structured 的安全方案

如果后续用户坚持把评分写回 `logs_structured`，必须遵守以下安全策略。

第一版不能做高频逐行实时 `UPDATE`。只能按小窗口批处理。

必须满足：

- dry-run 默认开启或显式支持。
- 必须有 `start_time`。
- 必须有 `end_time`。
- 必须有 `log_type`。
- 必须有 `limit`。
- 必须只更新 UEBA 专用字段。
- 禁止无 WHERE。
- 禁止无时间窗口。
- 禁止一次更新全表。
- 禁止修改 parser 原始字段。
- 禁止覆盖 `risk_score/risk_tags`，除非用户明确把它定义为临时展示适配。

定位单条记录不能只依赖 `id`。当前 `logs_structured.id` 是字段，不是 ClickHouse 唯一约束；ClickHouse 表结构本身不保证 `id` 唯一。

如果需要定位单条日志，应尽量使用复合条件：

- `id`
- `timestamp`
- `username`
- `request_id`
- `log_type`

建议策略：

- 如果复合条件命中 0 行：记录 skipped。
- 如果复合条件命中 1 行：允许写回。
- 如果复合条件命中多行：跳过或写结果表，不应强行 `UPDATE`。

批量写回必须输出：

- `selected`
- `scored`
- `updated`
- `skipped`
- `failed`
- `no_baseline`
- `unreliable_baseline`

写回前后必须数量对账：

- 待处理日志数。
- 评分结果数。
- dry-run 预期更新数。
- 实际更新条件命中数。
- 跳过原因分布。

如果对账不一致，应返回 `success=false`，不得伪造成功。

## 9. 模块结构设计

建议后续新增文件及职责：

| 文件 | 职责 |
|---|---|
| `src/behavior/score_calculator.py` | 纯规则评分；输入日志 + baseline，输出分数、等级、原因 |
| `src/behavior/validation_repository.py` | 读取待评分 `logs_structured`；写结果表或安全批量写回评分字段 |
| `src/behavior/validation_service.py` | 编排读取、baseline 查询、评分、dry-run/write、统计输出 |
| `scripts/run_ueba_validation.py` | CLI 入口，只解析参数、创建 client、调用 service |
| `tests/behavior/test_ueba_score_calculator.py` | 评分规则单测 |
| `tests/behavior/test_ueba_validation_repository.py` | SQL 安全、读取条件、写入测试 |
| `tests/behavior/test_ueba_validation_service.py` | service 编排、dry-run、跳过统计 |
| `tests/behavior/test_run_ueba_validation_cli.py` | CLI 参数与 help 不连接数据库测试 |

职责边界：

- `score_calculator.py` 不访问数据库。
- `validation_repository.py` 不实现评分算法。
- `validation_service.py` 只做编排，不拼接复杂 SQL。
- `scripts/run_ueba_validation.py` 不直接写 SQL、不直接实现评分规则。
- baseline 读取可复用 `BaselineStore.get_user_baseline()`，也可以通过只读 `BaselineReader` 包装，避免 validation service 依赖 baseline 写入能力。

测试和 fixture 边界：

- `src/behavior` 不能出现 `.tox`。
- `src/behavior` 不能出现 `fixture_user_%`。
- `src/behavior` 不能硬编码 `2026-05` / `2026-06`。
- `scripts` 不能写 `.tox`。
- `scripts` 不能硬编码 acceptance runner、manual runner、monthly runner 逻辑。
- `tests` 可以使用 fixture 和验收窗口。
- acceptance、manual runner、monthly runner 相关内容只能留在 `tests/behavior`。

## 10. 建议新增 dataclass / schema

以下 schema 只作为设计草案，后续实现时可放入 `src/behavior/schemas.py`，也可拆到 validation 专用 schema 文件。具体落点需在编码阶段结合现有导出边界确认。

### ValidationTargetLog

表示一条待评分日志。它是 repository 从 `logs_structured` 读取后交给 service / calculator 的稳定结构。

建议字段：

| 字段 | 含义 |
|---|---|
| `id` | 来源日志 ID |
| `timestamp` | 日志时间 |
| `username` | 用户名 |
| `log_type` | 日志类型 |
| `source_ip` | 来源 IP |
| `destination_ip` | 目标 IP |
| `src_country` | 来源国家 |
| `src_city` | 来源城市 |
| `vpn_gateway` | VPN 网关 |
| `action` | 行为动作 |
| `event_type` | 事件类型 |
| `result` | 登录结果 |
| `fail_reason` | 失败原因 |
| `auth_method` | 认证方式 |
| `client_software` | 客户端软件 |
| `protocol` | 协议 |
| `is_off_hours` | 输入侧非工作时间标签 |
| `is_unusual_ip` | 输入侧异常 IP 标签 |
| `request_id` | 请求 ID，用于辅助定位 |

### ScoreReason

表示一个评分原因。用于解释最终分数如何组成。

建议字段：

| 字段 | 含义 |
|---|---|
| `code` | 原因编码，例如 `NEW_SOURCE_COUNTRY` |
| `message` | 面向人类的简短说明 |
| `score_delta` | 该原因贡献的分值 |
| `evidence` | 证据字典，例如当前值、baseline common 值、ratio、阈值 |

### UebaValidationResult

表示一条日志的评分结果。

建议字段：

| 字段 | 含义 |
|---|---|
| `username` | 用户名 |
| `log_id` | 来源日志 ID |
| `timestamp` | 日志时间 |
| `score` | 0-100 UEBA 分数 |
| `risk_level` | 风险等级 |
| `reasons` | `ScoreReason` 列表 |
| `baseline_model_version` | 使用的 baseline 版本 |
| `validation_status` | 验证状态 |

### ValidationRunResult

表示一次 validation 批处理运行结果。

建议字段：

| 字段 | 含义 |
|---|---|
| `success` | 本次运行是否成功 |
| `selected_count` | 读取到的待评分日志数 |
| `scored_count` | 已完成评分的日志数 |
| `written_count` | 已写入结果表或主表的结果数 |
| `skipped_count` | 跳过数量 |
| `no_baseline_count` | 无 baseline 数量 |
| `unreliable_baseline_count` | baseline 不可靠数量 |
| `failed_count` | 处理失败数量 |
| `dry_run` | 是否 dry-run |
| `message` | 结果说明 |
| `error` | 失败时的错误信息 |

## 11. 评分规则初稿

第一版采用可解释的规则评分，不引入复杂模型。评分从 0 开始累加，最终裁剪到 0-100。

| 偏离项 | 建议分值 | 说明 |
|---|---:|---|
| 无 baseline | 15 | 标记 `NO_BASELINE`，不直接判高危 |
| baseline 不可靠 | 10 | 标记 `UNRELIABLE_BASELINE`，其他 baseline 相关权重减半 |
| 新来源国家 | 30 | 地理跨度大，权重较高 |
| 新来源城市 | 15 | 若国家也新，避免叠满 |
| 新来源 IP | 12 | 长尾 IP 用户降至 4-6 |
| 新 VPN gateway | 15 | VPN 接入路径变化 |
| 新认证方式 | 15 | 认证方式变化有安全意义 |
| 新客户端 | 10 | 中低风险 |
| 新协议 | 10 | 中低风险 |
| 非工作时间 | 10 | baseline off-hours 本来高时降权 |
| 登录失败 | 15 | baseline failed_rate 很低时可加到 20 |
| `is_unusual_ip` 标记 | 20 | 输入侧已有异常 IP 信号 |
| 新目标 IP | 8 | 辅助信号 |

风险等级映射：

- `LOW`：0-24
- `MEDIUM`：25-49
- `HIGH`：50-74
- `CRITICAL`：75-100

误报控制：

- `common_*` 只代表高频 Top-N，不代表历史全集。
- 单个“不在 common_* 中”只能作为弱信号。
- 高危应依赖多个信号叠加。
- 长尾 IP 用户应降低 IP 不匹配权重。
- 无 baseline 不应直接判高危。
- baseline 不可靠时不应把所有偏离都按满分计算。
- 新城市和新国家同时出现时应避免简单叠满；新国家可以覆盖或显著降低新城市的额外加分。
- 输入侧 `is_off_hours`、`is_unusual_ip` 是重要信号，但不是 UEBA 最终结论本身。

长尾 IP 用户建议判定方式：

- 如果 baseline 的 `common_source_ips` 为空，但 `sample_count` 较高，说明该用户可能没有稳定高频 IP。
- 如果 `common_source_ips` 中 Top-N 的最大 ratio 很低，也说明来源 IP 分散。
- 对这类用户，`NEW_SOURCE_IP` 权重建议降到 4-6。
- 新国家、新城市、失败登录、异常 IP 标签仍应正常计分。

## 12. CLI 设计

命令草案：

```bash
python scripts/run_ueba_validation.py \
  --start-time "2026-07-01 00:00:00" \
  --end-time "2026-07-01 01:00:00" \
  --log-type vpn \
  --model-version "ueba_baseline_v1" \
  --limit 1000 \
  --dry-run
```

参数说明：

- `--start-time`：必选，验证窗口开始。
- `--end-time`：必选，验证窗口结束。
- `--log-type`：默认 `vpn`。
- `--model-version`：选择 baseline 版本。
- `--limit`：单批最大处理数量。
- `--dry-run`：只评分不写入。
- `--write-mode`：`result-table` 或 `update-source-table`，后续可选。
- `--only-unvalidated`：只有结果表或 `ueba_validated_at` 支持后才可用。

第一版只做单次批处理：

- 不做常驻循环。
- 不做 daemon。
- 不接 Kafka。
- 不做 Flink / stream processing。
- 不做实时告警闭环。

CLI 输出应为 JSON，至少包含：

- `success`
- `selected_count`
- `scored_count`
- `written_count`
- `skipped_count`
- `no_baseline_count`
- `unreliable_baseline_count`
- `failed_count`
- `dry_run`
- `message`
- `error`

`--help` 必须不连接数据库。

## 13. 数据库安全约束

准线验证模块必须遵守以下数据库安全约束：

- 禁止无 WHERE UPDATE。
- 禁止无时间窗口 UPDATE。
- 禁止一次更新全表。
- 禁止逐条高频 UPDATE。
- 必须支持 dry-run。
- 必须输出处理统计。
- 必须只写 UEBA validation 结果字段。
- 不修改 `user_behavior_baselines`。
- 不修改 `ueba_baseline_training_logs`。
- 不使用 `risk_score` 判断是否已评分。
- 写回前后必须数量对账。
- 异常时 `success=false`，不伪造成功。
- 所有用户输入都必须参数化，不能直接拼进 SQL。
- 表名和数据库名必须受控白名单或标识符校验。
- `limit` 必须为正整数，并建议有上限。
- `start_time` 必须早于 `end_time`。
- 写结果表时也必须记录 source、model_version 和 validation_status，便于审计。

对于 `logs_structured` 主表回填：

- 只能写 `ueba_score`、`ueba_risk_level`、`ueba_anomaly_reasons`、`ueba_baseline_model_version`、`ueba_validated_at`、`ueba_validation_status` 等 UEBA 专用字段。
- 不得修改 `risk_score`、`risk_tags`、`raw_log`、`parser`、`parse_status`、`timestamp`、`username` 等原始或解析字段。

## 14. 与训练表链路的关系

准线验证模块：

- 不读取 `ueba_baseline_training_logs`。
- 不修改 `ueba_baseline_training_logs`。
- 不修改 `user_behavior_baselines`。
- 不重新训练 baseline。
- 只读取 `logs_structured` 与 `user_behavior_baselines`。
- 输出 validation result。

训练表链路：

- 仍用于初始化 / 手动更新 baseline。
- 仍由 `TrainingLogStore` 和 `scripts/update_ueba_baseline_training_logs.py` 管理。
- 与 validation 评分链路分离。

推荐关系：

```text
训练链路：
logs_structured
-> ueba_baseline_training_logs
-> build_ueba_baseline.py
-> user_behavior_baselines

验证链路：
logs_structured
-> read user_behavior_baselines
-> score_calculator
-> ueba_validation_results
```

validation 链路不应为了排除测试数据而在正式模块里硬编码 `fixture_user_%`。测试数据隔离应在 `tests/behavior` 或验收工具中完成。

## 15. 测试计划

### score_calculator 测试

建议覆盖：

- 正常日志低风险。
- 新国家。
- 新城市。
- 新 IP。
- 长尾 IP 降权。
- 非工作时间。
- 失败登录。
- `is_unusual_ip`。
- 无 baseline。
- 不可靠 baseline。
- 多信号叠加到 `HIGH` / `CRITICAL`。
- `common_*` 为空时不直接判高危。
- 新国家和新城市同时出现时不重复叠满。

### validation_repository 测试

建议覆盖：

- 读取待评分日志必须带时间窗口。
- 必须带 `log_type`。
- `limit` 必须为正。
- 不允许无条件 UPDATE。
- 写结果表字段正确。
- 主表写回时 WHERE 安全。
- 不读取训练表。
- 不修改 baseline 表。
- 表名和 database 标识符校验。
- 参数化查询，不拼接用户输入。

### validation_service 测试

建议覆盖：

- dry-run 不写库。
- 无 baseline 计数。
- 不可靠 baseline 计数。
- 成功评分计数。
- 写入结果计数。
- 部分日志失败时返回结构化统计。
- 异常返回 `success=false`。
- baseline 读取失败时不修改数据库。

### CLI 测试

建议覆盖：

- `--help` 不连接数据库。
- 缺少 start/end 报错。
- `limit` 非法报错。
- dry-run 默认安全。
- `write-mode` 参数受控。
- `--only-unvalidated` 在未具备结果表或 `ueba_validated_at` 能力时应受控报错或禁用。

### 边界测试

必须覆盖：

- `src/behavior` 和 `scripts` 不出现 `.tox`。
- `src/behavior` 和 `scripts` 不出现 `fixture_user_%`。
- `src/behavior` 和 `scripts` 不硬编码 `2026-05` / `2026-06`。
- `src/behavior` 和 `scripts` 不出现 acceptance runner、manual runner、monthly runner 业务逻辑。
- tests 专用 fixture 留在 `tests/behavior`。

## 16. 分阶段开发计划

阶段 19-A：固化设计文档

- 输出本文件。
- 不写代码。
- 不改数据库初始化 SQL。

阶段 19-B：确定结果表 / 字段方案

- 用户确认是否新增 `ueba_validation_results`。
- 用户确认是否暂不写回主表。
- 用户确认是否允许后续修改 `config/clickhouse.sql`。
- 用户确认是否需要 migration / ensure 方法。

阶段 19-C：实现 score_calculator

- 纯 Python 规则评分。
- 不连数据库。
- 先用单元测试覆盖规则和误报控制。

阶段 19-D：实现 validation_repository 只读查询

- 读取待评分 `logs_structured`。
- 读取或复用 baseline reader。
- 支持 dry-run 所需的只读数据路径。

阶段 19-E：实现结果表写入

- ensure table。
- append validation result。
- 不更新 `logs_structured`。
- 输出 selected / scored / written / skipped 对账。

阶段 19-F：CLI

- 新增 `run_ueba_validation.py`。
- dry-run 默认安全。
- JSON 输出。
- `--help` 不连接数据库。

阶段 19-G：受控 ClickHouse 验收

- 小窗口数据。
- dry-run。
- result table 写入。
- 对账。
- 不运行 acceptance runner 作为正式 validation 逻辑的一部分。

阶段 19-H：可选主表回填

- 仅当用户确认需要。
- 严格 WHERE。
- 批处理。
- 对账。
- 保留 dry-run。
- 只写 UEBA 专用字段。

## 17. 用户待决策问题

后续开发前需要用户确认：

1. 是否接受新增 `ueba_validation_results` 作为第一版输出？
2. 是否暂缓直接写回 `logs_structured`？
3. 是否允许后续修改 `config/clickhouse.sql`？
4. 是否需要新增独立 migration / ensure 方法？
5. 是否接受第一版只做批处理，不做实时常驻循环？
6. 是否确认新增 `scripts/run_ueba_validation.py`？
7. 无 baseline 时风险等级是 `LOW` 还是 `MEDIUM`？
8. baseline 不可靠时是否继续评分，还是只标记状态？
9. 是否需要把 validation result 接入 dashboard？
10. 结果表是审计优先的 append-only，还是幂等重跑优先的 latest-only？
11. 如果主表回填，是否接受复合定位命中多行时跳过？

## 18. 文档使用约束

本文档用于指导后续 Codex 分阶段开发，但不把未确认方案写成已确定事实。

推荐方案：

- 第一版优先新增 `ueba_validation_results`。
- 第一版只做单次批处理。
- 第一版不直接高频更新 `logs_structured`。
- 第一版不读取、不修改 `ueba_baseline_training_logs`。
- 第一版只读 `user_behavior_baselines`。

待用户确认后才能执行的事项：

- 修改 `config/clickhouse.sql`。
- 新增 migration / ensure 方法。
- 新增 `scripts/run_ueba_validation.py`。
- 新增 `src/behavior/score_calculator.py`。
- 新增 `src/behavior/validation_repository.py`。
- 新增 `src/behavior/validation_service.py`。
- 真实写入 ClickHouse。
- 主表回填。
- dashboard 接入。

后续 Codex 开发本模块时，应先阅读本文档和当前 `.trae/behavior` 约束，再检查真实文件树。若本文档、当前代码和用户最新要求发生冲突，以用户最新要求为最高优先级，并先报告冲突。
