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

如果旧 README、旧 API 文档、旧任务清单、旧行为分析接口、旧可视化适配逻辑与当前 UEBA 第一版目标冲突，必须以用户当前要求和 .trae/behavior/99-outdated-sources.md 为准。

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

### 3.1 feature 合并后的来源状态更新

upstream feature PR #23（`190ff88 Merge pull request #23 from S7777777B/feature-unify`）已经更新了统一环境配置、ClickHouse 初始化脚本和配置读取方式。

因此，历史版本中“`config/clickhouse.sql` 是完全过时来源、不得查看或参考”的绝对说法需要修正为分级约束：

| 来源 | PR #23 后状态 | UEBA v1 使用规则 |
|---|---|---|
| `config/clickhouse.sql` | 已更新，可作为当前项目全局 ClickHouse 初始化脚本、通用日志表结构和部署初始化参考 | 可用于确认 `logs_structured` 等通用字段，但不可单独覆盖 UEBA 专用表、基线流程和代码结构设计 |
| `.env.example` | 可作为环境变量名称参考 | 不得把其中示例密钥、账号或密码硬编码进代码或 prompt |
| `src/utils/config.py` | 当前 `settings` 配置读取的重要来源 | 涉及 `.env`、ClickHouse、Streamlit、行为分析窗口等配置时必须优先阅读 |
| `src/storage/clickhouse.py` | 当前 ClickHouse 客户端封装来源，包含 `ClickHouseClient.from_settings()` 和连接重试逻辑 | Repository / Store / CLI 创建 client 时应优先兼容现有封装，而不是重新硬编码连接参数 |
| `docs/dashboard_continuous统一环境配置文档.md` | 当前 dashboard_continuous、settings、`.env`、ClickHouse 连接和初始化方式的重要说明来源 | 后续涉及配置读取、ClickHouse 初始化、dashboard_continuous 脚本时必须先阅读 |
| `src/behavior/api.py` | upstream feature 中的旧 behavior 接口风险来源 | 不得覆盖当前 UEBA v1 的 `service.py` 主线，不得反向决定当前架构 |

当前可将更新后的 `config/clickhouse.sql` 作为全局 ClickHouse 初始化脚本和通用日志表结构参考，但对于 behavior / UEBA v1，它不是唯一权威来源。UEBA 专用表、UEBA 基线流程和代码结构仍必须同时参考：

```text
当前真实代码
.trae/behavior 的 UEBA 设计
src/utils/config.py
src/storage/clickhouse.py
docs/dashboard_continuous统一环境配置文档.md
Parser / storage 当前实际 schema
```

如果 `config/clickhouse.sql` 与 UEBA prompt 或当前真实代码冲突，不能盲目采用，必须先报告冲突并请求确认。

### 3.2 config/clickhouse.sql 的 UEBA 使用边界

历史版本的 `config/clickhouse.sql` 曾经被标记为过时来源。PR #23 后该文件已经更新，修正了表结构兼容性问题，可作为当前全局 ClickHouse 初始化和 `logs_structured` 通用字段参考。

但当前不得仅凭 `config/clickhouse.sql` 单独固化 UEBA 表名、字段名或建表 SQL。

如需写数据库访问层，应通过 Repository 层保留字段适配空间，并与当前真实代码、最新 feature 配置说明、Parser / storage 当前 schema、`.trae/behavior` UEBA 设计共同校验。

当前阶段 `logs_structured` 登录 / VPN 主表结构应结合更新后的 `config/clickhouse.sql`、Parser / storage 当前实际 schema 和 UEBA prompt 共同确认。

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

如果 `config/clickhouse.sql` 与 UEBA prompt、用户当前要求或当前真实代码冲突，应先报告冲突，不要擅自按任一单一来源实现。

如果 README、旧 API、旧 prompt、旧 dashboard 或旧 SQL 中的数据库字段说明与用户新提供的表结构冲突，应先报告冲突，不要擅自按旧字段实现。

当前新表字段中没有 `endpoint`、`status`、`location`，不要再按这些旧字段设计登录基线。

API endpoint 聚合属于后续 API 日志扩展，不属于当前登录 / VPN 主表第一版核心能力。

旧 `src/behavior/api.py`、旧 dashboard 行为分析接口、旧 README 仍不能反向决定当前 UEBA v1 主线。

### 3.3 docs/behavior_api.md

docs/behavior_api.md 描述的是历史 Behavior 前端接口，例如 analyze_behavior_for_frontend(payload)。

当前 UEBA 第一版不以该接口为开发目标。

当前目标是实现离线一次性用户行为基线构建，例如：

build_baseline_once(start_time, end_time)

除非用户明确要求兼容旧前端接口，否则不要按 docs/behavior_api.md 设计当前模块。

### 3.4 .trae/quick-reference.md

.trae/quick-reference.md 只能作为历史速查参考，不能作为当前 behavior 模块架构依据。

其中可能包含旧导入路径、旧模块名或历史冲突内容。Codex 不得因为 quick-reference 中出现某个 import 路径，就假设该模块已经存在。

当前 UEBA 开发以 .trae/behavior 模块 prompt 和当前真实文件树为准。

### 3.5 .trae/task-checklist.md

.trae/task-checklist.md 是历史任务清单，只能作为进度参考，不能覆盖当前用户明确目标。

其中关于完整异常检测、异常时间检测、异常 IP 检测、异常频率检测、异常评分、动态更新等内容属于后续扩展，不属于当前第一版。

如果其中出现明文数据库账号、密码、路径等敏感信息，不得继续引用、复制或写入新文档/代码。

数据库连接配置必须来自 .env、环境变量或项目后续统一配置。

### 3.6 docs/ClickhouseManual.md

docs/ClickhouseManual.md 是历史部署参考，不作为当前 UEBA 数据库结构或连接配置依据。

其中出现的具体用户名、密码、本地路径不得复制到新代码、新文档或 prompt。

### 3.7 config/behavior.env 与 config/behavior.env.example

config/behavior.env 和 config/behavior.env.example 是旧版 Behavior 异常检测配置参考。

当前 UEBA 第一版不应直接采用以下旧配置作为核心设计依据：

- BEHAVIOR_TIME_WINDOW_HOURS
- ANOMALY_THRESHOLD
- MIN_SAMPLES_FOR_PROFILE

当前配置应以 UebaBaselineConfig 为准，面向离线一次性基线构建。

### 3.8 src/visualization/dashboard.py 中的旧表名依赖

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

1. 不仅凭 `config/clickhouse.sql` 单独反向设计 UEBA。
2. 不根据旧前端接口文档设计当前核心模块。
3. 不根据旧 dashboard 表名反向设计 UEBA 表。
4. 不根据历史任务清单提前实现实时检测或异常评分闭环。
5. 不复制旧文档中的明文账号、密码、路径。
6. 每次修改前以当前真实文件树为准确认文件是否存在。
7. 涉及配置读取、ClickHouse 连接或 dashboard_continuous 脚本时，必须先阅读 `docs/dashboard_continuous统一环境配置文档.md`。
