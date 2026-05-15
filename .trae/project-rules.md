# 日志分析 AI 助手 - 项目开发规范

## 项目概述

构建一个日志分析 AI 助手，利用 UEBA（用户实体行为分析）和 AI 技术，帮助安全人员快速分析系统日志并发现异常行为。

## 技术栈

### 核心语言
- **Python 3.9+**

### 数据处理与存储
- **日志采集**: Filebeat, Flume
- **消息队列**: Kafka
- **数据存储**: ClickHouse（主）, Elasticsearch（可选）
- **日志解析**: Regex（正则表达式）, Logparser

### AI 与机器学习
- **AI 框架**: LangChain
- **大模型 API**: OpenAI API / 阿里云通义千问 API
- **行为分析**: 统计学异常检测，用户画像建模

### Web 与可视化
- **Web 框架**: Streamlit（快速构建可视化界面）
- **报表生成**: Markdown, PDF, HTML

### 工具与依赖
- **配置管理**: python-dotenv, pydantic-settings
- **日志工具**: loguru
- **任务调度**: APScheduler / Celery
- **HTTP 客户端**: requests

## 项目结构

```
Log_Analysis_AI_Assistant/
├── src/                     # 源代码目录
│   ├── main.py              # 主程序入口
│   ├── collectors/          # 日志采集模块
│   ├── parsers/             # 日志解析模块
│   ├── storage/             # 数据存储模块
│   ├── behavior/            # 用户行为建模模块
│   ├── ai/                  # AI 分析模块
│   ├── visualization/       # 可视化模块
│   ├── reports/             # 报告生成模块
│   └── utils/               # 工具类模块
├── config/                  # 配置文件目录
├── tests/                   # 测试目录
├── logs/                    # 运行日志目录
└── docs/                    # 文档目录
```

## 代码规范

### Python 编码规范

1. **文件命名**
   - 模块文件：小写字母 + 下划线（如 `user_profile.py`）
   - 包目录：小写字母（如 `collectors/`）
   - 测试文件：`test_` 前缀（如 `test_parsers.py`）

2. **类命名**
   - 使用 PascalCase（如 `UserProfile`, `AnomalyDetector`）
   - 异常类以 `Error` 结尾

3. **函数命名**
   - 使用小写字母 + 下划线（如 `parse_log`, `calculate_baseline`）
   - 私有函数以 `_` 开头

4. **变量命名**
   - 使用小写字母 + 下划线（如 `log_data`, `user_id`）
   - 常量使用大写字母 + 下划线（如 `MAX_RETRIES`, `DEFAULT_TIMEOUT`）

5. **文档字符串**
   - 所有模块、类、公共函数必须有文档字符串
   - 使用 Google 风格或 NumPy 风格
   - 包含参数说明、返回值说明、异常说明

6. **类型注解**
   - 所有函数参数和返回值必须有类型注解
   - 使用 `typing` 模块的类型（如 `Dict`, `List`, `Optional`）

7. **注释要求**
   - 复杂逻辑必须有注释说明
   - TODO 注释标记待开发功能
   - FIXME 注释标记需要修复的问题

### 代码示例

```python
"""
用户画像模块
TODO: 构建和维护用户行为画像
"""
from typing import Any, Dict, List, Optional
from datetime import datetime


class UserProfile:
    """用户画像类，用于存储和分析用户行为模式"""
    
    def __init__(self, username: str):
        """
        初始化用户画像
        
        Args:
            username: 用户名
        """
        self.username = username
        self.created_at = datetime.now()
        
    def add_login_record(self, timestamp: datetime, ip: str) -> None:
        """
        添加登录记录
        
        Args:
            timestamp: 登录时间
            ip: 登录 IP 地址
        """
        pass
```

## 架构约定

### 模块职责

1. **collectors/** - 日志采集层
   - 负责从各种日志源采集日志
   - 实现统一的数据采集接口
   - 支持增量采集和断点续传

2. **parsers/** - 日志解析层
   - 负责解析原始日志为结构化数据
   - 支持多种解析器（正则、JSON 等）
   - 实现字段标准化

3. **storage/** - 数据存储层
   - 负责日志数据的存储和查询
   - 实现 Kafka、ClickHouse、ES 等存储客户端
   - 支持批量插入和聚合查询

4. **behavior/** - 行为建模层
   - 构建用户行为画像
   - 计算行为基线
   - 实现异常检测算法

5. **ai/** - AI 分析层
   - 集成大模型 API
   - 实现威胁分类和风险评级
   - 生成处置建议

6. **visualization/** - 可视化层
   - 实现 Streamlit Web 界面
   - 展示实时日志、安全评分、异常排行
   - 提供交互式查询功能

7. **reports/** - 报告生成层
   - 自动生成日报、周报
   - 支持多种报告格式导出

### 数据流向

```
日志源 → collectors → Kafka → parsers → ClickHouse
                                    ↓
                              behavior → ai → visualization
                                    ↓
                              reports
```

## AI 协作约定

### 代码开发原则

1. **增量开发**
   - 优先实现核心功能框架
   - 使用 TODO 注释标记待完善功能
   - 避免一次性实现所有细节

2. **代码复用**
   - 优先使用项目已有的工具函数和类
   - 不要重复造轮子
   - 提取公共逻辑到 utils 模块

3. **依赖管理**
   - 新增依赖时说明原因和版本
   - 优先使用 requirements.txt 中已有的依赖
   - 避免不必要的依赖

4. **测试先行**
   - 为关键功能编写单元测试
   - 使用 pytest 框架
   - 保证核心逻辑有测试覆盖

### 代码输出规范

- 只输出修改的部分，不要输出完整文件
- 使用 SearchReplace 工具进行精确修改
- 对于复杂改动，先说明方案再实现
- 保持代码简洁，删除无用的注释和代码

## 开发工作流

### 1. 环境初始化

```bash
# 克隆项目
git clone <repository-url>
cd Log_Analysis_AI_Assistant

# 创建虚拟环境
python -m venv venv

# 激活虚拟环境（Windows）
.\venv\Scripts\activate

# 安装依赖
pip install -r requirements.txt

# 复制环境变量配置
cp .env.example .env

# 编辑 .env 文件，配置必要的 API 密钥和数据库连接
```

### 2. 开发流程

```bash
# 1. 创建功能分支
git checkout -b feature/your-feature-name

# 2. 实现功能并编写测试
# 编辑代码...

# 3. 运行测试
pytest tests/ -v

# 4. 提交代码
git add .
git commit -m "feat: 实现 XXX 功能"

# 5. 推送代码
git push origin feature/your-feature-name
```

### 3. 代码审查清单

- [ ] 代码符合编码规范
- [ ] 所有公共函数有文档字符串
- [ ] 所有函数有类型注解
- [ ] 复杂逻辑有注释说明
- [ ] 新增功能有单元测试
- [ ] 没有引入未使用的依赖
- [ ] TODO 注释标记待完善功能

## 常用命令

### Python 环境

```bash
# 安装依赖（由用户自己执行，AI 不运行此命令）
pip install -r requirements.txt

# 更新依赖（由用户自己执行，AI 不运行此命令）
pip install --upgrade -r requirements.txt

# 导出依赖（由用户自己执行，AI 不运行此命令）
pip freeze > requirements.txt

# 运行主程序
python -m src.main

# 运行测试
pytest tests/ -v

# 运行测试并生成覆盖率报告
pytest tests/ --cov=src --cov-report=html
```

**注意**: 所有 `pip install` 和 `pip freeze` 命令由用户自己执行，AI 不运行此类命令。

### Streamlit 可视化

```bash
# 启动 Web 界面
streamlit run src/visualization/dashboard.py

# 指定端口
streamlit run src/visualization/dashboard.py --server.port 8501
```

### 代码质量检查

```bash
# 代码格式化（使用 black）
black src/ tests/

# 代码检查（使用 flake8）
flake8 src/ tests/

# 类型检查（使用 mypy）
mypy src/
```

## 模块开发任务

### 待开发功能（高优先级）

1. **collectors/** - 实现 Filebeat 和 Flume 采集器的完整逻辑
2. **parsers/** - 实现正则解析器和 JSON 解析器的字段映射功能
3. **storage/** - 实现 Kafka 客户端的批量发送和消费逻辑
4. **behavior/** - 实现用户画像构建和异常检测算法
5. **ai/** - 集成大模型 API，实现威胁分类和处置建议生成
6. **visualization/** - 完善 Streamlit 界面的各个页面功能
7. **reports/** - 实现日报和周报的自动生成

### 测试任务

1. 为所有采集器编写单元测试
2. 为所有解析器编写单元测试
3. 为行为建模模块编写集成测试
4. 为 AI 分析模块编写 Mock 测试

## 注意事项

1. **编码格式**: 所有文件必须使用 UTF-8 编码
2. **日志输出**: 使用 loguru 统一日志格式，不要使用 print
3. **异常处理**: 所有外部调用必须有异常处理
4. **配置安全**: 敏感信息必须从环境变量读取，不要硬编码
5. **性能优化**: 批量操作优先，避免频繁的数据库查询
6. **文档更新**: 功能完成后及时更新 README 和代码文档

## 参考资料

- [UEBA 技术白皮书](docs/参考资料/UEBA 白皮书.pdf)
- [ClickHouse 官方文档](https://clickhouse.com/docs/)
- [Streamlit 官方文档](https://docs.streamlit.io/)
- [LangChain 官方文档](https://python.langchain.com/docs/)

---

**最后更新**: 2026-04-29
**版本**: 1.1.0
