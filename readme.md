# 日志分析 AI 助手 (Log Analysis AI Assistant)

利用 AI 的理解能力，将枯燥的统计异常转化为可理解的安全报告，帮助安全人员快速分析系统日志并发现异常行为。

## 项目背景

企业每天都会产生大量日志，单纯的"登录失败"可能只是忘记密码，但"凌晨三点在异地 IP 连续尝试五个账号登录"则是典型的安全威胁。传统 ELK 看板需要安全专家手工配置规则，而 UEBA（User and Entity Behavior Analytics）通过关注"人"的行为模式来发现异常。

## 项目流程

![项目流程图](docs/项目流程图/80803580-3542-4636-a82e-e18cd2be98f5.png)

## 功能模块与验收标准

### 1. 开发环境 + 技术栈部署

**目标**：搭建全套日志处理环境

**交付物**：
- Python 3.9+ 环境
- Kafka 消息队列
- ClickHouse/ES 存储
- Flink 基础环境

**验收标准**：
- 所有服务正常启动
- Kafka 可收发消息
- 数据库可读写与连接

### 2. 日志采集模块

**目标**：实现日志全量/增量采集

**交付物**：
- 模拟日志生成器（VPN/OA/API/系统）
- Filebeat 采集配置文件
- 日志推送至 Kafka

**验收标准**：
- 自动生成多类型日志
- 日志实时流入 Kafka
- 支持增量采集

### 3. 日志结构化处理

**目标**：非结构化日志 → 标准格式

**交付物**：
- 标准日志字段定义（时间、用户、行为、IP 等）
- 正则/Logparser 解析规则
- Kafka 消费与清洗逻辑
- 结构化日志入库程序

**验收标准**：
- 杂乱日志转为标准 JSON
- 无乱码、无字段丢失
- 数据成功入 ClickHouse/ES

### 4. 用户行为建模 (UEBA)

**目标**：构建用户正常行为基线

**交付物**：
- 用户常用登录时段统计
- API 调用频率/常用 IP 统计
- 行为基线数据表
- 定时更新基线模型

**验收标准**：
- 生成每个用户正常行为特征
- 支持按时间窗口统计
- 基线可查询可更新

### 5. AI 异常分析引擎

**目标**：大模型智能安全研判

**交付物**：
- 接入通义千问/OpenAI API
- 构建异常检测规则
- 拼接异常上下文 Prompt
- 大模型判断攻击类型 + 风险等级

**验收标准**：
- 自动识别异常行为
- 输出攻击类型 + 风险等级
- LangChain 分析链正常运行

### 6. 自动化简报 + 可视化

**目标**：Streamlit 界面 + 日报生成

**交付物**：
- Streamlit Web 界面
- 展示实时日志流、异常排行
- 自动生成每日安全态势简报
- 展示 AI 处置建议

**验收标准**：
- 界面可实时查看日志
- 自动生成 PDF/文本简报
- 展示高危用户与评分

### 7. 测试用例 + 项目完善

**目标**：功能测试 + 异常场景

**交付物**：
- 构造 3 类正常日志场景
- 构造 3 类高危攻击场景
- 全流程联调测试
- 项目文档整理

**验收标准**：
- 所有功能正常运行
- 攻击识别准确
- 文档可复现环境

### 8. 实时告警 + 规则扩展

**目标**：实时告警与扩展能力

**交付物**：
- Flink 实时异常检测
- 高危事件即时提醒
- 自定义异常规则

**验收标准**：
- 异常事件秒级发现
- 支持自定义配置

## 技术栈

- **语言**: Python 3.9+
- **日志处理与存储**: Flink + Kafka + ClickHouse/Elasticsearch
- **日志解析**: Regex (正则表达式), Logparser
- **AI 框架**: LangChain, OpenAI API / 阿里云通义千问 API
- **Web 展示**: Streamlit
- **任务调度**: APScheduler / Celery
- **配置管理**: python-dotenv

## 项目结构

```
Log_Analysis_AI_Assistant/
├── README.md                 # 项目说明文档
├── requirements.txt          # Python 依赖包
├── .env.example             # 环境变量配置模板
├── src/                     # 源代码目录
│   ├── main.py              # 主程序入口
│   ├── collectors/          # 日志采集模块
│   ├── parsers/             # 日志解析模块
│   ├── storage/             # 数据存储模块
│   ├── behavior/            # 用户行为建模模块
│   │   ├── __init__.py
│   │   ├── normalizer.py    # 行为日志标准化
│   │   ├── baseline.py      # 行为基线
│   │   ├── user_profile.py  # 用户画像
│   │   ├── anomaly.py       # 异常检测
│   │   ├── repository.py    # 行为数据协议
│   │   ├── service.py       # 统一分析服务入口
│   │   └── schemas.py       # 行为模块共享结构
│   ├── ai/                  # AI 分析模块
│   ├── visualization/       # 可视化模块
│   ├── reports/             # 报告生成模块
│   └── utils/               # 工具类
├── config/                  # 配置文件目录
├── tests/                   # 测试目录
│   ├── __init__.py
│   ├── behavior/
│   │   ├── behavior_config_cli.py
│   │   ├── run_behavior.sh
│   │   ├── test_behavior.py
│   │   └── test_behavior_unit.py
│   ├── test_collectors.py
│   ├── test_parsers.py
│   └── ...
└── logs/                    # 运行日志目录
    └── .gitkeep
```

## 快速开始

### 1. 环境初始化

```bash
# 克隆项目
git clone <repository-url>
cd Log_Analysis_AI_Assistant

# 创建虚拟环境
python -m venv venv

# 激活虚拟环境 (Windows)
venv\Scripts\activate

# 安装依赖
pip install -r requirements.txt
```

### 2. 启动可视化仪表板

```bash
# 方式一：使用脚本启动
.\tests\run_dashboard.ps1

# 方式二：手动启动
venv\Scripts\activate
streamlit run src\visualization\dashboard.py --server.port 8501
```

访问 http://localhost:8501 查看仪表板。

### 3. 配置环境变量

```bash
# 复制环境变量模板
cp .env.example .env

# 编辑 .env 文件，填入必要的配置信息
# - AI API 密钥 (OpenAI 或阿里云通义千问)
# - Kafka 配置
# - ClickHouse 配置
```

### 4. 运行主程序

#### 代码风格
- 遵循 PEP 8 代码规范
- 使用 Black 进行代码格式化
- 使用 Flake8 进行代码检查

#### Git 工作流
```bash
# 1. 从主分支创建功能分支
git checkout -b feature/your-feature-name

# 2. 开发并提交
git add .
git commit -m "feat: add your feature description"

# 提交规范:
# feat: 新功能
# fix: 修复 bug
# docs: 文档更新
# style: 代码格式调整
# refactor: 代码重构
# test: 测试相关
# chore: 构建/工具链相关

# 3. 推送到远程
git push origin feature/your-feature-name

# 4. 创建 Pull Request
```

#### 分支管理
- `main`: 主分支，稳定版本
- `develop`: 开发分支，日常开发
- `feature/*`: 功能分支，开发新功能
- `bugfix/*`: 修复分支，修复 bug
- `release/*`: 发布分支，准备新版本

### 3. 模块开发流程

#### 开发新采集器
1. 在 `src/collectors/` 创建新的采集器文件
2. 继承 `BaseCollector` 基类
3. 实现 `collect()` 方法
4. 在配置文件中添加采集配置
5. 编写单元测试

#### 开发新解析器
1. 在 `src/parsers/` 创建新的解析器文件
2. 继承 `BaseParser` 基类
3. 实现 `parse()` 方法
4. 定义日志格式模式
5. 编写单元测试和样例日志

#### 开发 AI 分析功能
1. 在 `src/ai/` 创建分析器或更新现有分析器
2. 设计 Prompt 模板
3. 实现分析逻辑
4. 添加威胁分类规则
5. 编写测试用例

### 4. 测试流程

```bash
# 运行所有测试
pytest tests/

# 运行 behavior 模块测试
pytest tests/behavior -v

# 运行特定模块测试
pytest tests/test_collectors.py

# 生成测试覆盖率报告
pytest --cov=src tests/
```

### Behavior 模块说明

- `src/behavior/normalizer.py`：负责统一字段读取和日志标准化。
- `src/behavior/baseline.py`：负责用户行为基线统计。
- `src/behavior/user_profile.py`：负责用户画像构建。
- `src/behavior/anomaly.py`：负责规则型异常检测和评分。
- `src/behavior/service.py`：负责输出统一行为分析结果。

说明：
- `tests/behavior/run_behavior.sh` 和 `tests/behavior/behavior_config_cli.py` 是本地辅助脚本，不是核心 pytest 入口。
- `local_only/` 仅供本地调试；云端 behavior 测试不依赖 `local_only`，测试样本由 `pytest` fixture 构造。

### 5. 部署流程

#### 开发环境
```bash
python src/main.py
```

## 配置说明

### 环境变量 (.env)

```ini
# AI API 配置
OPENAI_API_KEY=your_openai_api_key
OPENAI_API_BASE_URL=https://api.openai.com/v1
OPENAI_MODEL=gpt-3.5-turbo

# 或使用阿里云通义千问
DASHSCOPE_API_KEY=your_dashscope_api_key

# Kafka 配置
KAFKA_BOOTSTRAP_SERVERS=localhost:9092
KAFKA_LOGS_TOPIC=logs_raw
KAFKA_ANALYZED_TOPIC=logs_analyzed

# ClickHouse 配置
CLICKHOUSE_HOST=localhost
CLICKHOUSE_PORT=8123
CLICKHOUSE_DATABASE=log_analysis
CLICKHOUSE_USER=default
CLICKHOUSE_PASSWORD=your_password

# 应用配置
LOG_LEVEL=INFO
REPORT_OUTPUT_DIR=reports/
```

## 开发工作流

### Git 工作流

```bash
# 从主分支创建功能分支
git checkout -b feature/your-feature-name

# 开发并提交
git add .
git commit -m "feat: add your feature description"

# 提交规范:
# feat: 新功能
# fix: 修复 bug
# docs: 文档更新
# style: 代码格式调整
# refactor: 代码重构
# test: 测试相关
# chore: 构建/工具链相关

# 推送到远程
git push origin feature/your-feature-name
```

### 测试流程

```bash
# 运行所有测试
pytest tests/

# 运行特定模块测试
pytest tests/test_collectors.py

# 生成测试覆盖率报告
pytest --cov=src tests/
```

## 常见问题

### Q: 如何添加新的日志源？
A: 在 `config/log_sources.yml` 中添加日志源配置，包括日志路径、格式类型、解析规则等。

### Q: 如何自定义异常检测规则？
A: 在 `src/behavior/anomaly.py` 中添加新的检测规则，或调整现有规则的阈值。

### Q: 如何更换 AI 模型？
A: 修改 `.env` 文件中的 AI 配置，支持 OpenAI、阿里云通义千问等多种模型。

### Q: 日志数据存储多久？
A: 默认存储 90 天，可在 ClickHouse 配置中调整数据保留策略。

## 贡献指南

1. Fork 本项目
2. 创建功能分支 (`git checkout -b feature/AmazingFeature`)
3. 提交更改 (`git commit -m 'Add some AmazingFeature'`)
4. 推送到分支 (`git push origin feature/AmazingFeature`)
5. 创建 Pull Request

## 许可证

MIT License

## 联系方式

如有问题或建议，请通过 Issue 反馈。
