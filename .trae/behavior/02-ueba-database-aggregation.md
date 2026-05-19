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
logs_structured 原始结构化日志
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

## 2. 假设日志表结构

第一版假设结构化日志表为：

```text
logs_structured
```

字段如下：

```text
timestamp   DateTime
username    String
source_ip   String
location    String
action      String
endpoint    String
status      String
```

如果实际字段不一致，必须在 `repository.py` 里通过 SQL 别名适配。

例如：

```sql
SELECT
    user_name AS username,
    src_ip AS source_ip,
    api_path AS endpoint,
    result AS status
FROM logs_structured
```

不要让上层模块关心真实数据库字段名。

---

## 3. 统一时间过滤

所有聚合 SQL 必须使用相同的时间窗口。

ClickHouse 推荐：

```sql
PREWHERE timestamp >= {start_time:DateTime}
    AND timestamp < {end_time:DateTime}
WHERE username != ''
```

要求：

```text
1. 必须有 start_time 和 end_time
2. 必须过滤空 username
3. 必须使用参数化查询
4. 不允许直接拼接用户输入
```

参数占位符说明：

```text
1. 文档中的 {start_time:DateTime}、{end_time:DateTime}、{limit:UInt32} 只表示逻辑参数
2. 实际代码实现时，必须以项目现有 ClickHouse 封装为准
3. 当前项目使用 clickhouse_connect / ClickHouseClient 的 client.query(query, parameters=params) 参数化查询方式时，应采用该客户端支持的参数写法
4. 禁止通过字符串拼接方式把 start_time、end_time、limit 等用户输入直接拼进 SQL
5. Repository 层负责封装 SQL 参数，不允许上层模块拼 SQL
```

---

## 4. 用户总览统计

### 4.1 用途

生成每个用户的基础统计骨架。

包括：

```text
总日志数
首次出现时间
最后出现时间
失败数量
活跃天数
```

### 4.2 SQL

```sql
SELECT
    username,
    count() AS sample_count,
    min(timestamp) AS first_seen,
    max(timestamp) AS last_seen,
    countIf(lower(status) IN ('failed', 'fail', 'error', 'failure')) AS failed_count,
    uniqExact(toDate(timestamp)) AS active_days
FROM logs_structured
PREWHERE timestamp >= {start_time:DateTime}
    AND timestamp < {end_time:DateTime}
WHERE username != ''
GROUP BY username;
```

### 4.3 Repository 接口

```python
def fetch_user_summary(self, start_time, end_time) -> list[dict]:
    ...
```

---

## 5. 用户小时分布统计

### 5.1 用途

统计用户在一天中的哪些小时更活跃。

第一版建议叫：

```text
active_hours
```

不建议叫：

```text
login_hours
```

原因是结构化日志中不一定只有登录日志。

### 5.2 SQL

```sql
SELECT
    username,
    toHour(timestamp) AS active_hour,
    count() AS cnt
FROM logs_structured
PREWHERE timestamp >= {start_time:DateTime}
    AND timestamp < {end_time:DateTime}
WHERE username != ''
GROUP BY
    username,
    active_hour
ORDER BY
    username,
    active_hour;
```

### 5.3 Repository 接口

```python
def fetch_hour_distribution(self, start_time, end_time) -> list[dict]:
    ...
```

---

## 6. 用户常用 IP Top-N

### 6.1 用途

统计每个用户最常见的来源 IP。

### 6.2 SQL

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
    PREWHERE timestamp >= {start_time:DateTime}
        AND timestamp < {end_time:DateTime}
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

### 6.3 Repository 接口

```python
def fetch_top_ips(self, start_time, end_time, limit: int) -> list[dict]:
    ...
```

---

## 7. 用户常用地区 Top-N

### 7.1 用途

统计每个用户最常见的来源地区。

### 7.2 SQL

```sql
SELECT
    username,
    location,
    cnt
FROM
(
    SELECT
        username,
        location,
        count() AS cnt
    FROM logs_structured
    PREWHERE timestamp >= {start_time:DateTime}
        AND timestamp < {end_time:DateTime}
    WHERE username != ''
      AND location != ''
    GROUP BY
        username,
        location
    ORDER BY
        username ASC,
        cnt DESC
)
LIMIT {limit:UInt32} BY username;
```

### 7.3 Repository 接口

```python
def fetch_top_locations(self, start_time, end_time, limit: int) -> list[dict]:
    ...
```

---

## 8. 用户常用接口 Top-N

### 8.1 用途

统计每个用户最常访问的 API 接口。

### 8.2 endpoint 归一化要求

接口可能包含查询参数，例如：

```text
/api/order?id=1
/api/order?id=2
/api/order?id=3
```

如果直接聚合，会导致接口基线碎片化。

因此应先去除查询参数：

```sql
replaceRegexpOne(endpoint, '\\?.*$', '') AS endpoint_path
```

### 8.3 SQL

```sql
SELECT
    username,
    endpoint_path,
    cnt
FROM
(
    SELECT
        username,
        replaceRegexpOne(endpoint, '\\?.*$', '') AS endpoint_path,
        count() AS cnt
    FROM logs_structured
    PREWHERE timestamp >= {start_time:DateTime}
        AND timestamp < {end_time:DateTime}
    WHERE username != ''
      AND endpoint != ''
    GROUP BY
        username,
        endpoint_path
    ORDER BY
        username ASC,
        cnt DESC
)
LIMIT {limit:UInt32} BY username;
```

### 8.4 Repository 接口

```python
def fetch_top_endpoints(self, start_time, end_time, limit: int) -> list[dict]:
    ...
```

---

## 9. 用户行为类型分布

### 9.1 用途

统计用户平时主要有哪些行为类型。

例如：

```text
LOGIN_SUCCESS
LOGIN_FAILED
API_CALL
LOGOUT
```

### 9.2 SQL

```sql
SELECT
    username,
    action,
    count() AS cnt
FROM logs_structured
PREWHERE timestamp >= {start_time:DateTime}
    AND timestamp < {end_time:DateTime}
WHERE username != ''
  AND action != ''
GROUP BY
    username,
    action
ORDER BY
    username ASC,
    cnt DESC;
```

### 9.3 Repository 接口

```python
def fetch_action_distribution(self, start_time, end_time) -> list[dict]:
    ...
```

如果 action 类型过多，可以增加：

```sql
LIMIT {limit:UInt32} BY username
```

---

## 10. 用户状态分布

### 10.1 用途

统计用户行为成功、失败、错误等状态占比。

### 10.2 SQL

```sql
SELECT
    username,
    status,
    count() AS cnt
FROM logs_structured
PREWHERE timestamp >= {start_time:DateTime}
    AND timestamp < {end_time:DateTime}
WHERE username != ''
  AND status != ''
GROUP BY
    username,
    status
ORDER BY
    username ASC,
    cnt DESC;
```

### 10.3 Repository 接口

```python
def fetch_status_distribution(self, start_time, end_time) -> list[dict]:
    ...
```

---

## 11. 用户每日事件数

### 11.1 用途

用于计算：

```text
平均每日事件数
活跃日平均事件数
最大单日事件数
```

### 11.2 SQL

```sql
SELECT
    username,
    toDate(timestamp) AS event_date,
    count() AS cnt
FROM logs_structured
PREWHERE timestamp >= {start_time:DateTime}
    AND timestamp < {end_time:DateTime}
WHERE username != ''
GROUP BY
    username,
    event_date
ORDER BY
    username ASC,
    event_date ASC;
```

### 11.3 Repository 接口

```python
def fetch_daily_event_counts(self, start_time, end_time) -> list[dict]:
    ...
```

---

## 12. Repository 类建议

```python
class UebaRepository:
    def __init__(self, client, database: str = "log_analysis"):
        self.client = client
        self.database = database

    def fetch_user_summary(self, start_time, end_time) -> list[dict]:
        ...

    def fetch_hour_distribution(self, start_time, end_time) -> list[dict]:
        ...

    def fetch_top_ips(self, start_time, end_time, limit: int) -> list[dict]:
        ...

    def fetch_top_locations(self, start_time, end_time, limit: int) -> list[dict]:
        ...

    def fetch_top_endpoints(self, start_time, end_time, limit: int) -> list[dict]:
        ...

    def fetch_action_distribution(self, start_time, end_time) -> list[dict]:
        ...

    def fetch_status_distribution(self, start_time, end_time) -> list[dict]:
        ...

    def fetch_daily_event_counts(self, start_time, end_time) -> list[dict]:
        ...
```

---

## 13. 聚合方案优势

使用数据库侧聚合后，Python 处理的数据规模从：

```text
十万级原始日志
```

变成：

```text
用户数 × 每个维度的聚合结果
```

例如：

```text
500 个用户
每个用户最多：
24 个小时分布
10 个 IP
10 个地区
20 个接口
若干 action
若干 status
30 天每日统计
```

Python 侧处理的是较短的聚合行，而不是完整日志正文。

这样可以有效避免：

```text
内存暴涨
处理速度过慢
Python 对大批量原始日志循环处理压力过大
```
