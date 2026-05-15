# AI 助手开发指南

## 项目理解

这是一个**日志分析 AI 助手**项目，使用 UEBA（用户实体行为分析）技术来检测安全威胁。

### 核心功能

1. **日志采集** → 从 Filebeat/Flume 采集各类系统日志
2. **日志解析** → 使用正则/JSON 解析器标准化日志格式
3. **数据存储** → Kafka 缓冲 + ClickHouse 存储
4. **行为建模** → 构建用户画像，计算行为基线
5. **异常检测** → 检测偏离基线的异常行为
6. **AI 分析** → 使用大模型分析异常，生成处置建议
7. **可视化** → Streamlit Web 界面展示分析结果

### 技术架构

```
日志源 → Filebeat/Flume → Kafka → 解析器 → ClickHouse
                                              ↓
                                        用户行为建模
                                              ↓
                                          AI 分析
                                              ↓
                                      Streamlit 展示
                                              ↓
                                        自动生成报告
```

## 开发优先级

### P0 - 核心功能（必须实现）

1. ✅ **项目框架** - 已完成模板状态
2. 🔲 **日志解析器** - 实现正则和 JSON 解析器的完整逻辑
3. ✅ **Kafka 客户端** - 实现消息的发送和消费（测试通过）
4. ✅ **ClickHouse 客户端** - 实现数据插入和查询（测试通过）
5. 🔲 **用户画像** - 实现用户行为特征提取
6. 🔲 **异常检测** - 实现基于统计的异常检测
7. 🔲 **AI 分析器** - 集成大模型 API

### P1 - 增强功能

1. ✅ **Streamlit 界面** - 完善各个页面的展示逻辑（已完成）
2. 🔲 **报告生成** - 实现日报和周报自动生成
3. 🔲 **威胁分类** - 完善威胁类型体系
4. 🔲 **行为基线** - 实现动态基线更新

### P2 - 优化功能

1. 🔲 **Elasticsearch 集成** - 可选的日志检索功能
2. 🔲 **性能优化** - 批量处理、缓存优化
3. 🔲 **监控告警** - 实时监控和告警通知

## 代码开发指南

### 1. 新增功能时的步骤

```python
# 1. 在对应模块创建新文件
# 例如：src/behavior/new_feature.py

"""
新功能模块
TODO: 实现 XXX 功能
"""
from typing import Any, Dict, List
from ..utils.logger import get_logger

logger = get_logger(__name__)


class NewFeature:
    """新功能类"""
    
    def __init__(self, config: Dict[str, Any]):
        """初始化"""
        self.config = config
        
    def process(self, data: Any) -> Any:
        """
        处理逻辑
        
        Args:
            data: 输入数据
            
        Returns:
            处理结果
        """
        # TODO: 实现具体逻辑
        pass
```

### 2. 实现 TODO 功能

查找所有 TODO 注释：
```bash
# 使用 grep 查找 TODO
grep -r "TODO" src/ --include="*.py"
```

按照 TODO 中的开发任务说明逐步实现功能。

### 3. 编写测试

```python
# tests/test_new_feature.py
"""
测试新功能
"""
import pytest
from src.behavior.new_feature import NewFeature


class TestNewFeature:
    """测试新功能类"""
    
    def test_process(self):
        """测试处理逻辑"""
        feature = NewFeature(config={})
        result = feature.process(test_data)
        assert result is not None
```

### 4. 使用已有工具

**日志记录**：
```python
from ..utils.logger import get_logger

logger = get_logger(__name__)
logger.info("操作成功")
logger.error("发生错误：{}", error_message)
```

**配置读取**：
```python
from ..utils.config import settings

kafka_servers = settings.kafka_bootstrap_servers
model_name = settings.openai_model
```

**辅助函数**：
```python
from ..utils.helpers import generate_id, parse_timestamp

user_id = generate_id(username)
timestamp = parse_timestamp("2024-01-01 12:00:00")
```

## 常见问题

### Q1: 如何开始开发？

1. 阅读 `DEVELOPMENT_TASKS.md` 了解任务分配
2. 选择要开发的模块
3. 查看该模块的 TODO 注释
4. 实现功能并编写测试
5. 运行测试验证

### Q2: 如何调试代码？

```python
# 使用 loguru 调试
logger.debug("调试信息：{}", variable)
logger.info("状态：{}", status)

# 或使用 Python debugger
import pdb; pdb.set_trace()
```

### Q3: 如何测试 AI 功能？

使用 Mock 避免真实 API 调用：

```python
from unittest.mock import Mock, patch

@patch('src.ai.analyzer.AIAnalyzer.analyze_anomaly')
def test_ai_analysis(mock_analyze):
    mock_analyze.return_value = {
        'threat_type': 'ACCOUNT_TAKEOVER',
        'risk_level': 'HIGH'
    }
    # 测试逻辑...
```

### Q4: 数据库连接失败？

检查 `.env` 配置：
```bash
CLICKHOUSE_HOST=localhost
CLICKHOUSE_PORT=8123
CLICKHOUSE_DATABASE=log_analysis
```

使用 ClickHouse 客户端测试连接：
```python
from src.storage.clickhouse import ClickHouseClient

client = ClickHouseClient(config)
client.connect()  # 测试连接
```

## 开发技巧

### 1. 使用类型注解

```python
# ✅ 好的做法
def process_logs(logs: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    return processed_logs

# ❌ 避免
def process_logs(logs):
    return logs
```

### 2. 异常处理

```python
# ✅ 好的做法
try:
    result = self.client.query(sql)
except Exception as e:
    logger.error(f"查询失败：{e}")
    raise

# ❌ 避免
try:
    result = self.client.query(sql)
except:
    pass
```

### 3. 批量操作

```python
# ✅ 批量插入（高效）
def insert_batch(self, logs: List[Dict]):
    self.client.insert_many(logs)

# ❌ 单条插入（低效）
def insert_one(self, log: Dict):
    self.client.insert(log)
```

## 快速参考

### 模块导入路径

```python
# 采集器
from src.collectors.base import BaseCollector
from src.collectors.filebeat import FilebeatCollector

# 解析器
from src.parsers.base import BaseParser
from src.parsers.regex_parser import RegexParser

# 存储
from src.storage.kafka_client import KafkaClient
from src.storage.clickhouse import ClickHouseClient

# 行为建模
from src.behavior.user_profile import UserProfile
from src.behavior.anomaly import AnomalyDetector

# AI
from src.ai.analyzer import AIAnalyzer
from src.ai.threat_classifier import ThreatClassifier

# 工具
from src.utils.config import settings
from src.utils.logger import get_logger
from src.utils.helpers import generate_id, parse_timestamp
```

### 配置文件位置

- **环境变量**: `.env`
- **日志源配置**: `config/log_sources.yml`
- **Filebeat 配置**: `config/filebeat.yml`
- **数据库表结构**: `config/clickhouse.sql`

## 下一步行动

1. **选择任务**: 查看 `DEVELOPMENT_TASKS.md`
2. **实现功能**: 按照 TODO 注释开发
3. **编写测试**: 保证功能正确
4. **提交代码**: 使用 Git 提交

---

**提示**: 遇到问题时，先查看项目文档和已有代码，避免重复造轮子。
