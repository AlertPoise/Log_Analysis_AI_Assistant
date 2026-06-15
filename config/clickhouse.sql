-- 创建数据库（如果不存在）
CREATE DATABASE IF NOT EXISTS {CLICKHOUSE_DATABASE};

-- 创建用户（如果不存在）并设置密码
CREATE USER IF NOT EXISTS {CLICKHOUSE_USER} IDENTIFIED BY '{CLICKHOUSE_PASSWORD}';

-- 授予用户数据库所有权限
GRANT ALL ON {CLICKHOUSE_DATABASE}.* TO {CLICKHOUSE_USER};

-- 刷新权限
FLUSH PRIVILEGES;

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
TTL timestamp + INTERVAL 180 DAY
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

-- UEBA 用户行为基线表
CREATE TABLE IF NOT EXISTS user_behavior_baselines (
    username String,
    sample_count UInt64,
    is_reliable UInt8,
    common_active_hours String,
    common_source_ips String,
    common_destination_ips String,
    common_source_countries String,
    common_source_cities String,
    common_vpn_gateways String,
    action_distribution String,
    event_type_distribution String,
    result_distribution String,
    fail_reason_distribution String,
    auth_method_distribution String,
    client_software_distribution String,
    protocol_distribution String,
    failed_rate Float64,
    off_hours_rate Float64,
    unusual_ip_rate Float64,
    avg_daily_events Float64,
    active_day_avg_events Float64,
    max_daily_events UInt64,
    session_metric_summary String,
    traffic_metric_summary String,
    baseline_start_time DateTime,
    baseline_end_time DateTime,
    model_version String,
    baseline_json String,
    created_at DateTime DEFAULT now()
) ENGINE = ReplacingMergeTree(created_at)
ORDER BY (username, model_version, baseline_start_time, baseline_end_time);

-- UEBA baseline 受控训练日志表
CREATE TABLE IF NOT EXISTS ueba_baseline_training_logs (
    dataset_id String,
    baseline_purpose String,
    is_active UInt8,
    import_batch_id String,
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
    uri Nullable(String),
    method Nullable(String),
    status_code Nullable(UInt16),
    response_time Nullable(Float32),
    detail Nullable(String),
    severity_level Nullable(String),
    device_info Nullable(String),
    location Nullable(String),
    request_id Nullable(String),
    raw_log Nullable(String),
    parser Nullable(String),
    parse_status Nullable(String),
    source_table Nullable(String),
    source_record_id Nullable(String),
    remark Nullable(String),
    created_by Nullable(String),
    created_at DateTime DEFAULT now()
) ENGINE = MergeTree()
PARTITION BY toYYYYMM(timestamp)
ORDER BY (dataset_id, log_type, timestamp, username);

-- UEBA validation 评分结果表
CREATE TABLE IF NOT EXISTS ueba_validation_results (
    validation_id String,
    validation_run_id String,
    source_identity String,
    source_log_id UInt64,
    timestamp DateTime,
    username String,
    log_type String,
    request_id Nullable(String),
    baseline_model_version String,
    baseline_created_at Nullable(DateTime),
    baseline_is_reliable UInt8,
    ueba_score UInt8,
    ueba_risk_level LowCardinality(String),
    ueba_anomaly_reasons String,
    validation_status LowCardinality(String),
    validated_at DateTime,
    error Nullable(String),
    created_at DateTime DEFAULT now()
) ENGINE = MergeTree()
PARTITION BY toYYYYMM(timestamp)
ORDER BY (baseline_model_version, validation_run_id, log_type, timestamp, username, source_identity);

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

-- ------------------------------------------------------------------
-- AI 基线强化建议表
-- 存储 AI 对用户基线的强化分析结果，用于后续评分闭环和可视化
-- ------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS baseline_ai_refinements (
    username String,
    model_version String,
    analysis_summary String,
    pattern_type String,
    is_baseline_stale UInt8,
    stale_features String,
    suggested_adjustments String,
    new_watch_features String,
    reinforced_baseline_delta String,
    confidence Float64,
    ai_platform String,
    validated_at DateTime,
    anomaly_event_count UInt32,
    raw_response String,
    created_at DateTime DEFAULT now()
) ENGINE = ReplacingMergeTree(created_at)
ORDER BY (username, model_version, validated_at);

-- ------------------------------------------------------------------
-- 人工反馈表
-- 用于记录安全分析师对 AI 分析结果的反馈（确认违规/误报）
-- 累计误报次数决定 AI 强化置信度衰减
-- ------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS human_feedback (
    username String,
    model_version String,
    stale_feature String,
    decision UInt8,
    reviewer String,
    created_at DateTime DEFAULT now()
) ENGINE = ReplacingMergeTree(created_at)
ORDER BY (username, stale_feature);