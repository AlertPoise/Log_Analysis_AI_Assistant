USE {CLICKHOUSE_DATABASE};

CREATE TABLE IF NOT EXISTS logs_raw (
    id UInt64,
    timestamp DateTime,
    log_type String,
    source String,
    raw_message String,
    username Nullable(String),
    source_ip Nullable(String),
    action Nullable(String),
    status Nullable(String),
    collected_at DateTime,
    created_at DateTime
) ENGINE = Kafka()
SETTINGS
    kafka_broker_list = 'localhost:9092',
    kafka_topic_list = 'logs_raw',
    kafka_group_name = 'log_analysis_consumer',
    kafka_format = 'JSONEachRow',
    kafka_max_block_size = 1048576;

CREATE TABLE IF NOT EXISTS {CLICKHOUSE_TABLE} (
    id UInt64,
    timestamp DateTime,
    log_type String,
    source String,
    username String,
    user_id Nullable(String),
    dept Nullable(String),
    role Nullable(String),
    action String,
    event_type Nullable(String),
    result Nullable(String),
    fail_reason Nullable(String),
    source_ip Nullable(String),
    destination_ip Nullable(String),
    vpn_gateway Nullable(String),
    src_country Nullable(String),
    src_city Nullable(String),
    protocol Nullable(String),
    auth_method Nullable(String),
    client_software Nullable(String),
    user_agent Nullable(String),
    session_id Nullable(String),
    is_off_hours Nullable(Bool),
    is_unusual_ip Nullable(Bool),
    session_duration_sec Nullable(UInt32),
    bytes_sent Nullable(UInt64),
    bytes_recv Nullable(UInt64),
    risk_score Nullable(UInt8),
    risk_tags Nullable(String),
    uri Nullable(String),
    method Nullable(String),
    status_code Nullable(UInt16),
    response_time Nullable(Float32),
    detail Nullable(String),
    severity_level Nullable(String),
    device_info Nullable(String),
    location Nullable(String),
    request_id Nullable(String),
    collected_at DateTime,
    parsed_at DateTime DEFAULT now(),
    indexed_at DateTime DEFAULT now(),
    raw_log Nullable(String),
    parser Nullable(String),
    parse_status Nullable(String)
) ENGINE = MergeTree()
PARTITION BY toYYYYMMDD(timestamp)
ORDER BY (log_type, timestamp, username)
TTL timestamp + INTERVAL 90 DAY
SETTINGS index_granularity = 8192;

CREATE TABLE IF NOT EXISTS user_behavior_stats (
    username String,
    date Date,
    total_actions UInt32,
    login_count UInt32,
    logout_count UInt32,
    api_call_count UInt32,
    failed_login_count UInt32,
    unique_ips UInt32,
    unique_locations UInt32,
    avg_response_time Float32,
    max_response_time Float32,
    common_ip_array Array(String),
    common_time_slots Array(UInt8),
    risk_score Float32 DEFAULT 0.0,
    calculated_at DateTime DEFAULT now()
) ENGINE = ReplacingMergeTree()
PARTITION BY toYYYYMM(date)
ORDER BY (username, date)
TTL date + INTERVAL 90 DAY
SETTINGS index_granularity = 8192;

CREATE TABLE IF NOT EXISTS anomaly_detection (
    id UInt64,
    detection_time DateTime DEFAULT now(),
    username String,
    anomaly_type String,
    anomaly_score Float32,
    risk_level String,
    description String,
    context String,
    related_events Array(UInt64),
    is_processed Bool DEFAULT false,
    processed_at Nullable(DateTime),
    ai_analysis Nullable(String),
    threat_type Nullable(String),
    `处置建议` Nullable(String)
) ENGINE = MergeTree()
PARTITION BY toYYYYMM(detection_time)
ORDER BY (detection_time, username)
TTL detection_time + INTERVAL 90 DAY
SETTINGS index_granularity = 8192;

CREATE TABLE IF NOT EXISTS ai_analysis_reports (
    id UInt64,
    report_date Date,
    report_type String,
    username String,
    anomaly_id UInt64,
    threat_type String,
    risk_level String,
    risk_score Float32,
    description String,
    context String,
    ai_suggestion String,
    created_at DateTime DEFAULT now()
) ENGINE = MergeTree()
PARTITION BY toYYYYMM(report_date)
ORDER BY (report_date, risk_level, risk_score)
TTL report_date + INTERVAL 90 DAY
SETTINGS index_granularity = 8192;

CREATE TABLE IF NOT EXISTS daily_reports (
    report_date Date,
    total_logs UInt64,
    total_users UInt32,
    total_anomalies UInt32,
    high_risk_count UInt32,
    medium_risk_count UInt32,
    low_risk_count UInt32,
    overall_score Float32,
    top_risky_users Array(String),
    summary_text String,
    generated_at DateTime DEFAULT now()
) ENGINE = MergeTree()
ORDER BY report_date
TTL report_date + INTERVAL 365 DAY
SETTINGS index_granularity = 8192;

CREATE MATERIALIZED VIEW IF NOT EXISTS user_behavior_mv
TO user_behavior_stats
AS SELECT
    username,
    toDate(timestamp) as date,
    count() as total_actions,
    countIf(action = 'LOGIN') as login_count,
    countIf(action = 'LOGOUT') as logout_count,
    countIf(action LIKE '%API%') as api_call_count,
    countIf(result = 'FAIL') as failed_login_count,
    uniq(source_ip) as unique_ips,
    0 as unique_locations,
    0.0 as avg_response_time,
    0.0 as max_response_time,
    groupArray(source_ip) as common_ip_array,
    groupArray(toHour(timestamp)) as common_time_slots,
    0.0 as risk_score,
    now() as calculated_at
FROM {CLICKHOUSE_TABLE}
WHERE username IS NOT NULL
GROUP BY username, toDate(timestamp);

CREATE INDEX IF NOT EXISTS idx_username ON {CLICKHOUSE_TABLE} (username) TYPE bloom_filter GRANULARITY 4;
CREATE INDEX IF NOT EXISTS idx_source_ip ON {CLICKHOUSE_TABLE} (source_ip) TYPE bloom_filter GRANULARITY 4;
CREATE INDEX IF NOT EXISTS idx_action ON {CLICKHOUSE_TABLE} (action) TYPE bloom_filter GRANULARITY 4;
CREATE INDEX IF NOT EXISTS idx_risk_level ON anomaly_detection (risk_level) TYPE bloom_filter GRANULARITY 4;