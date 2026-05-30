# 第 20 阶段：UEBA Dashboard 管理与风险分析页面

> **优先级声明**：如本文件与旧 dashboard 注释、旧前端方案或历史描述冲突，以本文件为准。
>
> **保护声明**：未经用户明确授权，不得修改 `docs/ClickhouseManual.md`。未经用户明确授权，不得修改 `config/clickhouse.sql`。

---

## 1. 阶段定位

第 19 阶段已经结束。第 20 阶段单独处理 UEBA Dashboard 管理与风险分析页面。

第 20 阶段不再沿用 `19-P*`，内部步骤固定使用：

```text
20-A  审计与数据契约冻结
20-B  UEBA 静态页面骨架
20-C  补齐只读 API
20-D  接入真实只读展示
20-E  实现正式管理服务
20-F  接入页面写操作
20-G  视觉优化
20-H  测试与真实验收
```

---

## 2. 开发执行协议

第 20 阶段必须逐步执行。

每次只允许执行用户明确授权的一个步骤。完成当前步骤后，必须立即停止，输出修改文件、测试结果、Git 状态和剩余问题，等待用户确认。

未经用户明确授权，不得：

- 自动进入下一步骤
- 自动执行 `git add`
- 自动执行 `git commit`
- 自动执行 `git push`
- 顺手完成后续步骤
- 扩大允许修改范围
- 修改 `.trae/`

如实现过程中发现计划与当前代码事实不一致：

1. 立即停止编码。
2. 输出事实差异。
3. 说明需要修改的约束条目。
4. 等待用户授权。
5. 未经授权不得自行修改 `.trae/`。

---

## 3. 页面名称

统一使用：

```text
UEBA 准线与风险分析
```

将现有导航中的 `UEBA 异常排行` 在后续实现阶段改为 `UEBA 准线与风险分析`。

---

## 4. 页面定位

该页面是面向运维人员、安全分析人员和系统管理员的 UEBA 管理与风险分析工作台。

用户应能够：

- 查看当前 baseline 状态
- 查看当前运行默认参数
- 查看风险等级分布
- 查看近期风险行为
- 筛选风险事件
- 查看单用户风险详情
- 手动建立 baseline
- 手动更新训练日志并重建 baseline
- 手动运行 validation
- 写操作成功后自动刷新页面

---

## 5. 页面整体布局

页面采用左侧固定导航栏 + 右侧主内容区。

左侧保持五个页面：

```text
实时日志流
UEBA 准线与风险分析
安全评分看板
处置 + AI 建议
历史查询
```

当前页面需要高亮。

右侧主内容区分为五个区域：

```text
区域 1：页面标题与数据来源说明
区域 2：准线管理
区域 3：风险概览
区域 4：风险行为查询
区域 5：用户风险详情
```

---

## 6. 区域 1：页面标题

展示：

```text
UEBA 准线与风险分析
数据来源：user_behavior_baselines / ueba_validation_results
```

不得伪造数据来源。

---

## 7. 区域 2：准线管理

按钮：

```text
建立准线
更新准线
查看默认参数
运行风险分析
```

当前准线信息展示字段：

```text
当前准线版本
适用日志类型
训练时间范围
样本用户数
样本日志数
可靠用户数
不可靠用户数
最近更新时间
```

这些字段必须来自数据库聚合或正式 service 返回结果。不得硬编码。不得使用 fixture 数据代替真实结果。

### baseline 日志类型说明

`user_behavior_baselines` 当前未持久化 `log_type` 字段。

第 20 阶段页面显示：

```text
适用日志类型：VPN
```

该值来自 UEBA v1 当前固定业务边界，不是从 baseline 表读取。

不得：

- 将 `log_type` 描述为 baseline 表真实字段
- 在第 20 阶段修改 schema
- 为了页面展示向 baseline 表新增列

---

## 8. 当前准线选择与聚合规则

### 默认 baseline 选择规则（已冻结）

默认：

```text
优先读取最新 validation_run_id
→ 使用该批次对应的 baseline_model_version

如无 validation 结果
→ 回退到 user_behavior_baselines 中最近创建的 model_version
```

当前数据库中每个 `model_version` 仅对应一个训练窗口，可按 `model_version` 唯一聚合。

实现仍必须保留防御性处理：

```text
如未来同一个 model_version 出现多个训练窗口
→ 选择最近创建的窗口批次
→ 不得混合聚合多个窗口
```

### 聚合规则（已冻结）

- 样本用户数：`uniqExact(username)`
- 样本日志数：`SUM(sample_count)`
- 可靠用户数：`countIf(is_reliable = 1)`
- 不可靠用户数：`countIf(is_reliable = 0)`
- 训练开始时间：`min(baseline_start_time)`
- 训练结束时间：`max(baseline_end_time)`
- 最近更新时间：`max(created_at)`
- 无 baseline 时返回空结构：`{"success": True, "baseline": None}`

20-C 不得自行猜测这些规则。

---

## 9. 当前运行默认参数

页面必须使用标题 `当前运行默认参数`。不得默认使用 `当前准线参数`。

原因：部分构建参数只存在于正式配置对象中，并未随 baseline 持久化。不能把代码默认值伪装成数据库中已经生效的 baseline 参数。

展示字段（已冻结）：

- 最低样本数（可靠性判定）
- 活跃时段阈值
- 常用来源 IP TopN
- 常用地点 TopN

当前不存在独立的 `reliability_threshold` 配置。真实可靠性条件：

```python
is_reliable = sample_count >= min_sample_count
```

不得展示虚构的独立"可靠性阈值"。

如果字段名称或语义与现有配置不一致，以当前正式代码为准，不得根据视觉稿编造字段。

---

## 10. 区域 3：风险概览

左侧：风险等级分布柱状图。等级为 `LOW`、`MEDIUM`、`HIGH`、`CRITICAL`。

右侧：最近风险行为，基础字段：

```text
时间
用户名
来源 IP
登录地点
风险分数
风险等级
异常原因摘要
```

风险等级视觉规范：

| 等级 | 颜色 |
|------|------|
| LOW | 绿色 |
| MEDIUM | 蓝色 |
| HIGH | 橙色 |
| CRITICAL | 红色 |

---

## 11. 近期风险行为的默认筛选规则（已冻结）

"最近风险行为"不得混入完全正常的事件。

默认列表必须先排除 `NO_BASELINE`：

```text
validation_status != 'NO_BASELINE'
AND
(
    anomaly reasons 非空
    OR risk_level IN ('MEDIUM', 'HIGH', 'CRITICAL')
    OR validation_status != 'VALIDATED'
)
```

说明：

- `NO_BASELINE` 表示无法评估，不属风险事件，不得默认混入列表。
- `NO_BASELINE` 必须使用独立指标卡单独展示数量。
- 查询区必须允许主动筛选 `NO_BASELINE`。

完全正常的事件（以下全部满足）：

```text
ueba_score = 0
ueba_risk_level = LOW
validation_status = VALIDATED
ueba_anomaly_reasons = []
```

不得默认出现在"最近风险行为"中。但完全正常事件仍允许通过风险行为查询区主动检索。

规则已冻结。20-C 必须按冻结后的规则实现。

---

## 12. 增强事件字段与降级规则

增强字段：

```text
来源 IP
来源国家
来源城市
目标 IP
VPN 网关
认证方式
客户端
协议
```

事实约束：

```text
ueba_validation_results 不直接保存全部原始日志字段。
logs_structured 保存增强字段。
source_log_id > 0 时，可以尝试通过 logs_structured.id 回查。
source_log_id = 0 时，无法稳定回查。
```

实现原则：

```text
先查询 ueba_validation_results
→ 收集 source_log_id > 0 的事件
→ 批量回查 logs_structured
→ 在 Behavior API 层合并字段
→ 返回 dashboard
```

无法回查时必须显示：

```text
source_ip = "--"
location = "原始日志不可用"
```

禁止隐藏事件、伪造字段、修改数据库 schema、向 `ueba_validation_results` 强行增加字段。第 20 阶段不修改数据库表结构。

### source_log_id 回查唯一性约束（已核验）

20-A 已确认：

- `logs_structured.id` 类型为 `UInt64`，非 Nullable，无 AUTO_INCREMENT
- 当前已审计数据中，非零 `id` 没有重复
- 当前已审计数据中，`source_log_id > 0` 的 JOIN 回查成功率为 100%
- 数据库 schema 未强制保证 `logs_structured.id` 唯一
- `source_log_id = 0` 的根因是 fixture 生成器未提供 `id` 字段，不是正式 validation 链路丢失

20-C 必须按尽力回查实现：

| 情况 | 行为 |
|------|------|
| `source_log_id > 0` 且唯一匹配一条日志 | 合并来源 IP、国家、城市、VPN 网关等增强字段 |
| `source_log_id = 0` | `source_ip = "--"`，`location = "原始日志不可用"` |
| 无法匹配 | 降级为占位信息 |
| 匹配结果不明确 | 降级为占位信息，不得猜测 |

禁止：

- 隐藏事件
- 伪造字段
- 修改 schema
- 假设数据库强制保证 `id` 唯一
- 在第 20 阶段顺手修复 fixture

---

## 13. 区域 4：风险行为查询

基础筛选：

```text
时间范围
用户名
风险等级
日志类型
查询
重置
```

高级筛选：

```text
validation_run_id
model_version
validation_status
```

暂不强制加入 `source_ip`、`source_country`、`source_city`。原因：这些字段需要关联 `logs_structured`，第一版优先用于展示，不作为必需筛选条件。

---

## 14. 区域 5：用户风险详情

选择用户后展示：

```text
用户风险摘要
用户 baseline 摘要
异常事件明细
完整异常原因
```

用户 baseline 摘要可以展示：

```text
sample_count
is_reliable
baseline_start_time
baseline_end_time
model_version
created_at
failed_rate
off_hours_rate
unusual_ip_rate
avg_daily_events
common_source_ips
common_source_cities
common_source_countries
common_vpn_gateways
```

字段必须以正式 baseline 表真实结构为准。

---

## 15. 左侧系统状态边界

现有 sidebar 中存在硬编码数值。第 20 阶段不实现：

```text
真实 QPS 接入
全局日志统计刷新
storage / collectors 联动改造
其他页面数据源重构
```

但必须在视觉优化阶段增加明确标记 `系统状态（演示）`。禁止继续让硬编码数字看起来像真实生产数据。

侧栏硬编码指标只能作为视觉占位。不得将其描述为实时生产指标。不得在第 20 阶段顺手接入真实 QPS。不得扩大到 collectors、storage 或全局 dashboard 数据源重构。

---

## 16. visualization 目录边界

`src/visualization/dashboard.py` 继续作为唯一 dashboard 展示入口。

禁止新增：

```text
src/visualization/pages/
src/visualization/ueba_page.py
src/visualization/data_provider.py
src/visualization/mock_data.py
```

允许在 `src/visualization/dashboard.py` 内部新增局部辅助函数和明确分区注释。

业务逻辑、数据库查询和写操作编排必须进入 `src/behavior/`。

`dashboard.py` 禁止直接：

```text
拼 SQL
创建 ClickHouse client
导入 tests/
导入 local_only/
调用 acceptance runner
读取 .tox/
解析 fixture
调用 subprocess 执行 CLI
```

---

## 17. 正式管理服务

第 20 阶段新增：

```text
src/behavior/ueba_management_service.py
```

类名 `UebaManagementService`。

职责：

```text
建立 baseline
更新训练日志并重建 baseline
运行 validation
统一参数校验
统一阶段状态
统一异常结果
密码脱敏
统一关闭 ClickHouse client
限制重复并发触发
```

调用链：

```text
dashboard.py
→ UebaManagementService
→ 现有正式 service
→ repository / store
```

禁止 `dashboard.py → subprocess → CLI`。CLI 继续作为命令行入口，但 dashboard 不调用 CLI。

---

## 18. 三个正式写操作

### 建立准线

语义：使用指定历史窗口重新构建一个 baseline 版本。调用 `UebaService.build_baseline_once(...)`。

### 更新准线

语义：人工更新 training logs 数据集 → 基于训练表重新构建 baseline。

注意：这是人工触发的离线更新，不是实时动态更新，不是滑动窗口自动更新。

### 运行风险分析

语义：选择 validation 时间窗口 → 使用指定 baseline model_version → 执行 validation → 写入 `ueba_validation_results` → 返回 `validation_run_id` → 页面自动刷新。调用 `UebaValidationService.run(dry_run=False)`。

---

## 19. 三个写操作的数据契约（已冻结）

### 建立准线

必需输入：

- `baseline_start_time`
- `baseline_end_time`
- `model_version`

固定值：

- `log_type = "vpn"`

行为约束：

- 默认直接基于 `logs_structured` 的指定历史窗口构建 baseline
- 同名 `model_version` 已存在时必须明确警告
- 必须独立二次确认
- 构建完成后只刷新准线信息区
- 风险概览继续展示当前选中的既有 `validation_run_id`
- 如需生成新的风险结果，必须由用户单独点击"运行风险分析"

职责划分：

```text
建立准线
→ 直接基于 logs_structured 的指定历史窗口构建

更新训练日志并重建 baseline
→ 更新 training logs
→ 基于训练表重新构建
```

不得在第一版页面中为"建立准线"增加数据源切换选项。

### 更新训练日志并重建 baseline

必需输入：

- `training_start_time`
- `training_end_time`
- `dataset_id`
- `baseline_purpose`
- `mode`
- `rebuild_model_version`

自动生成：

- `import_batch_id`

默认值：

- `mode = "replace"`

高级设置：

- 允许用户选择 `append`

行为约束：

- `dataset_id` 由用户选择或填写
- `import_batch_id` 自动生成
- `rebuild_model_version` 由用户填写
- 更新失败时禁止继续重建 baseline
- 更新成功后基于训练表重建 baseline
- 必须独立二次确认
- 完成后刷新准线信息区
- 不自动运行 validation
- 不自动替换风险概览结果

### 运行风险分析

必需输入：

- `validation_start_time`
- `validation_end_time`
- `baseline_model_version`

固定值：

- `log_type = "vpn"`

默认生成：

- `validation_run_id`

高级设置：

- 允许用户覆盖 `validation_run_id`

行为约束：

- `baseline_model_version` 默认使用当前选中的版本
- 必须独立二次确认
- validation 成功后自动刷新：
  - 风险概览
  - 最近风险行为
  - 用户排行
  - 用户详情
- 最新 `validation_run_id` 自动带入查询条件

---

## 20. 长耗时操作策略

现有 baseline 构建为同步阻塞调用。

第 20 阶段只实现：

```text
同步执行
spinner
阶段提示
进程内重复触发拒绝
成功后刷新
失败后显示 stage 和 error
```

阶段提示示例：

```text
正在校验参数
正在构建准线
正在更新训练日志
正在运行风险分析
正在刷新页面
```

暂不实现：

```text
异步任务队列
后台 worker
精确百分比进度
跨进程分布式锁
```

进程内并发拒绝只保证当前 Streamlit 进程内互斥。它不等价于：

- 跨进程锁
- 分布式锁
- 数据库级锁
- 多实例协调
- 生产级任务队列

页面和文档不得将其描述为完整并发控制。

---

## 21. 空状态与错误状态

数据库无结果时显示：

```text
暂无 UEBA validation 结果
暂无可用 baseline
```

ClickHouse 不可用时显示：

```text
ClickHouse 不可用
```

Behavior API 不可用时显示：

```text
Behavior API 不可用
```

禁止静默回退 mock 数据、硬编码演示用户、硬编码虚假风险事件。

---

## 22. 页面安全展示约束

页面不得展示：

- ClickHouse 密码
- 数据库连接字符串
- 完整 Python traceback
- `raw_log` 原文
- 未脱敏的敏感配置
- 内部异常对象的完整 `repr`
- 可能包含凭证的命令行参数

错误信息必须经过脱敏，只展示：

- 操作阶段
- 简短错误摘要
- 可理解的处理建议

如需要调试完整异常，应写入后端日志，不得直接回显到页面。

---

## 23. CSS 与视觉边界

目标视觉：浅色背景、白色卡片、蓝紫色准线管理区、统一圆角、浅边框、轻阴影、风险等级彩色标签、当前导航红色高亮。

允许：

```text
在 dashboard.py 内集中维护局部 CSS
只调整 UEBA 页面
调整左侧导航文字
调整当前页面高亮
增加"系统状态（演示）"标记
```

禁止：

```text
大范围格式化 dashboard.py
重构其他四个页面
修改其他页面数据降级逻辑
引入大量第三方依赖
切换到 st.navigation
切换到 st.Page
```

继续使用现有 `st.sidebar`、`st.button`、`st.session_state.current_page` 路由机制。

---

## 24. 默认查询条件（已冻结）

- 查询时间范围：最近 7 天
- `validation_run_id`：默认最新批次
- `model_version`：由最新 `validation_run_id` 推导；无 validation 结果时取最新 baseline
- `log_type`：`"vpn"`
- 风险等级筛选：全部
- 最近风险事件 `limit`：20
- 用户排行 `limit`：20
- 用户详情 `limit`：50
- 查询最大 `limit`：1000
- 分页：第 20 阶段暂不实现

20-C 必须按以上默认值实现。

---

## 25. 分阶段计划

### 20-A：审计与数据契约冻结 — ✅ 已完成

20-A 已完成。已确认：

- dashboard 当前结构（五页手动路由、零 CSS、st.bar_chart）
- baseline 表真实结构（无 `log_type`、无 `reliability_threshold`）
- validation 表真实结构（`source_log_id` UInt64）
- `logs_structured` 表结构（`id` UInt64，已审计数据中非零 ID 无重复）
- `source_log_id=0` 根因（fixture 未设 `id`，非正式链路丢失）
- 非零 `id` 回查稳定（100% 匹配率）
- baseline 默认选择规则（已冻结）
- 默认查询条件（已冻结）
- 最近风险行为规则（已冻结：先排除 NO_BASELINE）
- 三个写操作默认契约（已冻结）

**20-A 已具备进入 20-B 的条件。** 未经用户明确授权，不得开始 20-B。

### 20-B：UEBA 静态页面骨架

原则上只修改 `src/visualization/dashboard.py`。

完成：标题区、准线管理区、当前准线信息、当前运行默认参数、风险分布区、最近风险行为、风险行为查询、用户风险详情、空状态、局部 CSS、导航名称调整。

不接新 API。不接写操作。不硬编码虚假风险数据。

### 20-C：补齐只读 API

优先修改 `src/behavior/api.py`。必要时最小修改 `src/behavior/validation_repository.py`、`src/behavior/baseline_store.py`、`src/behavior/repository.py`。

新增或扩展：

```text
get_baseline_summary()
get_baseline_detail()
get_recent_risk_events()
get_validation_ranking(username=...)
```

增强字段采用 `source_log_id` 尽力回查 `logs_structured`。所有 API 必须只读。禁止 INSERT、DELETE、ALTER、DROP、TRUNCATE、触发 baseline 构建、触发 validation。

### 20-D：接入真实只读展示

原则上只修改 `src/visualization/dashboard.py`。

完成：准线概览、风险分布、近期事件、用户排行、基础筛选、用户详情、空状态、错误状态、增强字段降级显示。

禁止静默回退 mock 数据。

### 20-E：实现正式管理服务

新增 `src/behavior/ueba_management_service.py`。

提供 `build_baseline()`、`update_training_and_rebuild()`、`run_validation()`。

实现：参数校验、同步执行、阶段状态、进程内并发拒绝、统一异常返回、密码脱敏、finally 关闭连接。

### 20-F：接入页面写操作

原则上只修改 `src/visualization/dashboard.py`。

实现：建立准线、更新准线、查看默认参数、运行风险分析、二次确认、spinner、阶段提示、错误提示、成功提示、自动刷新、最新 `validation_run_id` 自动回填。

### 20-G：视觉优化

只针对 UEBA 页面、导航文字、当前页高亮、系统状态演示标记。增加局部 CSS。禁止影响其他四个页面。

### 20-H：测试与真实验收

必须验证：

```text
五个导航页面仍存在
UEBA 页面正常加载
ClickHouse 无数据时空状态明确
ClickHouse 不可用时错误明确
dashboard 不直接拼 SQL
dashboard 不创建 ClickHouse client
dashboard 不导入 tests/
dashboard 不导入 local_only/
dashboard 不读取 .tox/
dashboard 不调用 acceptance runner
dashboard 不调用 subprocess CLI
baseline 建立入口可用
training logs 手动更新入口可用
validation 入口可用
写操作需要二次确认
重复写操作被拒绝
validation 成功后自动刷新
normal 用户 score = 0
combo 用户进入 CRITICAL
nobase 用户显示 NO_BASELINE
```

---

## 26. 第 20 阶段测试分层约束

测试分层必须延续第 19 阶段规则。

### 提交级 `tests/behavior/security/`

只保留轻量安全与架构边界门禁，例如：

- dashboard 不直接拼 SQL
- dashboard 不创建 ClickHouse client
- dashboard 不导入 `tests/`
- dashboard 不导入 `local_only/`
- dashboard 不读取 `.tox/`
- dashboard 不调用 acceptance runner
- dashboard 不调用 subprocess CLI
- dashboard 保留五个导航页面
- 正式 service 不依赖 `tests/`
- 正式 service 不依赖 `local_only/`
- 正式 service 不泄漏密码

### 本地 `local_only/tests/behavior/`

存放开发期 unit / mock / FakeClient 测试，例如：

- `UebaManagementService` 参数校验
- baseline 构建成功和失败
- training logs 更新成功和失败
- validation 成功和失败
- ClickHouse 异常模拟
- client `close()` 调用
- 密码脱敏
- 进程内并发拒绝
- API 聚合查询 mock 测试
- `source_log_id` 回查降级逻辑

### 持续要求

- `local_only/` 不得被 Git 跟踪
- `local_only/` 不得提交
- 正式代码不得 import `local_only`
- 开发期 mock 测试不得重新堆积回提交级目录

---

## 27. 默认允许修改范围

按步骤最小修改。

允许新增：

```text
src/behavior/ueba_management_service.py
```

允许按阶段最小修改：

```text
src/visualization/dashboard.py
src/behavior/api.py
src/behavior/validation_repository.py
src/behavior/baseline_store.py
src/behavior/repository.py
tests/behavior/security/test_dashboard_ueba_boundary.py
```

### `.trae` 文档修改权限

`.trae/behavior/20-ueba-dashboard-plan.md` 与 `.trae/behavior/README.md` 仅允许在方案固化任务中修改。固化完成后，它们不属于第 20 阶段开发的默认允许修改范围。

如后续实现过程中确实需要再次修改 `.trae/`：

1. 停止当前编码。
2. 输出事实差异。
3. 说明需要修改的约束条目。
4. 等待用户明确授权。
5. 获得授权后，单独执行文档修订任务。
6. 文档复核通过后，再恢复编码。

需要新增测试时，优先放入 `tests/behavior/security/` 或 `local_only/tests/behavior/`。

禁止默认修改：

```text
docs/ClickhouseManual.md
config/clickhouse.sql
scripts/
tests/behavior/ueba_baseline_acceptance/
```

如确认必须修改禁止范围，停止、输出原因、等待用户授权。

---

## 28. 第 19 阶段遗留问题边界

第 19 阶段中已经接受但暂不修复的技术债不得在第 20 阶段顺手处理：

```text
validation DB validator 没有强制 normal score=0 且 reasons=[]
run_validation_acceptance.py 缺少统一异常兜底
fixture 清理失败后未显式立即停止
validation_baseline_user_count 对 1、3 等值的语义不完整
cleanup 脚本示例窗口可能过时
一次性验收工具未显式关闭 ClickHouse client
```

---

## 29. 文档固化后的 `.trae` 修改边界

本文件固化完成后，默认禁止继续修改 `.trae/`。

如后续实现过程中发现：

- 文档与正式代码事实冲突
- 数据表结构与文档不一致
- service 调用链与预期不同
- 页面契约无法落地
- 允许修改范围不足

必须：

1. 停止当前编码。
2. 输出差异报告。
3. 等待用户授权。
4. 用户授权后再单独修改约束文档。
5. 约束更新完成并复核后，再恢复编码。

---

*固化版本：第 20 阶段方案 | 尚未开始实现*
