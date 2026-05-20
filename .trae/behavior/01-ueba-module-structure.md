# UEBA 模块结构设计

## 1. 推荐目录结构

建议在 behavior 模块目录下实现 UEBA 行为基线能力：

```text
src/behavior/
├── __init__.py
├── config.py
├── schemas.py
├── repository.py
├── aggregate_merger.py
├── baseline_builder.py
├── baseline_store.py
└── service.py
```

可选脚本：

```text
scripts/
└── build_ueba_baseline.py
```

---

## 2. 各模块职责总表

| 文件 | 主要职责 |
|---|---|
| `config.py` | 定义第一版默认配置和配置对象 |
| `schemas.py` | 定义 UEBA 内部稳定数据结构 |
| `repository.py` | 执行数据库侧聚合 SQL，读取聚合结果 |
| `aggregate_merger.py` | 将多个维度的聚合结果按用户合并 |
| `baseline_builder.py` | 根据用户聚合特征生成用户行为基线 |
| `baseline_store.py` | 将用户基线批量写入数据库，并提供查询能力 |
| `service.py` | 对外提供统一调用入口 |
| `scripts/build_ueba_baseline.py` | 命令行触发一次性基线构建 |

---

## 3. 模块之间的调用关系

推荐调用链路：

```text
build_ueba_baseline.py
    ↓
UebaService.build_baseline_once()
    ↓
UebaRepository 执行数据库侧聚合
    ↓
AggregateMerger 合并聚合结果
    ↓
BaselineBuilder 生成用户行为基线
    ↓
BaselineStore 写入基线表
    ↓
返回 BaselineBuildResult
```

---

## 4. `config.py`

### 4.1 模块定位

`config.py` 当前只作为第一版默认配置来源。

必须明确：

```text
config.py 是临时默认配置，不是长期最终配置中心。
```

后期配置应该可以来自：

```text
外部 API 请求
前端页面
数据库配置表
环境变量
命令行参数
```

因此业务逻辑不要直接强依赖全局常量，而应该通过配置对象传入。

不推荐：

```python
from src.behavior.config import TOP_IP_LIMIT
```

推荐：

```python
service = UebaService(
    repository=repository,
    aggregate_merger=aggregate_merger,
    baseline_builder=baseline_builder,
    baseline_store=baseline_store,
    config=config,
)
```

---

### 4.2 推荐配置对象

```python
from dataclasses import dataclass


@dataclass
class UebaBaselineConfig:
    baseline_window_days: int = 30
    min_sample_count: int = 20

    top_source_ip_limit: int = 10
    top_destination_ip_limit: int = 10
    top_source_country_limit: int = 10
    top_source_city_limit: int = 10
    top_vpn_gateway_limit: int = 10
    top_fail_reason_limit: int = 10
    top_client_software_limit: int = 10

    common_hour_min_ratio: float = 0.05
    common_source_ip_min_ratio: float = 0.03
    common_source_city_min_ratio: float = 0.03
    model_version: str = "ueba_baseline_v1"

    write_batch_size: int = 1000
```

---

### 4.3 参数说明

| 参数 | 含义 |
|---|---|
| `baseline_window_days` | 默认基线时间窗口 |
| `min_sample_count` | 用户日志量低于该值时认为基线不可靠 |
| `top_source_ip_limit` | 每个用户最多保存多少个常用来源 IP |
| `top_destination_ip_limit` | 每个用户最多保存多少个常用目标 IP |
| `top_source_country_limit` | 每个用户最多保存多少个常用来源国家 |
| `top_source_city_limit` | 每个用户最多保存多少个常用来源城市 |
| `top_vpn_gateway_limit` | 每个用户最多保存多少个常用 VPN 网关 |
| `top_fail_reason_limit` | 每个用户最多保存多少个失败原因 |
| `top_client_software_limit` | 每个用户最多保存多少个客户端软件 |
| `common_hour_min_ratio` | 某小时占比达到多少才算常用活跃小时 |
| `common_source_ip_min_ratio` | 某来源 IP 占比达到多少才算常用来源 IP |
| `common_source_city_min_ratio` | 某来源城市占比达到多少才算常用来源城市 |
| `model_version` | 当前基线模型版本 |
| `write_batch_size` | 批量写入数据库时的批大小 |

---

## 5. `schemas.py`

### 5.1 模块定位

`schemas.py` 用于定义 UEBA 内部通用数据结构。

推荐数据流：

```text
数据库聚合结果
    ↓
UserAggregateFeature
    ↓
UserBaseline
    ↓
数据库基线表记录
```

---

### 5.2 基础计数项

```python
from dataclasses import dataclass


@dataclass
class CountRatioItem:
    value: str | int
    count: int
    ratio: float
```

适用于：

```text
常用小时
常用来源 IP
常用目标 IP
常用来源国家
常用来源城市
常用 VPN 网关
```

---

### 5.3 用户聚合特征

```python
from dataclasses import dataclass, field
from datetime import datetime


@dataclass
class UserAggregateFeature:
    username: str
    sample_count: int = 0
    failed_count: int = 0
    off_hours_count: int = 0
    unusual_ip_count: int = 0
    active_days: int = 0
    first_seen: datetime | None = None
    last_seen: datetime | None = None

    hour_counts: dict[int, int] = field(default_factory=dict)
    source_ip_counts: dict[str, int] = field(default_factory=dict)
    destination_ip_counts: dict[str, int] = field(default_factory=dict)
    source_country_counts: dict[str, int] = field(default_factory=dict)
    source_city_counts: dict[str, int] = field(default_factory=dict)
    vpn_gateway_counts: dict[str, int] = field(default_factory=dict)
    action_counts: dict[str, int] = field(default_factory=dict)
    event_type_counts: dict[str, int] = field(default_factory=dict)
    result_counts: dict[str, int] = field(default_factory=dict)
    fail_reason_counts: dict[str, int] = field(default_factory=dict)
    auth_method_counts: dict[str, int] = field(default_factory=dict)
    client_software_counts: dict[str, int] = field(default_factory=dict)
    protocol_counts: dict[str, int] = field(default_factory=dict)
    daily_counts: dict[str, int] = field(default_factory=dict)
    session_metric_summary: dict[str, float] = field(default_factory=dict)
    traffic_metric_summary: dict[str, float] = field(default_factory=dict)
```

注意：

```text
这里保存的是聚合计数，不是原始日志列表。
```

---

### 5.4 用户行为基线

```python
from dataclasses import dataclass
from datetime import datetime


@dataclass
class UserBaseline:
    username: str
    sample_count: int
    is_reliable: bool

    common_active_hours: list[CountRatioItem]
    common_source_ips: list[CountRatioItem]
    common_destination_ips: list[CountRatioItem]
    common_source_countries: list[CountRatioItem]
    common_source_cities: list[CountRatioItem]
    common_vpn_gateways: list[CountRatioItem]

    action_distribution: dict[str, float]
    event_type_distribution: dict[str, float]
    result_distribution: dict[str, float]
    fail_reason_distribution: dict[str, float]
    auth_method_distribution: dict[str, float]
    client_software_distribution: dict[str, float]
    protocol_distribution: dict[str, float]

    failed_rate: float
    off_hours_rate: float
    unusual_ip_rate: float
    avg_daily_events: float
    session_duration_avg: float
    session_duration_p50: float
    session_duration_p95: float
    bytes_sent_avg: float
    bytes_recv_avg: float
    active_day_avg_events: float
    max_daily_events: int

    baseline_start_time: datetime
    baseline_end_time: datetime
    model_version: str
```

---

### 5.5 构建结果

```python
from dataclasses import dataclass
from datetime import datetime


@dataclass
class BaselineBuildResult:
    success: bool
    baseline_start_time: datetime
    baseline_end_time: datetime
    total_user_count: int
    reliable_user_count: int
    unreliable_user_count: int
    total_log_count: int
    model_version: str
    duration_seconds: float
    message: str = ""
```

---

## 6. `service.py`

### 6.1 模块定位

`service.py` 是 UEBA 模块唯一推荐对外入口。

其他模块、脚本、前端接口、AI 模块都应该调用：

```python
UebaService
```

而不是直接调用：

```text
repository.py
aggregate_merger.py
baseline_builder.py
baseline_store.py
```

---

### 6.2 推荐 Service 类结构

```python
class UebaService:
    def __init__(
        self,
        repository,
        aggregate_merger,
        baseline_builder,
        baseline_store,
        config,
    ):
        self.repository = repository
        self.aggregate_merger = aggregate_merger
        self.baseline_builder = baseline_builder
        self.baseline_store = baseline_store
        self.config = config
```

---

### 6.3 对外核心方法

```python
def build_baseline_once(self, start_time, end_time) -> BaselineBuildResult:
    ...
```

它负责：

```text
1. 确保基线表存在
2. 执行数据库侧聚合查询
3. 合并聚合结果
4. 生成用户行为基线
5. 批量写入数据库
6. 返回构建统计信息
```
