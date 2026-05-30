# Behavior / UEBA 测试目录说明

## 1. 文档目的

本文件说明 `tests/behavior/` 目录中需要提交到仓库的测试、验收工具和测试说明的组织方式。

**核心原则**：

1. 测试入口和实际应用入口必须明确分离。
2. 正式运行代码不得依赖 `tests/`。
3. 正式运行代码不得依赖 `local_only/`。
4. `local_only/` 中的测试仅用于本地快速回归，不提交仓库（已由 `.gitignore` 排除）。
5. `tests/behavior/` 中应优先保留以下三类内容：
   - 轻量安全边界测试
   - 真实端到端验收测试
   - 可复用验收基础设施
6. 开发期局部 unit / mock / fake 测试不应继续堆积在 `tests/behavior/`，应放入 `local_only/tests/behavior/`。

---

## 2. 目录分层规则

### tests/behavior/security/

**定位**：提交级轻量安全边界测试。

**用途**：
- 保护只读 API 不出现写库逻辑。
- 防止恢复旧 behavior demo 接口。
- 防止 Repository 弱化 SQL 参数化或白名单校验。
- 防止 CLI 默认行为失去 dry-run 或只读约束。
- 防止正式应用入口依赖 `tests/` 或 `local_only/`。
- 防止 dashboard 重新读取旧 `logs_structured.risk_score` 作为 UEBA 结果。

**当前状态**：该目录将在后续阶段（P3）创建。当前 P1 不创建目录。

### tests/behavior/e2e/

**定位**：真实或接近真实 ClickHouse 环境的端到端验收测试。

**目标链路**：
```
日志生成
→ 写入 ClickHouse
→ baseline 构建
→ validation 执行
→ 写入 ueba_validation_results
→ 查询验证结果
→ 校验风险等级、高风险用户、model_version、validation_run_id
→ 通过正式只读 API 验证 dashboard 数据层可读
```

**当前状态**：该目录将在后续 validation e2e 阶段（P5）规划。当前 P1 不创建目录。当前 validation e2e 尚未完成。

### tests/behavior/ueba_baseline_acceptance/

**定位**：**baseline + training table 端到端验收基础设施**（不是完整 UEBA e2e）。

**当前已经覆盖**：
1. 生成确定性 fixture 日志（26 用户 × 66,130 条，May + June 双窗口）。
2. 写入 `logs_structured`（参数化 SQL，DELETE 范围限制在 `fixture_user_%`）。
3. 调用 `scripts/build_ueba_baseline.py` 构建 baseline。
4. 对比预期 baseline 与实际 baseline（19+ 字段，float 容差，长尾 IP 豁免）。
5. 执行 May / June 双窗口 training table 更新（replace + append 模式）。

**当前尚未覆盖**：
1. 执行 UEBA validation（`run_ueba_validation.py --write`）。
2. 写入 `ueba_validation_results`。
3. 校验风险等级分布（LOW / MEDIUM / HIGH / CRITICAL）。
4. 校验高风险用户。
5. 校验 `validation_run_id` 一致性。
6. 调用正式只读 API（`get_validation_summary` / `get_validation_ranking` / `get_user_validation_detail`）验证 dashboard 数据入口。

### local_only/tests/behavior/

**定位**：本地开发期 unit / mock / fake 测试。

**用途**：
- 验证单个类、函数或局部逻辑。
- 使用 FakeClient、Mock、monkeypatch 等假对象。
- 快速验证 schema、builder、service、repository、CLI 局部行为。
- 不作为正式应用入口。
- 不提交仓库（已由 `.gitignore` 排除）。

### local_only/tests/behavior/dashboard/

**定位**：UEBA dashboard 本地 mock / AST 测试。

**用途**：
- UEBA 专属 dashboard 完整 mock 测试。
- AST 静态检查。
- Streamlit 接入辅助验证。
- 不提交仓库。
- 不作为正式应用入口。

---

## 3. 测试分类原则

### A 类：本地开发测试

**特征**：
- 局部 unit / mock / fake。
- 不连接真实 ClickHouse。
- 不跑完整链路。
- 主要用于快速回归。
- 验证局部实现契约。

**目标位置**：`local_only/tests/behavior/`

### B 类：提交级安全边界测试

**特征**：
- 轻量。
- 稳定。
- 低成本。
- 保护跨模块关键约束。
- 不依赖真实数据库。
- 不过度绑定局部实现细节。

**目标位置**：`tests/behavior/security/`

### C 类：真实端到端验收测试

**特征**：
- 接近实际运行环境。
- 可以生成较大量日志。
- 写入测试 ClickHouse。
- 构建 baseline。
- 执行 validation。
- 查询结果并断言。
- 验证真实风险行为。
- 需要显式启用，不得默认执行破坏性写库操作。

**目标位置**：`tests/behavior/e2e/`

### D 类：验收基础设施或手动工具

**特征**：
- 可复用 fixture 生成器。
- ClickHouse 写入 helper。
- runner / validator / 报告生成器。
- 可能需要人工显式运行。

**目标位置**：`tests/behavior/ueba_baseline_acceptance/`

**说明**：是否迁移或拆分，需要后续阶段逐文件评估。

---

## 4. 实际应用入口

正式应用入口**只**位于 `src/` 和 `scripts/`：

| 入口 | 路径 |
|------|------|
| Baseline 构建 CLI | `scripts/build_ueba_baseline.py` |
| Validation 执行 CLI | `scripts/run_ueba_validation.py` |
| Validation 结果导出 CLI | `scripts/export_ueba_validation_results.py` |
| 训练表更新 CLI | `scripts/update_ueba_baseline_training_logs.py` |
| Dashboard 只读 API | `src/behavior/api.py` |
| Streamlit 仪表盘 | `src/visualization/dashboard.py` |

**约束**：

1. 正式应用入口不得 `import tests.*`。
2. 正式应用入口不得 `import local_only.*`。
3. Dashboard 手动更新器后续必须通过正式 `src/behavior` service 实现（建议命名为 `src/behavior/manual_update_service.py`），不得调用 `tests/` 或 `local_only/` 中的工具。
4. `tests/` 和 `local_only/` 只能验证正式入口，不能成为正式入口本身。

---

## 5. 当前整理阶段

### 已完成

- **P1**（当前）：新增 `tests/behavior/README.md`，固化目录规划和职责边界。

### 后续规划

| 阶段 | 内容 |
|------|------|
| P2 | 迁移第一批高置信局部测试到 `local_only/tests/behavior/` |
| P3 | 建立 `tests/behavior/security/` 提交级门禁 |
| P4 | 迁移已被 security 门禁替代的完整 mock 测试 |
| P5 | 扩展真实 validation e2e |
| P6 | 迁移剩余 validation mock 测试 |
| P7 | 实现 dashboard 手动更新器 |

### P1 边界

1. P1 不迁移文件。
2. P1 不删除测试。
3. P1 不创建新测试目录。
4. P1 不修改业务代码。
5. P1 不运行测试。
6. P1 只新增说明文档。

---

## 6. 当前约束

1. `local_only/` 不提交仓库（已由 `.gitignore` 排除）。
2. `tests/behavior/` 保留提交级安全边界、e2e 和验收基础设施。
3. 实际应用入口不得依赖测试目录。
4. `config/clickhouse.sql` 不是真实 schema 唯一来源，UEBA 专用表以 `src/behavior/` 源码和 `.trae/behavior/` 设计文档为准。
5. `docs/ClickhouseManual.md` 不允许修改。
6. 每个迁移阶段必须单独 commit，便于审核和回滚。
7. 后续所有测试迁移统一使用以下流程：
   ```
   cp 原文件 → local_only/
   cmp -s 原文件 local_only/副本
   pytest local_only/副本 -v
   git ls-files local_only/（确认空）
   git status（确认 local_only 未出现）
   git rm 原文件
   ```
8. 不允许直接粗暴删除历史测试。
9. 不允许在 validation e2e 建立前整体迁移 validation mock 测试。
10. Repository 和 CLI 完整 mock 测试必须先提取 security 门禁到 `tests/behavior/security/`，再迁移完整版到 `local_only/`。
11. UEBA 专属 visualization 测试应移出 `tests/visualization/`，归入 Behavior 测试体系（`tests/behavior/security/` 或 `local_only/tests/behavior/dashboard/`）。
12. Visualization 模块通用测试（如 `dashboard_continuous.py`）不得随意迁移。

---

*最后更新：2026-05-30 | 阶段：P1*
