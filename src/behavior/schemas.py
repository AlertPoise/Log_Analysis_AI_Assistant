"""行为模块共享 schema。"""

from typing import Any, Dict, List, TypedDict


class NormalizedBehaviorLog(TypedDict, total=False):
    """标准化后的行为日志结构。"""

    id: int
    timestamp: str
    username: str
    log_type: str
    action: str
    status: str
    source_ip: str
    location: str
    endpoint: str
    method: str
    response_time: float
    user_agent: str
    dept: str
    role: str
    protocol: str
    auth_method: str
    vpn_gateway: str
    session_id: str
    fail_reason: str
    raw_log: str
    parser: str
    parse_status: str


class BehaviorBaselineResult(TypedDict):
    """行为基线输出结构。"""

    username: str
    sample_count: int
    is_reliable: bool
    activity_hours: Dict[int, int]
    common_hours: List[int]
    ip_frequency: Dict[str, int]
    common_ips: List[str]
    location_frequency: Dict[str, int]
    common_locations: List[str]
    action_frequency: Dict[str, int]
    api_frequency: Dict[str, int]
    api_call_avg_per_hour: float
    failed_login_count: int
    failed_login_rate: float
    calculated_at: str


class UserProfileResult(TypedDict):
    """用户画像输出结构。"""

    username: str
    created_at: str
    updated_at: str
    login_times: List[str]
    common_ips: List[str]
    common_locations: List[str]
    user_agents: List[str]
    api_call_frequency: float
    activity_hours: Dict[int, int]
    failed_login_count: int
    total_actions: int
    baseline: BehaviorBaselineResult


class AnomalyContext(TypedDict, total=False):
    """异常上下文结构。"""

    matched_rules: List[str]
    baseline_common_hours: List[int]
    baseline_common_ips: List[str]
    baseline_common_locations: List[str]
    meets_threshold: bool
    source_ips: List[str]
    window_minutes: int
    current_count: int
    baseline_avg: float
    current_failed: int
    baseline_failed: float


class AnomalyResult(TypedDict):
    """异常检测输出结构。"""

    anomaly_id: str
    username: str
    timestamp: str
    anomaly_type: str
    anomaly_score: float
    risk_level: str
    is_alert: bool
    description: str
    source_ip: str
    location: str
    context: AnomalyContext
    related_logs: List[int]


class BehaviorAnalysisSummary(TypedDict):
    """行为分析摘要。"""

    anomaly_count: int
    alert_count: int
    highest_risk_level: str


class BehaviorAnalysisResult(TypedDict):
    """统一行为分析服务输出结构。"""

    username: str
    baseline: BehaviorBaselineResult
    profile: UserProfileResult
    anomalies: List[AnomalyResult]
    summary: BehaviorAnalysisSummary
