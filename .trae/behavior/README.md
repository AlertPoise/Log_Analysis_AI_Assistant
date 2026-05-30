# UEBA Module Guide 文档目录

本压缩包将 UEBA 模块指导文档拆分为 6 份 Markdown 文件，方便后续单独修改、提交给 Codex 或放入项目文档目录。

## 文档列表

| 文件名 | 内容 |
|---|---|
| `00-ueba-overview.md` | UEBA 模块总览、当前版本边界、输入输出、核心原则 |
| `01-ueba-module-structure.md` | 模块目录结构、各子模块职责、配置对象和数据结构 |
| `02-ueba-database-aggregation.md` | ClickHouse / 数据库侧聚合读取方案、SQL 设计 |
| `03-ueba-baseline-build.md` | 聚合结果合并、用户基线生成规则 |
| `04-ueba-storage-and-service.md` | 基线表设计、批量写入、Service 对外入口 |
| `05-ueba-script-and-codex-rules.md` | 脚本入口、后期接口扩展、Codex 开发约束 |
| `15-ueba-final-acceptance.md` | UEBA v1 阶段 12-14 最终验收结论、运行方式、字段事实和后续边界 |
| `20-ueba-dashboard-plan.md` | 第 20 阶段：UEBA Dashboard 管理与风险分析页面开发约束 |


## 相关外部配置说明文件

| 文件路径 | 用途 |
|---|---|
| `config/clickhouse.sql` | PR #23 后更新的全局 ClickHouse 初始化脚本和通用表结构参考 |
| `.env.example` | 环境变量模板参考，不得复制示例密钥或密码 |
| `src/utils/config.py` | `settings` 配置读取来源 |
| `src/storage/clickhouse.py` | `ClickHouseClient` 封装、`from_settings()` 和连接重试参考 |
| `docs/dashboard_continuous统一环境配置文档.md` | dashboard_continuous、统一环境配置、`.env` 与 ClickHouse 连接方式说明 |

## 当前 UEBA 第一版定位

当前第一版不是完整实时 UEBA 系统，而是：

```text
离线一次性用户行为基线构建模块
```

核心目标：

```text
从结构化日志数据库中读取指定时间范围内的日志聚合结果，
按用户生成行为基线，
批量写入数据库，
并保证至少十万级日志数据不会把 Python 侧冲烂。
```

## 当前版本明确不做

```text
不做 Kafka 实时消费
不做实时基线更新
不做滑动窗口增量更新
不做复杂机器学习
不直接读取十万级原始日志到 Python 内存
不保存每个用户的完整历史日志列表
```


## 当前 UEBA v1 已完成链路

截至阶段 15，`behavior-new` 分支已经完成并验收 UEBA v1 离线一次性用户行为基线构建链路：

```text
UebaRepository
-> AggregateMerger
-> BaselineBuilder
-> BaselineStore
-> UebaService
-> scripts/build_ueba_baseline.py
```

当前完成范围包括：

```text
数据库侧聚合读取 logs_structured
按 username 合并聚合 rows
生成 UserBaseline
批量写入 user_behavior_baselines
返回 BaselineBuildResult
通过 CLI 输出 JSON 结果
```

当前仍明确不做：

```text
实时检测
动态基线
Kafka / Flink 流处理
前端 dashboard 联调
旧 behavior_api 兼容
risk_score / risk_tags 最终异常检测闭环
```

## 推荐验收命令

```bash
source .venv/bin/activate
python --version
python -m pytest --version
```

```bash
python -m compileall src/behavior tests/behavior scripts/build_ueba_baseline.py
python -m pytest tests/behavior -v
```

```bash
python scripts/build_ueba_baseline.py --help
```

空窗口构建示例：

```bash
python scripts/build_ueba_baseline.py \
  --start-time "2026-05-01 00:00:00" \
  --end-time "2026-05-02 00:00:00" \
  --log-type vpn
```

非空窗口构建示例：

```bash
python scripts/build_ueba_baseline.py \
  --start-time "2026-05-01 00:00:00" \
  --end-time "2026-05-10 00:00:00" \
  --log-type vpn \
  --model-version ueba_baseline_v1_stage13 \
  --min-sample-count 20
```

阶段 14 本地验证过 100000 条日志、200 用户、每用户 500 条的构建，约 0.5 秒量级完成。该结果只是本机本次验证结果，不是生产 SLA。

最终验收摘要详见：

```text
.trae/behavior/15-ueba-final-acceptance.md
```
