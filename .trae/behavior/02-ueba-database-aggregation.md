# UEBA 数据库侧聚合设计

## 1. 为什么要数据库侧聚合

当前 UEBA 第一版要求能处理至少十万级日志数据。

因此不能采用：

```python
logs = db.query("SELECT * FROM logs_structured")
```

也不应该把十万级原始日志全部读到 Python 中循环处理。

正确方式是：

```text
数据库负责大规模扫描、过滤、分组、计数
Python 只负责处理聚合后的结果
```

推荐链路：

```text
logs_structured 原始结构化登录日志
    ↓
ClickHouse GROUP BY 聚合
    ↓
Python 获取聚合结果
    ↓
按 username 合并
    ↓
生成用户行为基线
```

---

## 2. 当前 logs_structured 登录数据主表

当前阶段以用户新提供的 `logs_structured` 登录 / VPN 行为数据主表为准，并结合 PR #23 后更新的 `config/clickhouse.sql`、Parser / storage 当前真实 schema 共同校验。

字段如下：

```text
timestamp DateTime64(3)       日志生成时间
log_type String               日志类型，当前默认可按 vpn 设计，但必须通过配置或参数传入
action String                 行为动作，例如 LOGIN
event_type String             事件类型，例如 LOGIN_SUCCESS / LOGIN_FAIL
result String                 登录结果，例如 SUCCESS / FAIL
username String               用户名
dept String                   部门
role String                   角色
fail_reason String            失败原因
source_ip String              来源 IP
destination_ip String         目标 IP
vpn_gateway String            VPN 网关
src_country String            来源国家
src_city String               来源城市
protocol String               协议
auth_method String            认证方式
client_software String        客户端软件
session_id String             会话 ID
is_off_hours Bool             输入侧已有非工作时间标签
is_unusual_ip Bool            输入侧已有异常 IP 标签
session_duration_sec Int32    会话时长
bytes_sent UInt64             发送字节数
bytes_recv UInt64             接收字节数
risk_score UInt8              历史风险评分参考
risk_tags String              历史风险标签参考
raw_log Nullable(String)      结构化表中的原始日志文本
parser String                 解析器名称
parse_status String           解析状态
collected_at DateTime64(3)    采集入库时间
```

必须明确：

```text
1. 当前表是登录 / VPN 行为数据主表。
2. 当前表没有 endpoint 字段，不能按 API endpoint 维度设计登录基线。
3. 当前结构化表没有旧登录结果字段 status，登录结果字段是 result，事件字段是 event_type。
4. status_code 可以作为通用扩展字段存在，但不等同于 UEBA 登录结果 status。
5. 当前结构化表保留 location Nullable(String) 作为通用扩展字段，但 location 不是 UEBA v1 来源位置字段，来源位置应使用 src_country、src_city。
6. is_off_hours、is_unusual_ip 是输入侧已有标签或解析侧特征，不能替代 UEBA 自己的基线统计。
7. risk_score、risk_tags 是已有风险字段，只能作为历史风险参考，不作为 UEBA 第一版最终异常结论。
8. logs_raw Kafka 原始表中可以存在 raw_message String；logs_structured 结构化表当前实际字段是 raw_log Nullable(String)，不包含 raw_message。
9. raw_message / raw_log 只作为原始日志文本或测试数据标记字段，不作为 UEBA v1 核心聚合维度。
10. parser、parse_status 可用于数据质量过滤或统计，但不是用户行为核心维度。
11. 当前排序键是 (log_type, timestamp)，聚合查询应优先使用 log_type 和时间范围过滤。
12. Repository 不得按 endpoint / status / location 旧字段设计登录 / VPN 核心基线。
```

如果后续接入 API 日志，API endpoint 相关聚合应作为另一类 `log_type` 或另一张表的扩展，不属于当前登录主表第一版核心字段。

---

## 2.1 schema 来源交叉校验规则

feature PR #23 后，`config/clickhouse.sql` 已可作为 `logs_structured` 通用字段参考之一，不再写成“绝对禁止查看”。

但禁止仅凭 `config/clickhouse.sql` 单独反向设计 UEBA；写 `repository.py` 查询前必须交叉检查：

```text
config/clickhouse.sql
src/storage/clickhouse.py
src/utils/config.py
docs/dashboard_continuous统一环境配置文档.md
Parser / storage 当前真实 schema
当前 .trae/behavior 设计
```

如果这些来源之间字段名、表名、默认数据库或连接方式冲突，必须先报告冲突并请求确认，不要静默修改 UEBA 字段映射。

---

## 2.2 阶段 15 字段事实固化

阶段 15 最终验收时，字段事实固化如下：

```text
1. logs_raw Kafka 表中存在 raw_message。
2. logs_structured 表中实际使用 raw_log。
3. raw_message / raw_log 不作为 UEBA v1 核心聚合维度。
4. logs_structured 可以保留 location 作为通用扩展字段。
5. UEBA v1 来源位置使用 src_country / src_city。
6. 登录结果使用 result。
7. 事件类型使用 event_type。
8. 不使用独立旧字段 status 作为 UEBA 登录结果字段。
9. status_code 可以作为通用字段存在，但不是 UEBA 登录结果字段。
10. 不使用 endpoint 作为 UEBA v1 核心字段。
11. result = 'FAILED' 与 result = 'FAIL' 都纳入失败统计。
```

当前 `failed_count` 统一口径为：

```sql
countIf(result IN ('FAILED', 'FAIL') OR event_type = 'LOGIN_FAIL') AS failed_count
```

## 3. Repository 逻辑字段映射

当前登录表建议在 Repository 层映射为以下 UEBA 逻辑字段：

```text
event_time ← timestamp
log_type ← log_type
username ← username
dept ← dept
role ← role
source_ip ← source_ip
destination_ip ← destination_ip
source_country ← src_country
source_city ← src_city
action ← action
event_type ← event_type
result ← result
fail_reason ← fail_reason
vpn_gateway ← vpn_gateway
protocol ← protocol
auth_method ← auth_method
client_software ← client_software
session_id ← session_id
session_duration_sec ← session_duration_sec
bytes_sent ← bytes_sent
bytes_recv ← bytes_recv
is_off_hours ← is_off_hours
is_unusual_ip ← is_unusual_ip
parser ← parser
parse_status ← parse_status
collected_at ← collected_at
```

字段名差异只允许在 `repository.py` 中适配，不要让上层 Merger、Builder、Store 感知数据库字段差异。

---

## 4. 统一过滤与参数化查询

所有聚合 SQL 必须使用相同的时间窗口和日志类型过滤。

逻辑表达建议：

```sql
PREWHERE log_type = {log_type:String}
    AND timestamp >= {start_time:DateTime64(3)}
    AND timestamp < {end_time:DateTime64(3)}
WHERE username != ''
```

要求：

```text
1. 必须有 start_time 和 end_time
2. 必须有 log_type，当前默认可为 vpn，但必须通过配置或参数传入，不能永久写死
3. 必须过滤空 username
4. 必须使用参数化查询
5. 不允许直接拼接用户输入
```

参数占位符说明：

```text
1. 文档中的 {start_time:DateTime64(3)}、{end_time:DateTime64(3)}、{log_type:String}、{limit:UInt32} 只表示逻辑参数
2. 实际代码实现时，必须以项目现有 ClickHouse 封装为准
3. 当前项目使用 clickhouse_connect / ClickHouseClient 的 client.query(query, parameters=params) 参数化查询方式时，应采用该客户端支持的参数写法
4. 禁止通过字符串拼接方式把 start_time、end_time、log_type、limit 等用户输入直接拼进 SQL
5. Repository 层负责封装 SQL 参数，不允许上层模块拼 SQL
```

---

## 5. 用户总览统计

### 5.1 用途

生成每个用户的基础统计骨架。

包括：

```text
总日志数
首次出现时间
最后出现时间
失败数量
活跃天数
非工作时间数量
异常 IP 标签数量
```

### 5.2 SQL

```sql
SELECT
    username,
    count() AS sample_count,
    min(timestamp) AS first_seen,
    max(timestamp) AS last_seen,
    countIf(result IN ('FAILED', 'FAIL') OR event_type = 'LOGIN_FAIL') AS failed_count,
    uniqExact(toDate(timestamp)) AS active_days,
    countIf(is_off_hours) AS off_hours_count,
    countIf(is_unusual_ip) AS unusual_ip_count
FROM logs_structured
PREWHERE log_type = {log_type:String}
    AND timestamp >= {start_time:DateTime64(3)}
    AND timestamp < {end_time:DateTime64(3)}
WHERE username != ''
GROUP BY username;
```

### 5.3 Repository 接口

```python
def fetch_user_summary(self, start_time, end_time, log_type: str = "vpn") -> list[dict]:
    ...
```

---

## 6. 用户小时分布统计

```sql
SELECT
    username,
    toHour(timestamp) AS active_hour,
    count() AS cnt
FROM logs_structured
PREWHERE log_type = {log_type:String}
    AND timestamp >= {start_time:DateTime64(3)}
    AND timestamp < {end_time:DateTime64(3)}
WHERE username != ''
GROUP BY
    username,
    active_hour
ORDER BY
    username,
    active_hour;
```

```python
def fetch_hour_distribution(self, start_time, end_time, log_type: str = "vpn") -> list[dict]:
    ...
```

---

## 7. 用户常用来源 IP Top-N

```sql
SELECT
    username,
    source_ip,
    cnt
FROM
(
    SELECT
        username,
        source_ip,
        count() AS cnt
    FROM logs_structured
    PREWHERE log_type = {log_type:String}
        AND timestamp >= {start_time:DateTime64(3)}
        AND timestamp < {end_time:DateTime64(3)}
    WHERE username != ''
      AND source_ip != ''
    GROUP BY
        username,
        source_ip
    ORDER BY
        username ASC,
        cnt DESC
)
LIMIT {limit:UInt32} BY username;
```

```python
def fetch_top_source_ips(self, start_time, end_time, limit: int, log_type: str = "vpn") -> list[dict]:
    ...
```

---

## 8. 用户常用目标 IP Top-N

```sql
SELECT
    username,
    destination_ip,
    cnt
FROM
(
    SELECT
        username,
        destination_ip,
        count() AS cnt
    FROM logs_structured
    PREWHERE log_type = {log_type:String}
        AND timestamp >= {start_time:DateTime64(3)}
        AND timestamp < {end_time:DateTime64(3)}
    WHERE username != ''
      AND destination_ip != ''
    GROUP BY
        username,
        destination_ip
    ORDER BY
        username ASC,
        cnt DESC
)
LIMIT {limit:UInt32} BY username;
```

```python
def fetch_top_destination_ips(self, start_time, end_time, limit: int, log_type: str = "vpn") -> list[dict]:
    ...
```

---

## 9. 用户常用来源国家 Top-N

```sql
SELECT
    username,
    src_country AS source_country,
    cnt
FROM
(
    SELECT
        username,
        src_country,
        count() AS cnt
    FROM logs_structured
    PREWHERE log_type = {log_type:String}
        AND timestamp >= {start_time:DateTime64(3)}
        AND timestamp < {end_time:DateTime64(3)}
    WHERE username != ''
      AND src_country != ''
    GROUP BY
        username,
        src_country
    ORDER BY
        username ASC,
        cnt DESC
)
LIMIT {limit:UInt32} BY username;
```

```python
def fetch_top_source_countries(self, start_time, end_time, limit: int, log_type: str = "vpn") -> list[dict]:
    ...
```

---

## 10. 用户常用来源城市 Top-N

```sql
SELECT
    username,
    src_city AS source_city,
    cnt
FROM
(
    SELECT
        username,
        src_city,
        count() AS cnt
    FROM logs_structured
    PREWHERE log_type = {log_type:String}
        AND timestamp >= {start_time:DateTime64(3)}
        AND timestamp < {end_time:DateTime64(3)}
    WHERE username != ''
      AND src_city != ''
    GROUP BY
        username,
        src_city
    ORDER BY
        username ASC,
        cnt DESC
)
LIMIT {limit:UInt32} BY username;
```

```python
def fetch_top_source_cities(self, start_time, end_time, limit: int, log_type: str = "vpn") -> list[dict]:
    ...
```

---

## 11. 用户常用 VPN 网关 Top-N

```sql
SELECT
    username,
    vpn_gateway,
    cnt
FROM
(
    SELECT
        username,
        vpn_gateway,
        count() AS cnt
    FROM logs_structured
    PREWHERE log_type = {log_type:String}
        AND timestamp >= {start_time:DateTime64(3)}
        AND timestamp < {end_time:DateTime64(3)}
    WHERE username != ''
      AND vpn_gateway != ''
    GROUP BY
        username,
        vpn_gateway
    ORDER BY
        username ASC,
        cnt DESC
)
LIMIT {limit:UInt32} BY username;
```

```python
def fetch_top_vpn_gateways(self, start_time, end_time, limit: int, log_type: str = "vpn") -> list[dict]:
    ...
```

---

## 12. 行为、事件、结果与失败原因分布

### 12.1 action 分布

```sql
SELECT username, action, count() AS cnt
FROM logs_structured
PREWHERE log_type = {log_type:String}
    AND timestamp >= {start_time:DateTime64(3)}
    AND timestamp < {end_time:DateTime64(3)}
WHERE username != '' AND action != ''
GROUP BY username, action
ORDER BY username ASC, cnt DESC;
```

```python
def fetch_action_distribution(self, start_time, end_time, log_type: str = "vpn") -> list[dict]:
    ...
```

### 12.2 event_type 分布

```sql
SELECT username, event_type, count() AS cnt
FROM logs_structured
PREWHERE log_type = {log_type:String}
    AND timestamp >= {start_time:DateTime64(3)}
    AND timestamp < {end_time:DateTime64(3)}
WHERE username != '' AND event_type != ''
GROUP BY username, event_type
ORDER BY username ASC, cnt DESC;
```

```python
def fetch_event_type_distribution(self, start_time, end_time, log_type: str = "vpn") -> list[dict]:
    ...
```

### 12.3 result 分布

```sql
SELECT username, result, count() AS cnt
FROM logs_structured
PREWHERE log_type = {log_type:String}
    AND timestamp >= {start_time:DateTime64(3)}
    AND timestamp < {end_time:DateTime64(3)}
WHERE username != '' AND result != ''
GROUP BY username, result
ORDER BY username ASC, cnt DESC;
```

```python
def fetch_result_distribution(self, start_time, end_time, log_type: str = "vpn") -> list[dict]:
    ...
```

### 12.4 fail_reason Top-N

```sql
SELECT
    username,
    fail_reason,
    cnt
FROM
(
    SELECT
        username,
        fail_reason,
        count() AS cnt
    FROM logs_structured
    PREWHERE log_type = {log_type:String}
        AND timestamp >= {start_time:DateTime64(3)}
        AND timestamp < {end_time:DateTime64(3)}
    WHERE username != ''
      AND fail_reason != ''
    GROUP BY username, fail_reason
    ORDER BY username ASC, cnt DESC
)
LIMIT {limit:UInt32} BY username;
```

```python
def fetch_fail_reason_distribution(self, start_time, end_time, limit: int, log_type: str = "vpn") -> list[dict]:
    ...
```

---

## 13. 认证方式、客户端软件、协议分布

```python
def fetch_auth_method_distribution(self, start_time, end_time, log_type: str = "vpn") -> list[dict]:
    ...

def fetch_client_software_distribution(self, start_time, end_time, limit: int, log_type: str = "vpn") -> list[dict]:
    ...

def fetch_protocol_distribution(self, start_time, end_time, log_type: str = "vpn") -> list[dict]:
    ...
```

对应 SQL 应分别基于 `auth_method`、`client_software`、`protocol` 做用户维度聚合；`client_software` 可按 Top-N 限制数量。

---

## 14. 用户每日事件数

```sql
SELECT
    username,
    toDate(timestamp) AS event_date,
    count() AS cnt
FROM logs_structured
PREWHERE log_type = {log_type:String}
    AND timestamp >= {start_time:DateTime64(3)}
    AND timestamp < {end_time:DateTime64(3)}
WHERE username != ''
GROUP BY
    username,
    event_date
ORDER BY
    username ASC,
    event_date ASC;
```

```python
def fetch_daily_event_counts(self, start_time, end_time, log_type: str = "vpn") -> list[dict]:
    ...
```

---

## 15. 会话时长与流量统计

```sql
SELECT
    username,
    avg(session_duration_sec) AS session_duration_avg,
    max(session_duration_sec) AS session_duration_max,
    quantileExact(0.5)(session_duration_sec) AS session_duration_p50,
    quantileExact(0.95)(session_duration_sec) AS session_duration_p95,
    avg(bytes_sent) AS bytes_sent_avg,
    avg(bytes_recv) AS bytes_recv_avg,
    max(bytes_sent) AS bytes_sent_max,
    max(bytes_recv) AS bytes_recv_max
FROM logs_structured
PREWHERE log_type = {log_type:String}
    AND timestamp >= {start_time:DateTime64(3)}
    AND timestamp < {end_time:DateTime64(3)}
WHERE username != ''
GROUP BY username;
```

```python
def fetch_session_metric_summary(self, start_time, end_time, log_type: str = "vpn") -> list[dict]:
    ...
```

返回值可拆分到 `session_metric_summary` 和 `traffic_metric_summary`。

---

## 16. Repository 类建议

```python
class UebaRepository:
    def fetch_user_summary(self, start_time, end_time, log_type: str = "vpn") -> list[dict]:
        ...

    def fetch_hour_distribution(self, start_time, end_time, log_type: str = "vpn") -> list[dict]:
        ...

    def fetch_top_source_ips(self, start_time, end_time, limit: int, log_type: str = "vpn") -> list[dict]:
        ...

    def fetch_top_destination_ips(self, start_time, end_time, limit: int, log_type: str = "vpn") -> list[dict]:
        ...

    def fetch_top_source_countries(self, start_time, end_time, limit: int, log_type: str = "vpn") -> list[dict]:
        ...

    def fetch_top_source_cities(self, start_time, end_time, limit: int, log_type: str = "vpn") -> list[dict]:
        ...

    def fetch_top_vpn_gateways(self, start_time, end_time, limit: int, log_type: str = "vpn") -> list[dict]:
        ...

    def fetch_action_distribution(self, start_time, end_time, log_type: str = "vpn") -> list[dict]:
        ...

    def fetch_event_type_distribution(self, start_time, end_time, log_type: str = "vpn") -> list[dict]:
        ...

    def fetch_result_distribution(self, start_time, end_time, log_type: str = "vpn") -> list[dict]:
        ...

    def fetch_fail_reason_distribution(self, start_time, end_time, limit: int, log_type: str = "vpn") -> list[dict]:
        ...

    def fetch_auth_method_distribution(self, start_time, end_time, log_type: str = "vpn") -> list[dict]:
        ...

    def fetch_client_software_distribution(self, start_time, end_time, limit: int, log_type: str = "vpn") -> list[dict]:
        ...

    def fetch_protocol_distribution(self, start_time, end_time, log_type: str = "vpn") -> list[dict]:
        ...

    def fetch_daily_event_counts(self, start_time, end_time, log_type: str = "vpn") -> list[dict]:
        ...

    def fetch_session_metric_summary(self, start_time, end_time, log_type: str = "vpn") -> list[dict]:
        ...
```

---

## 17. 聚合方案优势

使用数据库侧聚合后，Python 处理的数据规模从：

```text
十万级原始日志
```

变成：

```text
用户数 × 每个维度的聚合结果
```

并且这些聚合行都很短，不包含完整日志正文。

随着日志从十万增长到百万，Python 侧数据量主要取决于：

```text
用户数
Top-N 限制
时间窗口天数
行为类型数量
```

这就是数据库侧聚合方案的核心优势。
