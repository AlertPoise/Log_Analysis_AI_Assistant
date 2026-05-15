# 快速参考手册

## 常用命令速查

### 环境配置
```bash
# 创建虚拟环境
python -m venv venv

# 激活虚拟环境（Windows）
.\venv\Scripts\activate

# 激活虚拟环境（Linux/Mac）
source venv/bin/activate

# 安装依赖
pip install -r requirements.txt

# 复制环境变量
cp .env.example .env
```

### 运行程序
```bash
# 运行主程序
python -m src.main

# 运行 Streamlit 界面
streamlit run src/visualization/dashboard.py

# 指定端口运行 Streamlit
streamlit run src/visualization/dashboard.py --server.port 8501
```

### 测试命令
```bash
# 运行所有测试
pytest tests/ -v

# 运行特定测试文件
pytest tests/test_parsers.py -v

# 运行特定测试类
pytest tests/test_parsers.py::TestRegexParser -v

# 运行特定测试函数
pytest tests/test_parsers.py::TestRegexParser::test_vpn_login_pattern -v

# 生成覆盖率报告
pytest tests/ --cov=src --cov-report=html

# 查看覆盖率报告
# 打开 htmlcov/index.html
```

### 代码质量
```bash
# 代码格式化
black src/ tests/

# 代码检查
flake8 src/ tests/

# 类型检查
mypy src/

# 检查导入顺序
isort src/ tests/
```

### Git 操作
```bash
# 创建功能分支
git checkout -b feature/功能名

# 查看状态
git status

# 添加文件
git add .

# 提交代码
git commit -m "feat: 实现 XXX 功能"

# 推送代码
git push origin feature/功能名

# 拉取最新代码
git pull origin main

# 查看提交历史
git log --oneline
```

## 模块导入速查

### 基础模块
```python
# 解析器模块（已合并到 parsers.py）
from src.parsers import BaseParser, JSONParser, RegexParser, LogparserParser
from src.parsers import StandardLogSchema, FieldExtractor
from src.parsers import COMMON_PATTERNS, PREDEFINED_PATTERNS, create_parser

# 流式处理
from src.parsers import StreamProcessor, DataCleaner

# 主处理器
from src.parsers import LogProcessor, load_config

# 接口定义
from src.parsers import DataSink, DataSource, StreamConsumer, StreamProducer

# 配置管理
from src.utils.config import settings

# 日志工具
from src.utils.logger import get_logger

# 辅助函数
from src.utils.helpers import generate_id, parse_timestamp
```

### 存储模块
```python
# Kafka 客户端
from src.storage.kafka_client import KafkaClient

# ClickHouse 客户端
from src.storage.clickhouse import ClickHouseClient

# Elasticsearch 客户端
from src.storage.elasticsearch import ElasticsearchClient
```

### 行为建模模块
```python
# 用户画像
from src.behavior.user_profile import UserProfile

# 行为基线
from src.behavior.baseline import BehaviorBaseline

# 异常检测
from src.behavior.anomaly import AnomalyDetector
```

### AI 模块
```python
# AI 分析器
from src.ai.analyzer import AIAnalyzer

# 威胁分类器
from src.ai.threat_classifier import ThreatClassifier

# Prompt 模板
from src.ai.prompt_templates import ANOMALY_ANALYSIS_PROMPT
```

### 报告模块
```python
# 日报生成器
from src.reports.daily_report import DailyReportGenerator

# 周报生成器
from src.reports.weekly_report import WeeklyReportGenerator
```

## 配置项速查

### 环境变量（.env）
```bash
# AI API 配置
OPENAI_API_KEY=your_key
OPENAI_API_BASE_URL=https://api.openai.com/v1
OPENAI_MODEL=gpt-3.5-turbo

DASHSCOPE_API_KEY=your_key
DASHSCOPE_MODEL=qwen-turbo

# Kafka 配置
KAFKA_BOOTSTRAP_SERVERS=localhost:9092
KAFKA_LOGS_TOPIC=logs_raw
KAFKA_ANALYZED_TOPIC=logs_analyzed
KAFKA_CONSUMER_GROUP=log_analysis_group

# ClickHouse 配置
CLICKHOUSE_HOST=localhost
CLICKHOUSE_PORT=8123
CLICKHOUSE_DATABASE=log_analysis
CLICKHOUSE_USER=default
CLICKHOUSE_PASSWORD=

# Elasticsearch 配置（可选）
ES_HOST=localhost
ES_PORT=9200
ES_INDEX=logs-*
ES_USER=
ES_PASSWORD=

# 应用配置
LOG_LEVEL=INFO
LOG_OUTPUT_DIR=logs
REPORT_OUTPUT_DIR=reports
DATA_RETENTION_DAYS=90

# 行为分析配置
BEHAVIOR_TIME_WINDOW_HOURS=24
ANOMALY_THRESHOLD=0.7
MIN_SAMPLES_FOR_PROFILE=10

# Streamlit 配置
STREAMLIT_SERVER_PORT=8501
STREAMLIT_SERVER_ADDRESS=localhost
```

## 日志记录速查

```python
from src.utils.logger import get_logger

logger = get_logger(__name__)

# 不同级别的日志
logger.debug("调试信息")
logger.info("普通信息")
logger.warning("警告信息")
logger.error("错误信息")
logger.critical("严重错误")

# 带参数的日志
logger.info("用户 {} 登录成功", username)
logger.error("处理失败：{}", error_message)

# 带异常堆栈的日志
try:
    # 可能出错的代码
    pass
except Exception as e:
    logger.exception("处理过程中发生异常：{}", str(e))
```

## 数据类型速查

### 日志数据结构（标准格式）
```python
# JSON 解析器输出示例
log_record = {
    "timestamp": "2024-01-01T10:00:00Z",
    "username": "admin",
    "action": "login",
    "source_ip": "192.168.1.1",
    "status": "success",
    "raw_log": '{"timestamp": "2024-01-01T10:00:00Z", "username": "admin", ...}',
    "parser": "json_test",
    "parse_status": "success"
}

# RegexParser 输出示例（VPN 日志）
vpn_log_record = {
    "timestamp": "2024-01-01 10:00:00",
    "log_type": "vpn",
    "username": "admin",
    "action": "LOGIN",
    "source_ip": "192.168.1.1",
    "status_code": "SUCCESS",
    "parse_status": "success",
    "parsed_at": "2026-04-27 18:37:35.264502",
    "raw_log": "2024-01-01 10:00:00 LOGIN user=admin ip=192.168.1.1 status=SUCCESS",
    "parser": "regex_VPN 登录日志"
}

# RegexParser 输出示例（Nginx 日志）
nginx_log_record = {
    "timestamp": "2024-01-01 12:00:00+08:00",
    "log_type": "network",
    "username": "unknown",
    "action": "UNKNOWN",
    "source_ip": "192.168.1.100",
    "uri": "/api/users",
    "method": "GET",
    "status_code": "200",
    "parse_status": "success",
    "parsed_at": "2026-04-27 18:37:35.268948",
    "raw_log": '192.168.1.100 - - [01/Jan/2024:12:00:00 +0800] "GET /api/users HTTP/1.1" 200 1234 "-" "Mozilla/5.0"',
    "parser": "regex_nginx_access"
}

# RegexParser 输出示例（API 日志）
api_log_record = {
    "timestamp": "2024-01-01 12:00:00",
    "log_type": "api",
    "username": "admin",
    "action": "UNKNOWN",
    "source_ip": "0.0.0.0",
    "uri": "/api/v1/users",
    "method": "GET",
    "status_code": "200",
    "response_time": "45.5",
    "parse_status": "success",
    "parsed_at": "2026-04-27 18:37:35.270838",
    "raw_log": "2024-01-01T12:00:00Z GET /api/v1/users user=admin status=200 response_time=45.5ms",
    "parser": "regex_api_call"
}
```

### 数据清洗后结构
```python
# DataCleaner 输出示例
cleaned_record = {
    "username": "admin",
    "source_ip": "192.168.1.1",
    "action": "LOGIN",
    "severity_level": "INFO"
}

# LogProcessor 完整流程输出（解析 + 清洗）
processed_record = {
    "user": "admin",
    "ip": "192.168.1.1",
    "timestamp": "2024-01-01T10:00:00Z",
    "action": "login",
    "raw_log": '{"timestamp": "2024-01-01T10:00:00Z", ...}',
    "parser": "json",
    "parse_status": "success",
    "severity_level": "INFO"
}
```

### 用户画像结构
```python
user_profile = {
    "username": "zhangsan",
    "created_at": "2024-01-01 00:00:00",
    "updated_at": "2024-01-01 12:00:00",
    "login_times": ["09:00", "14:00", "18:00"],
    "common_ips": ["192.168.1.100", "192.168.1.101"],
    "api_call_frequency": 10.5,
    "activity_hours": {9: 10, 10: 15, 14: 20},
    "common_locations": ["北京", "上海"]
}
```

### 异常检测结果结构
```python
anomaly_result = {
    "anomaly_id": "unique_id",
    "username": "zhangsan",
    "timestamp": "2024-01-01 03:00:00",
    "anomaly_type": "UNUSUAL_TIME",
    "anomaly_score": 0.85,
    "risk_level": "HIGH",
    "description": "凌晨 3 点在异地 IP 登录",
    "source_ip": "10.0.0.100",
    "location": "广州",
    "related_logs": ["log_id_1", "log_id_2"]
}
```

### AI 分析结果结构
```python
ai_analysis = {
    "threat_type": "ACCOUNT_TAKEOVER",
    "threat_name": "账号接管",
    "risk_level": "HIGH",
    "confidence": 0.92,
    "description": "检测到异常的登录行为，可能是账号被盗用",
    "suggestion": "建议立即冻结账号并联系用户确认",
    "immediate_actions": [
        "冻结账号",
        "通知用户"
    ],
    "follow_up_actions": [
        "调查登录来源",
        "检查账号操作记录"
    ]
}
```

## 数据库操作速查

### ClickHouse 查询示例
```python
from src.storage.clickhouse import ClickHouseClient

client = ClickHouseClient(config)
client.connect()

# 查询日志
logs = client.query_logs(
    table="vpn_logs",
    conditions={
        "username": "zhangsan",
        "start_time": "2024-01-01 00:00:00",
        "end_time": "2024-01-01 23:59:59"
    }
)

# 聚合查询
result = client.aggregate(
    table="vpn_logs",
    metrics=["count() as total", "uniq(username) as users"],
    group_by=["toDate(timestamp)"]
)

client.close()
```

### Kafka 发送示例
```python
from src.storage.kafka_client import KafkaClient

client = KafkaClient(config)

# 发送单条消息
client.send_message(
    topic="logs_raw",
    message={"log_type": "vpn_login", "username": "zhangsan"}
)

# 批量发送
messages = [
    {"log_type": "vpn_login", "username": "zhangsan"},
    {"log_type": "vpn_login", "username": "lisi"}
]
client.send_batch(topic="logs_raw", messages=messages)
```

## 常见错误处理

### 数据库连接失败
```python
try:
    client.connect()
except Exception as e:
    logger.error("数据库连接失败：{}", str(e))
    # 重试逻辑或降级处理
```

### API 调用失败
```python
try:
    result = ai_analyzer.analyze_anomaly(context)
except Exception as e:
    logger.error("AI 分析失败：{}", str(e))
    # 返回默认结果或使用备用 API
    result = {
        "threat_type": "UNKNOWN",
        "risk_level": "MEDIUM",
        "description": "分析失败，请人工审查"
    }
```

### 日志解析失败
```python
parsed = parser.parse(raw_log)
if not parsed:
    logger.warning("日志解析失败：{}", raw_log[:100])
    # 使用备用解析器或记录原始日志
```

## 调试技巧

### 使用 Python Debugger
```python
import pdb

# 设置断点
pdb.set_trace()

# 或使用 breakpoint() (Python 3.7+)
breakpoint()

# 调试命令:
# n - 执行下一行
# c - 继续执行
# s - 进入函数
# r - 返回
# q - 退出调试
# p variable - 打印变量值
```

### 使用日志调试
```python
# 设置调试级别日志
logger.debug("变量值：username={}, ip={}", username, ip)

# 打印数据结构
logger.debug("完整数据：{}", json.dumps(data, ensure_ascii=False, indent=2))
```

## 性能优化技巧

### 批量操作
```python
# ✅ 好的做法 - 批量插入
logs_batch = []
for log in logs:
    logs_batch.append(log)
    if len(logs_batch) >= 1000:
        client.insert_logs("logs_table", logs_batch)
        logs_batch = []

# ❌ 差的做法 - 单条插入
for log in logs:
    client.insert_log("logs_table", log)
```

### 使用连接池
```python
# 复用数据库连接
class DatabasePool:
    def __init__(self, size=10):
        self.pool = [create_connection() for _ in range(size)]
    
    def get_connection(self):
        return self.pool.pop()
    
    def return_connection(self, conn):
        self.pool.append(conn)
```

## 快速故障排查

### 问题：程序启动失败
```bash
# 检查 Python 版本
python --version  # 需要 3.9+

# 检查依赖是否安装
pip list | grep -E "streamlit|kafka|clickhouse"

# 检查环境变量
cat .env

# 查看详细错误日志
python -m src.main 2>&1 | tee debug.log
```

### 问题：Kafka 连接失败
```bash
# 检查 Kafka 是否运行
# Windows: 查看服务
# Linux: systemctl status kafka

# 测试 Kafka 连接
telnet localhost 9092

# 检查配置
cat .env | grep KAFKA
```

### 问题：Streamlit 无法访问
```bash
# 检查端口是否被占用
netstat -ano | findstr :8501

# 使用其他端口
streamlit run dashboard.py --server.port 8502

# 检查防火墙设置
```

---

**提示**: 将此文件放在手边，开发时快速查阅！

**更新日期**: 2026-04-29
**当前状态**: 数据存储模块（Kafka + ClickHouse）测试通过
