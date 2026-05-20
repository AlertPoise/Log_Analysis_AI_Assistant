# UEBA 模块总览

## 1. 模块定位

本项目中的 UEBA 模块，全称为：

```text
User and Entity Behavior Analytics
用户与实体行为分析模块
```

当前第一版的目标不是实现完整的实时安全分析系统，而是先实现一个：

```text
离线一次性用户行为基线构建模块
```

也就是说，当前 UEBA 模块只负责：

```text
从结构化日志数据库中读取指定时间范围内的日志聚合结果
    ↓
按用户生成正常行为特征
    ↓
构建用户行为基线
    ↓
将用户行为基线写入数据库
    ↓
供后续异常检测、AI 分析、前端展示调用
```

当前版本的重点是：

```text
能处理十万级日志数据，不被大数据量冲烂。
```

---

## 2. 当前版本只做什么

第一版 UEBA 只做一次性基线构建。

核心功能包括：

```text
1. 从结构化日志表中读取日志统计结果
2. 按用户聚合行为特征
3. 生成用户行为基线
4. 判断基线是否可靠
5. 将基线写入数据库
6. 返回本次基线构建统计信息
```

第一版输出的是用户基线，而不是实时异常检测结果。

---

## 3. 当前版本不做什么

第一版暂时不做以下内容：

```text
不做 Kafka 实时消费
不做实时基线更新
不做滑动窗口增量更新
不做复杂机器学习模型
不做深度用户画像
不做实时告警
不直接 SELECT * 读取全部原始日志
不在 Python 中保存每个用户的完整历史日志列表
```

这些能力可以作为后续扩展方向，但不应该塞进第一版。

---

## 4. UEBA 在项目中的位置

UEBA 位于结构化日志之后，异常分析、AI 分析、可视化之前。

```text
日志采集模块
    ↓
日志解析 / 清洗模块
    ↓
结构化日志表 logs_structured
    ↓
UEBA 用户行为基线构建模块
    ↓
用户行为基线表 user_behavior_baselines
    ↓
异常检测 / AI 分析 / 前端可视化
```

UEBA 不负责采集日志，也不负责解析原始日志。

它的输入应该是已经结构化后的日志数据。

---

## 5. 输入数据

第一版输入来自当前 `logs_structured` 登录 / VPN 行为数据主表。

当前主表字段包括：

```text
timestamp
log_type
username
dept
role
action
event_type
result
fail_reason
source_ip
destination_ip
vpn_gateway
src_country
src_city
protocol
auth_method
client_software
session_id
is_off_hours
is_unusual_ip
session_duration_sec
bytes_sent
bytes_recv
risk_score
risk_tags
raw_message
parser
parse_status
collected_at
```

当前必须明确：

```text
1. logs_structured 是登录 / VPN 行为数据主表。
2. 当前表没有 endpoint 字段，不按 API endpoint 维度设计登录基线。
3. 当前表没有 status 字段，登录结果使用 result，事件类型使用 event_type。
4. 当前表没有 location 字段，来源位置使用 src_country、src_city。
5. is_off_hours、is_unusual_ip 是输入侧已有标签或解析侧特征，只能统计其比例，不能替代 UEBA 自己的基线统计。
6. risk_score、risk_tags 是历史风险参考字段，不作为 UEBA 第一版最终异常结论。
7. raw_message 不作为常规基线聚合维度。
8. parser、parse_status 可用于数据质量过滤或统计，但不是用户行为核心维度。
9. 查询应优先使用 log_type 和 timestamp 时间范围过滤；默认 log_type 可按 vpn 设计，但必须通过配置或参数传入。
```

Repository 层负责把真实字段映射成 UEBA 逻辑字段，不要让其他模块感知数据库字段差异。

---

## 6. 输出数据

第一版输出的是用户行为基线。

每个用户一条基线记录，包含：

```text
用户名
样本数量
基线是否可靠
常用活跃小时
常用来源 IP
常用目标 IP
常用来源国家
常用来源城市
常用 VPN 网关
行为类型分布
事件类型分布
结果分布
失败原因分布
认证方式分布
客户端软件分布
协议分布
失败率
平均每日事件数
活跃日平均事件数
最大单日事件数
基线时间窗口
模型版本
完整 baseline_json
```

示例：

```json
{
  "username": "zhangsan",
  "sample_count": 12000,
  "is_reliable": true,
  "common_active_hours": [
    {"value": 9, "count": 2300, "ratio": 0.191667},
    {"value": 10, "count": 2100, "ratio": 0.175}
  ],
  "common_source_ips": [
    {"value": "10.0.0.1", "count": 8000, "ratio": 0.666667}
  ],
  "common_destination_ips": [
    {"value": "172.16.0.10", "count": 7800, "ratio": 0.65}
  ],
  "common_source_countries": [
    {"value": "中国", "count": 9500, "ratio": 0.791667}
  ],
  "common_source_cities": [
    {"value": "北京", "count": 9500, "ratio": 0.791667}
  ],
  "common_vpn_gateways": [
    {"value": "vpn-gw-01", "count": 9000, "ratio": 0.75}
  ],
  "result_distribution": {
    "SUCCESS": 0.973333,
    "FAIL": 0.026667
  },
  "event_type_distribution": {
    "LOGIN_SUCCESS": 0.973333,
    "LOGIN_FAIL": 0.026667
  },
  "failed_rate": 0.026667,
  "avg_daily_events": 400.0,
  "model_version": "ueba_baseline_v1"
}
```

---

## 7. 核心设计原则

第一版必须遵守以下原则：

```text
1. 数据库侧聚合优先。
2. Python 侧不直接处理完整十万级原始日志。
3. 不保存每个用户的完整日志列表。
4. 每个维度使用独立 SQL 聚合，避免巨型 SQL。
5. 所有 Top-N 特征必须限制数量。
6. 当前登录主表不包含 endpoint，API endpoint 聚合不属于当前第一版核心能力。
7. 配置模块当前可临时存在，但后续要支持外部接口传入配置。
8. 基线复杂字段先用 JSON 字符串存储。
9. 基线写入必须批量插入。
10. Service 层作为 UEBA 对外统一入口。
```

---

## 8. 当前版本一句话总结

当前 UEBA 第一版的目标是：

```text
用数据库侧聚合扛住十万级结构化日志，
用 Python 合并聚合结果并生成用户行为基线，
将基线稳定写入数据库，
为后续异常检测、AI 分析和可视化展示提供可靠数据基础。
```
