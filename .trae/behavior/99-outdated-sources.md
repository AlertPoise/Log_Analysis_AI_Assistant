# UEBA 开发中过时信息防护说明

## 1. 当前权威依据

当前 behavior/UEBA 模块开发依据优先级如下：

1. 用户当前明确要求
2. .trae/behavior/99-outdated-sources.md
3. .trae/behavior/behavior-guide.md
4. .trae/behavior/UEBA-module-guide.md
5. .trae/behavior/00-ueba-overview.md 至 .trae/behavior/05-ueba-script-and-codex-rules.md
6. .trae/project-rules.md
7. .trae/ai-assistant-guide.md
8. 当前真实代码文件树

如果旧 README、旧 API 文档、旧任务清单、旧 SQL 文件、旧可视化适配逻辑与当前 UEBA 第一版目标冲突，必须以用户当前要求和 .trae/behavior/99-outdated-sources.md 为准。

.trae/behavior/99-outdated-sources.md 的作用是防止过时信息污染当前 UEBA 第一版开发。

## 2. 当前 UEBA 第一版不可偏离的目标

当前 UEBA 第一版只做：

- 从结构化日志数据库中按时间范围读取聚合结果
- 按用户生成行为基线
- 将用户行为基线写入数据库
- 返回构建统计信息

当前 UEBA 第一版不做：

- 实时更新
- 动态基线
- Kafka 实时消费
- Flink 实时检测
- 完整异常评分闭环
- 前端旧接口兼容

## 3. 已确认不作为当前开发依据的文件和内容

### 3.1 config/clickhouse.sql

config/clickhouse.sql 已被确认是过时文件。

当前不得根据 config/clickhouse.sql 固化 UEBA 表名、字段名或建表 SQL。

如需写数据库访问层，只能通过 Repository 层保留字段适配空间，不得把旧 SQL 文件中的字段当作最终权威字段。

当前阶段新的 `logs_structured` 登录 / VPN 主表结构由用户提供，应作为 behavior/UEBA 第一版数据库文档修订依据。

当前 `logs_structured` 关键字段摘要：

```text
timestamp
log_type
username
dept
role
action
event_type
result
fail_reason
source_ip
destination_ip
vpn_gateway
src_country
src_city
protocol
auth_method
client_software
session_id
is_off_hours
is_unusual_ip
session_duration_sec
bytes_sent
bytes_recv
risk_score
risk_tags
parser
parse_status
collected_at
```

如果 `config/clickhouse.sql` 与用户新提供的 `logs_structured` 表冲突，以用户新提供的表结构为准。

如果 README、旧 API、旧 prompt、旧 dashboard 或旧 SQL 中的数据库字段说明与用户新提供的表结构冲突，应先报告冲突，不要擅自按旧字段实现。

当前新表字段中没有 `endpoint`、`status`、`location`，不要再按这些旧字段设计登录基线。

API endpoint 聚合属于后续 API 日志扩展，不属于当前登录 / VPN 主表第一版核心能力。

### 3.2 docs/behavior_api.md

docs/behavior_api.md 描述的是历史 Behavior 前端接口，例如 analyze_behavior_for_frontend(payload)。

当前 UEBA 第一版不以该接口为开发目标。

当前目标是实现离线一次性用户行为基线构建，例如：

build_baseline_once(start_time, end_time)

除非用户明确要求兼容旧前端接口，否则不要按 docs/behavior_api.md 设计当前模块。

### 3.3 .trae/quick-reference.md

.trae/quick-reference.md 只能作为历史速查参考，不能作为当前 behavior 模块架构依据。

其中可能包含旧导入路径、旧模块名或历史冲突内容。Codex 不得因为 quick-reference 中出现某个 import 路径，就假设该模块已经存在。

当前 UEBA 开发以 .trae/behavior 模块 prompt 和当前真实文件树为准。

### 3.4 .trae/task-checklist.md

.trae/task-checklist.md 是历史任务清单，只能作为进度参考，不能覆盖当前用户明确目标。

其中关于完整异常检测、异常时间检测、异常 IP 检测、异常频率检测、异常评分、动态更新等内容属于后续扩展，不属于当前第一版。

如果其中出现明文数据库账号、密码、路径等敏感信息，不得继续引用、复制或写入新文档/代码。

数据库连接配置必须来自 .env、环境变量或项目后续统一配置。

### 3.5 docs/ClickhouseManual.md

docs/ClickhouseManual.md 是历史部署参考，不作为当前 UEBA 数据库结构或连接配置依据。

其中出现的具体用户名、密码、本地路径不得复制到新代码、新文档或 prompt。

### 3.6 config/behavior.env 与 config/behavior.env.example

config/behavior.env 和 config/behavior.env.example 是旧版 Behavior 异常检测配置参考。

当前 UEBA 第一版不应直接采用以下旧配置作为核心设计依据：

- BEHAVIOR_TIME_WINDOW_HOURS
- ANOMALY_THRESHOLD
- MIN_SAMPLES_FOR_PROFILE

当前配置应以 UebaBaselineConfig 为准，面向离线一次性基线构建。

### 3.7 src/visualization/dashboard.py 中的旧表名依赖

src/visualization/dashboard.py 中出现的以下表名属于可视化历史适配逻辑，当前视为过时依据：

- user_anomaly_scores
- anomaly_events
- daily_security_scores
- security_logs

当前 UEBA 第一版不得为了迎合这些旧表名而改变核心基线表设计。

如后续需要对接前端，应通过明确的适配层完成，不得反向用旧 dashboard 表名约束 UEBA 模块。

## 4. 需要阶段性修正的内容

根目录 readme.md 中关于 UEBA、Flink、实时告警、定时更新基线模型、完整异常检测闭环等描述可以保留为项目最终愿景或历史规划。

但当前第一版开发不得根据 readme.md 提前实现实时更新、Flink、完整异常检测闭环。

当前 behavior/UEBA 第一版阶段目标以 .trae/behavior 下的模块 prompt 为准。

## 5. 开发约束

Codex 在开发当前 UEBA 模块时必须遵守：

1. 不根据过时 SQL 文件固化表结构。
2. 不根据旧前端接口文档设计当前核心模块。
3. 不根据旧 dashboard 表名反向设计 UEBA 表。
4. 不根据历史任务清单提前实现实时检测或异常评分闭环。
5. 不复制旧文档中的明文账号、密码、路径。
6. 每次修改前以当前真实文件树为准确认文件是否存在。
