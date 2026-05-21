## 项目统一环境配置文档

### 1. 概述

本项目的所有环境相关配置均通过**配置文件**管理，无需修改代码即可在不同环境（开发、测试、生产）中部署。核心原则：

- **所有可变参数**（数据库地址、端口、账号、表名、AI API 密钥等）存储在 `.env` 文件中。
- **数据库表结构**独立存储在 `config/clickhouse.sql`，便于版本控制和环境同步。
- **日志采集规则**（Filebeat 输入）通过 `config/log_sources.yml` 定义（可选，用于生产环境）。
- **行为分析阈值**可通过 `.env` 或单独的 `config/behavior.env` 调整。

---

### 2. 配置文件总览

| 文件路径 | 用途 | 是否必须 |
|---------|------|----------|
| `.env`（项目根目录） | 核心环境变量：Kafka、ClickHouse、AI 平台、Streamlit 等 | ✅ 必须 |
| `config/clickhouse.sql` | ClickHouse 表结构定义（使用 `{CLICKHOUSE_DATABASE}` 和 `{CLICKHOUSE_TABLE}` 占位符） | ✅ 必须 |
| `config/log_sources.yml` | Filebeat 多日志源采集规则（生产环境推荐） | ❌ 可选 |
| `config/behavior.env` | 行为分析专用配置（可与主 `.env` 合并） | ❌ 可选 |
| `tests/collectors/gen_vpn_logs.py` | 测试日志生成器（用于开发/测试环境） | ❌ 仅测试 |

---

### 3. 核心环境变量（`.env` 文件）

在项目根目录创建 `.env` 文件，内容如下（请根据实际环境修改值）：

```ini
# ========== Kafka ==========
KAFKA_BOOTSTRAP_SERVERS=localhost:9092
KAFKA_LOGS_TOPIC=logs_raw
KAFKA_ANALYZED_TOPIC=logs_analyzed
KAFKA_CONSUMER_GROUP=log_analysis_group

# ========== ClickHouse ==========
CLICKHOUSE_HOST=localhost
CLICKHOUSE_PORT=8123
CLICKHOUSE_DATABASE=log_analysis          # 生产环境可改为 prod_logs
CLICKHOUSE_USER=your_user                  # 根据实际创建的用户
CLICKHOUSE_PASSWORD=your_password
CLICKHOUSE_TABLE=logs_structured           # 结构化日志表名

# ========== AI 平台（示例：智谱AI） ==========
AI_PLATFORM=zhipu
ZHIPU_API_KEY=your_api_key_here
ZHIPU_MODEL=glm-4-flash
ZHIPU_BASE_URL=https://open.bigmodel.cn/api/paas/v4/

# ========== 行为分析 ==========
BEHAVIOR_TIME_WINDOW_HOURS=24
ANOMALY_THRESHOLD=0.7
MIN_SAMPLES_FOR_PROFILE=10
ANALYSIS_WINDOW_MINUTES=5
ANALYSIS_INTERVAL_SEC=60

# ========== Streamlit 仪表板 ==========
STREAMLIT_SERVER_PORT=8501
STREAMLIT_SERVER_ADDRESS=localhost

# ========== 测试临时目录（仅测试脚本使用） ==========
TEST_TMP_DIR=./test_tmp
FILEBEAT_DATA_DIR=./test_tmp/filebeat_data
FILEBEAT_LOGS_DIR=./test_tmp/filebeat_logs
FILEBEAT_CONFIG_DIR=./test_tmp/filebeat_config

# ========== 日志生成速度（测试用） ==========
LOG_GEN_INTERVAL_SEC=1
LOGS_PER_BATCH=10
```

**说明**：
- 生产环境中，`CLICKHOUSE_HOST` 应改为实际服务器 IP 或域名。
- AI 平台支持 `kimi`、`siliconflow`、`dashscope`、`openai`、`zhipu`，只需设置 `AI_PLATFORM` 和对应的 API Key。
- 如果不需要 AI 分析，可以留空 API Key，行为分析仍会运行（仅无 AI 建议）。

---

### 4. 数据库表结构（`config/clickhouse.sql`）

此文件定义了 ClickHouse 中所有表的结构。**请确保此文件与代码一起版本管理**。脚本（如 `dashboard_continuous.py`）会在启动时自动执行此文件，将 `{CLICKHOUSE_DATABASE}` 和 `{CLICKHOUSE_TABLE}` 替换为 `.env` 中的实际值。

**使用方法**：
- 无需手动修改 SQL 文件，只需配置 `.env` 中的数据库名和表名。
- 若需修改表结构（如增加字段），在此 SQL 文件中修改后，重新运行初始化脚本即可。

**示例**（简化版，完整版见仓库）：
```sql
CREATE DATABASE IF NOT EXISTS {CLICKHOUSE_DATABASE};
USE {CLICKHOUSE_DATABASE};

CREATE TABLE IF NOT EXISTS {CLICKHOUSE_TABLE} (
    id UInt64,
    timestamp DateTime,
    username String,
    ...
) ENGINE = MergeTree() ORDER BY timestamp;
```

---

### 5. 如何在不同环境中部署

#### 5.1 开发环境（本地测试）

1. 克隆项目代码。
2. 复制 `.env.example` 为 `.env`，修改其中的 `CLICKHOUSE_DATABASE` 等为本地值。
3. 启动依赖服务（使用 `dashboard_continuous.py` 自动拉起 Kafka + ClickHouse 容器）：
   ```bash
   python tests/visualization/dashboard_continuous.py
   ```
4. 访问 `http://localhost:8501` 查看仪表板。

#### 5.2 生产环境（独立部署 Kafka/ClickHouse 集群）

1. 确保 Kafka 和 ClickHouse 集群已部署并可供访问。
2. 在部署机器上设置环境变量（或使用 `.env` 文件）：
   - `CLICKHOUSE_HOST` 指向集群地址。
   - `KAFKA_BOOTSTRAP_SERVERS` 指向 Kafka 集群。
3. 单独运行 Streamlit 仪表板（不运行测试采集脚本）：
   ```bash
   streamlit run src/visualization/dashboard.py --server.port 8501 --server.address 0.0.0.0
   ```
4. 如果需要行为分析和 AI 分析，单独启动后台服务（如使用 systemd 管理 `continuous_ingester` 和 `behavior_ai_analyzer` 线程）。

#### 5.3 测试环境（CI/CD）

- 使用 Docker Compose 一键拉起所有依赖（参考 `dashboard_continuous.py` 中的 `docker-compose.yml` 模板）。
- 通过环境变量覆盖配置（例如设置 `CLICKHOUSE_DATABASE=ci_test`）。
- 运行单元测试和集成测试。

---

### 6. 常见问题与配置调试

#### 6.1 如何确认配置是否生效？

- 在 Python 中打印配置值：
  ```python
  from src.utils.config import settings
  print(settings.clickhouse_host, settings.clickhouse_database)
  ```
- 检查日志输出（`logs/` 目录）中的 “当前显示: 实时数据 - 从 ClickHouse 获取” 等信息。

#### 6.2 修改配置后需要重启哪些服务？

- 修改 `.env` 后，需要重启所有 Python 进程（如 Streamlit、行为分析线程、Kafka 消费者）。
- 修改 `clickhouse.sql` 后，若表已存在且结构不兼容，需要手动 `DROP TABLE` 或执行 `ALTER`（脚本中的 `init_clickhouse_tables` 不会自动删除旧表）。

#### 6.3 如何支持多环境（开发/测试/生产）？

- 为每个环境创建不同的 `.env` 文件，例如 `.env.dev`、`.env.prod`。
- 启动脚本时指定环境文件：
  ```python
  from dotenv import load_dotenv
  load_dotenv('.env.prod')
  ```

#### 6.4 如何避免硬编码端口和地址？

- 所有连接参数都已通过 `settings` 读取，只需在 `.env` 中修改。
- 例如，将 `CLICKHOUSE_HOST` 从 `localhost` 改为 `10.0.0.5`，无需修改任何代码。

---

### 7. 配置管理最佳实践

1. **不要提交 `.env` 文件到版本控制**（加入 `.gitignore`）。提交一个 `.env.example` 模板即可。
2. **使用占位符和默认值**：在 `settings` 类中使用 `Field(default=..., env='...')` 提供合理默认值。
3. **配置校验**：在应用启动时检查关键配置是否存在（如 `clickhouse_host` 不能为空）。
4. **使用配置中心**（生产环境）：可将配置存储在 Consul、etcd 等，并让应用动态读取。
5. **日志记录配置**：启动时打印使用的数据库名和表名，便于排查。

---

### 8. 总结

通过统一的配置文件管理，本项目实现了：
- ✅ 无需修改代码即可切换数据库、Kafka、AI 平台。
- ✅ 一键部署到任何支持 Docker 和 Python 的环境。
- ✅ 清晰的配置文档和示例，降低维护成本。

**下一步**：请根据上述模板创建您的 `.env` 文件，并确保 `config/clickhouse.sql` 存在且正确。运行 `dashboard_continuous.py` 验证所有组件正常工作。