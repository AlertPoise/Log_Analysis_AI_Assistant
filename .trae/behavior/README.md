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
