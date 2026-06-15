"""
Streamlit 可视化仪表板
日志分析 AI 助手 - 自动化简报 + 可视化模块

验收标准:
1. 界面可实时查看日志
2. 自动生成 PDF/文本简报
3. 展示高危用户与评分
"""
import sys
import os

# 将项目根目录添加到 Python 路径（必须在其他本地导入之前）
project_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if project_root not in sys.path:
    sys.path.insert(0, project_root)

import streamlit as st
from typing import Any, Dict, List
from datetime import datetime, timedelta
import pandas as pd
import json
import time
import io
import logging

from src.ai.analyzer import AIAnalyzer
from src.utils.config import settings
import clickhouse_connect

# 确保 logs 目录存在
logs_dir = os.path.join(project_root, "logs")
os.makedirs(logs_dir, exist_ok=True)

# 获取当前日期作为日志文件名
log_filename = os.path.join(logs_dir, f"dashboard_{datetime.now().strftime('%Y-%m-%d')}.log")

# 创建日志处理器
handlers = [
    logging.StreamHandler(),  # 控制台输出
    logging.FileHandler(log_filename, encoding='utf-8')  # 文件输出
]

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=handlers
)
logger = logging.getLogger(__name__)

# 尝试导入存储模块
try:
    from src.storage.clickhouse import ClickHouseClient
    from src.storage.kafka_client import KafkaClient
    STORAGE_AVAILABLE = True
    logger.info("✅ 成功导入存储模块")
except ImportError as e:
    STORAGE_AVAILABLE = False
    logger.warning(f"⚠️ 无法导入存储模块，将使用模拟数据: {e}")

# 尝试导入 AI 模块
try:
    from src.ai.analyzer import AIAnalyzer
    from src.utils.config import settings
    AI_AVAILABLE = True
    logger.info("✅ 成功导入 AI 模块")
except ImportError as e:
    AI_AVAILABLE = False
    logger.warning(f"⚠️ 无法导入 AI 模块: {e}")

from fpdf import FPDF
from src.behavior.api import (
    analyze_behavior_for_frontend,
    analyze_behavior_from_clickhouse,
    get_behavior_dashboard_data,
)

# ClickHouse 直连辅助函数（弃用旧的 ClickHouseClient 封装，直连避免传参 bug）
def get_clickhouse_client():
    """获取 ClickHouse 原生连接客户端"""
    import clickhouse_connect
    return clickhouse_connect.get_client(
        host=settings.clickhouse_host,
        port=settings.clickhouse_port,
        username=settings.clickhouse_user,
        password=settings.clickhouse_password,
        database=settings.clickhouse_database,
    )

# 设置页面配置
st.set_page_config(
    page_title="日志分析 AI 助手",
    page_icon="🔍",
    layout="wide",
    initial_sidebar_state="expanded"
)

# ==================== 会话状态 ====================


def generate_pdf_report(report_type, data=None):
    """生成 PDF 报告，返回 bytes。非 ASCII 字符转 XML 实体。"""
    from fpdf import FPDF
    pdf = FPDF()
    pdf.add_page()
    pdf.set_font("Helvetica", size=10)

    def _safe(t: str) -> str:
        return t.encode('ascii', errors='replace').decode('ascii')

    if report_type == "history" and data:
        pdf.set_font("Helvetica", style="B", size=14)
        pdf.cell(0, 10, "History Query Report", new_x="LMARGIN", new_y="NEXT", align="C")
        pdf.set_font("Helvetica", size=9)
        pdf.cell(0, 8, f"Generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}", new_x="LMARGIN", new_y="NEXT")
        pdf.ln(4)

        cols = ["Time", "User", "Type", "IP", "Status", "Location", "Risk"]
        widths = [35, 25, 20, 35, 20, 25, 30]
        pdf.set_font("Helvetica", style="B", size=8)
        for col, w in zip(cols, widths):
            pdf.cell(w, 7, col, border=1)
        pdf.ln()

        pdf.set_font("Helvetica", size=7)
        for row in data[:100]:
            if pdf.get_y() > 260:
                pdf.add_page()
                pdf.set_font("Helvetica", size=7)
            vals = [_safe(str(row.get(c, "")))[:14] for c in ["时间", "用户", "类型", "IP", "状态", "地点", "风险等级"]]
            for v, w in zip(vals, widths):
                pdf.cell(w, 6, v, border=1)
            pdf.ln()
    else:
        pdf.set_font("Helvetica", size=12)
        pdf.cell(0, 10, "No data available", new_x="LMARGIN", new_y="NEXT")

    return bytes(pdf.output())

def init_session_state():
    """初始化 session state"""
    if "current_page" not in st.session_state:
        st.session_state.current_page = "安全评分看板"
    if "logs_data" not in st.session_state:
        st.session_state.logs_data = []
    if "anomaly_users" not in st.session_state:
        st.session_state.anomaly_users = []
    if "ai_suggestions" not in st.session_state:
        st.session_state.ai_suggestions = []
    if "data_source" not in st.session_state:
        st.session_state.data_source = "模拟数据"


@st.cache_resource
def get_ai_analyzer():
    """获取 AI 分析器实例（缓存）"""
    if not AI_AVAILABLE:
        return None
    try:
        config = settings.current_ai_config
        analyzer = AIAnalyzer(
            api_key=config["api_key"],
            platform=config["platform"],
            model=config.get("model"),
            base_url=config.get("base_url"),
        )
        logger.info(f"🤖 AI 分析器初始化成功: platform={config['platform']}")
        return analyzer
    except Exception as e:
        logger.error(f"❌ AI 分析器初始化失败: {e}")
        return None


def analyze_anomaly_with_ai(username: str, anomaly_description: str, log_context: str = None) -> Dict[str, Any]:
    """使用 AI 分析异常行为"""
    analyzer = get_ai_analyzer()
    if analyzer is None:
        return {
            "threat_type": "AI_UNAVAILABLE",
            "risk_level": "MEDIUM",
            "description": "AI 服务不可用，请检查配置",
            "suggestion": "请人工审查该异常行为"
        }
    
    try:
        result = analyzer.analyze_anomaly(
            username=username,
            anomaly_description=anomaly_description,
            log_context=log_context
        )
        logger.info(f"🤖 AI 分析完成: user={username}, threat={result.get('threat_type')}")
        return result
    except Exception as e:
        logger.error(f"❌ AI 分析失败: {e}")
        return {
            "threat_type": "ANALYSIS_ERROR",
            "risk_level": "MEDIUM",
            "description": f"AI 分析失败: {str(e)}",
            "suggestion": "请人工审查该异常行为"
        }


# ==================== 模拟数据层 ====================

def get_sample_logs(log_type="全部"):
    """获取模拟日志数据"""
    sample_logs = [
        {"时间": datetime.now().strftime("%Y-%m-%d %H:%M:%S"), "类型": "VPN 登录", "用户": "zhangsan", 
         "IP": "192.168.1.100", "状态": "✅ 成功", "地点": "北京", "风险": "🟢 正常"},
        {"时间": (datetime.now() - timedelta(seconds=5)).strftime("%Y-%m-%d %H:%M:%S"), "类型": "API 调用", "用户": "lisi", 
         "IP": "192.168.1.101", "状态": "✅ 成功", "地点": "上海", "风险": "🟢 正常"},
        {"时间": (datetime.now() - timedelta(seconds=10)).strftime("%Y-%m-%d %H:%M:%S"), "类型": "VPN 登录", "用户": "wangwu", 
         "IP": "10.0.0.100", "状态": "❌ 失败", "地点": "广州", "风险": "🔴 高危"},
        {"时间": (datetime.now() - timedelta(seconds=15)).strftime("%Y-%m-%d %H:%M:%S"), "类型": "系统日志", "用户": "system", 
         "IP": "127.0.0.1", "状态": "⚠️ 警告", "地点": "本地", "风险": "🟡 低危"},
        {"时间": (datetime.now() - timedelta(seconds=20)).strftime("%Y-%m-%d %H:%M:%S"), "类型": "安全设备", "用户": "firewall", 
         "IP": "192.168.1.1", "状态": "🔴 阻断", "地点": "边界", "风险": "🔴 高危"},
    ]
    
    if log_type != "全部":
        sample_logs = [log for log in sample_logs if log["类型"] == log_type]
    
    return sample_logs


def get_sample_anomaly_users():
    """获取模拟异常用户数据"""
    return {
        "排名": list(range(1, 11)),
        "用户名": ["zhangsan", "lisi", "wangwu", "zhaoliu", "sunqi", 
                   "zhouba", "wujiu", "zhengshi", "qianshi", "liushi"],
        "异常评分": [0.95, 0.88, 0.82, 0.75, 0.68, 0.62, 0.55, 0.48, 0.42, 0.35],
        "风险等级": ["🔴 高危", "🔴 高危", "🟠 中危", "🟠 中危", "🟠 中危",
                    "🟡 低危", "🟡 低危", "🟡 低危", "🟡 低危", "🟡 低危"],
        "异常事件数": [15, 12, 10, 8, 7, 6, 5, 4, 3, 2],
        "最近异常时间": [datetime.now().strftime("%Y-%m-%d %H:%M"), 
                        (datetime.now() - timedelta(hours=1)).strftime("%Y-%m-%d %H:%M"),
                        (datetime.now() - timedelta(hours=2)).strftime("%Y-%m-%d %H:%M"),
                        (datetime.now() - timedelta(hours=3)).strftime("%Y-%m-%d %H:%M"),
                        (datetime.now() - timedelta(hours=5)).strftime("%Y-%m-%d %H:%M"),
                        (datetime.now() - timedelta(hours=7)).strftime("%Y-%m-%d %H:%M"),
                        (datetime.now() - timedelta(hours=9)).strftime("%Y-%m-%d %H:%M"),
                        (datetime.now() - timedelta(hours=11)).strftime("%Y-%m-%d %H:%M"),
                        (datetime.now() - timedelta(hours=13)).strftime("%Y-%m-%d %H:%M"),
                        (datetime.now() - timedelta(hours=15)).strftime("%Y-%m-%d %H:%M")]
    }


def get_sample_security_metrics():
    """获取模拟安全指标数据"""
    return {
        "security_score": 75,
        "anomaly_count": 12,
        "high_risk_count": 5,
        "disposed_count": 8
    }


def get_sample_security_trend(days=7):
    """获取模拟安全评分趋势"""
    dates = pd.date_range(end=datetime.now(), periods=days, freq='D').strftime("%Y-%m-%d")
    return pd.DataFrame({
        "日期": dates,
        "安全评分": [85, 82, 78, 80, 75, 73, 75][-days:],
        "异常事件数": [5, 8, 10, 7, 12, 15, 12][-days:]
    })


def get_sample_risk_distribution():
    """获取模拟风险等级分布"""
    return pd.DataFrame({
        "风险等级": ["🔴 高危", "🟠 中危", "🟡 低危"],
        "事件数": [5, 18, 45]
    })


def get_sample_threat_stats():
    """获取模拟威胁类型统计"""
    return pd.DataFrame({
        "威胁类型": ["账号接管", "异常访问", "暴力破解", "数据外传", "其他"],
        "数量": [3, 15, 8, 2, 40]
    })


def get_sample_ai_suggestions(status_filter="全部", risk_filter="全部"):
    """获取模拟 AI 处置建议数据"""
    suggestions = [
        {
            "id": 1, "用户": "zhangsan", "威胁类型": "账号接管", "风险等级": "🔴 高危",
            "异常描述": "检测到用户在凌晨 3 点从异地 IP 登录，并频繁调用敏感 API",
            "AI 分析": "该行为符合账号接管攻击特征，攻击者可能已获取用户凭据",
            "处置建议": "立即冻结账号，联系用户确认，调查登录来源 IP",
            "置信度": "92%", "处置状态": "待处置", "生成时间": datetime.now().strftime("%Y-%m-%d %H:%M")
        },
        {
            "id": 2, "用户": "lisi", "威胁类型": "暴力破解", "风险等级": "🔴 高危",
            "异常描述": "检测到同一 IP 在 5 分钟内尝试登录 50 次，涉及多个账号",
            "AI 分析": "典型的暴力破解攻击，建议封禁来源 IP",
            "处置建议": "封禁 IP 地址 10.0.0.100，启用账号锁定策略",
            "置信度": "98%", "处置状态": "待处置", "生成时间": (datetime.now() - timedelta(hours=1)).strftime("%Y-%m-%d %H:%M")
        },
        {
            "id": 3, "用户": "wangwu", "威胁类型": "数据外传", "风险等级": "🟠 中危",
            "异常描述": "用户批量下载敏感数据，下载量超过平时 10 倍",
            "AI 分析": "可能存在数据外传风险，需要进一步核实业务需求",
            "处置建议": "限制下载权限，联系用户主管确认业务需求",
            "置信度": "75%", "处置状态": "处置中", "生成时间": (datetime.now() - timedelta(hours=3)).strftime("%Y-%m-%d %H:%M")
        },
        {
            "id": 4, "用户": "zhaoliu", "威胁类型": "异常访问", "风险等级": "🟡 低危",
            "异常描述": "用户在非工作时间访问系统",
            "AI 分析": "可能是用户加班，建议核实",
            "处置建议": "联系用户确认访问原因",
            "置信度": "45%", "处置状态": "已处置", "生成时间": (datetime.now() - timedelta(hours=5)).strftime("%Y-%m-%d %H:%M")
        },
        {
            "id": 5, "用户": "sunqi", "威胁类型": "权限提升", "风险等级": "🔴 高危",
            "异常描述": "用户尝试访问超出权限的资源",
            "AI 分析": "可能存在权限提升攻击",
            "处置建议": "立即冻结账号，进行安全审计",
            "置信度": "88%", "处置状态": "误报", "生成时间": (datetime.now() - timedelta(hours=8)).strftime("%Y-%m-%d %H:%M")
        },
    ]
    
    filtered = []
    for suggestion in suggestions:
        if status_filter != "全部" and suggestion["处置状态"] != status_filter:
            continue
        if risk_filter != "全部" and suggestion["风险等级"] != risk_filter:
            continue
        filtered.append(suggestion)
    
    return filtered


def get_sample_search_results():
    """获取模拟历史查询结果"""
    return [
        {"时间": datetime.now().strftime("%Y-%m-%d %H:%M:%S"), "用户": "zhangsan", "类型": "VPN 登录", 
         "IP": "10.0.0.100", "状态": "❌ 失败", "地点": "广州", "风险等级": "🔴 高危"},
        {"时间": (datetime.now() - timedelta(minutes=10)).strftime("%Y-%m-%d %H:%M:%S"), "用户": "zhangsan", "类型": "API 调用", 
         "IP": "10.0.0.100", "状态": "✅ 成功", "地点": "广州", "风险等级": "🟠 中危"},
        {"时间": (datetime.now() - timedelta(hours=2)).strftime("%Y-%m-%d %H:%M:%S"), "用户": "lisi", "类型": "VPN 登录", 
         "IP": "192.168.1.101", "状态": "✅ 成功", "地点": "上海", "风险等级": "🟡 低危"},
    ]


# ==================== Behavior 演示数据层 ====================

def build_demo_behavior_payload() -> Dict[str, Any]:
    """基于 VPN 样例结构构造可供 behavior 模块分析的演示 payload。"""
    return {
        "target_user": "sun.lei",
        "history_logs": [
            {
                "timestamp": "2026-04-01 10:39:47",
                "username": "sun.lei",
                "source_ip": "101.89.15.237",
                "location": "上海",
                "action": "LOGIN",
                "event_type": "LOGIN_SUCCESS",
                "status": "SUCCESS",
            },
            {
                "timestamp": "2026-04-01 12:00:24",
                "username": "sun.lei",
                "source_ip": "117.136.0.238",
                "location": "上海",
                "action": "LOGIN",
                "event_type": "LOGIN_SUCCESS",
                "status": "SUCCESS",
            },
            {
                "timestamp": "2026-04-01 12:05:35",
                "username": "sun.lei",
                "source_ip": "117.136.0.213",
                "location": "上海",
                "action": "LOGIN",
                "event_type": "LOGIN_SUCCESS",
                "status": "SUCCESS",
            },
            {
                "timestamp": "2026-04-02 08:51:38",
                "username": "sun.lei",
                "source_ip": "101.89.15.125",
                "location": "上海",
                "action": "LOGIN",
                "event_type": "LOGIN_SUCCESS",
                "status": "SUCCESS",
            },
            {
                "timestamp": "2026-04-02 10:25:45",
                "username": "sun.lei",
                "source_ip": "101.89.15.20",
                "location": "上海",
                "action": "LOGIN",
                "event_type": "LOGIN_SUCCESS",
                "status": "SUCCESS",
            },
        ],
        "detection_logs": [
            {
                "timestamp": "2026-04-02 21:53:34",
                "username": "sun.lei",
                "source_ip": "185.220.101.30",
                "location": "阿姆斯特丹",
                "action": "LOGIN",
                "event_type": "LOGIN_FAIL",
                "status": "FAIL",
            }
        ],
    }


def get_behavior_demo_result() -> Dict[str, Any]:
    """调用 behavior 前端接口生成演示分析结果，失败时返回稳定结构。"""
    try:
        result = analyze_behavior_for_frontend(build_demo_behavior_payload())
        return {**result, "source": "behavior_demo"}
    except Exception as exc:
        logger.exception("获取 behavior 演示分析失败")
        return {
            "success": False,
            "source": "behavior_demo",
            "target_user": None,
            "baseline": {},
            "profile": {},
            "anomalies": [],
            "summary": {},
            "error": {
                "code": "DASHBOARD_BEHAVIOR_DEMO_ERROR",
                "message": str(exc),
            },
        }


def convert_behavior_result_for_dashboard(result: Dict[str, Any]) -> Dict[str, Any]:
    """将 behavior 返回结果整理为 dashboard 便于展示的结构。"""
    anomalies = result.get("anomalies") if isinstance(result.get("anomalies"), list) else []
    error = result.get("error") if isinstance(result.get("error"), dict) else None
    return {
        "source": result.get("source", "behavior_demo"),
        "target_user": result.get("target_user"),
        "baseline": result.get("baseline") if isinstance(result.get("baseline"), dict) else {},
        "profile": result.get("profile") if isinstance(result.get("profile"), dict) else {},
        "anomalies": anomalies,
        "summary": result.get("summary") if isinstance(result.get("summary"), dict) else {},
        "anomaly_count": len(anomalies),
        "is_success": bool(result.get("success")),
        "error": error,
    }


def get_behavior_analysis_for_dashboard(target_user: str = "zhangsan") -> Dict[str, Any]:
    """优先读取 ClickHouse behavior，失败时回退到演示分析结果。"""
    client = None
    try:
        client = _get_behavior_clickhouse_client()
        clickhouse_result = analyze_behavior_from_clickhouse(
            client=client,
            database=settings.clickhouse_database,
            username=target_user,
        )
    except Exception as exc:
        logger.exception("获取 ClickHouse behavior 分析失败")
        clickhouse_result = {
            "success": False,
            "source": "clickhouse",
            "error": str(exc),
        }
    finally:
        if client is not None:
            client.close()

    if clickhouse_result.get("success"):
        dashboard_data = convert_behavior_result_for_dashboard(clickhouse_result)
        dashboard_data["source"] = "clickhouse"
        dashboard_data["fallback_reason"] = None
        dashboard_data["clickhouse_error"] = None
        return dashboard_data

    demo_result = get_behavior_demo_result()
    dashboard_data = convert_behavior_result_for_dashboard(demo_result)
    dashboard_data["source"] = dashboard_data.get("source") or "behavior_demo"
    dashboard_data["fallback_reason"] = clickhouse_result.get("error")
    dashboard_data["clickhouse_error"] = clickhouse_result.get("error")
    return dashboard_data


def show_behavior_analysis_demo(
    target_user: str = "zhangsan",
    dashboard_data: Dict[str, Any] | None = None,
) -> None:
    """展示真实数据优先、演示数据兜底的用户行为分析结果。"""
    st.divider()
    st.subheader("🧭 用户行为分析")

    if dashboard_data is None:
        dashboard_data = get_behavior_analysis_for_dashboard(target_user)
    if not dashboard_data["is_success"]:
        error = dashboard_data.get("error") or {}
        st.warning(f"Behavior 分析暂不可用：{error.get('message', '未知错误')}")
        st.caption("数据来源：behavior_demo（调用失败，保留原页面 fallback）")
        return

    baseline = dashboard_data["baseline"]
    summary = dashboard_data["summary"]

    st.caption(f"数据来源：{dashboard_data['source']}")
    if dashboard_data.get("fallback_reason"):
        st.info(
            "ClickHouse 数据不可用，已回退到 demo 数据。"
            f" 原因：{dashboard_data['fallback_reason']}"
        )
    col1, col2, col3, col4 = st.columns(4)
    with col1:
        st.metric("目标用户", dashboard_data.get("target_user") or "-")
    with col2:
        st.metric("基线样本数", str(baseline.get("sample_count", 0)))
    with col3:
        st.metric("基线可靠", "是" if baseline.get("is_reliable") else "否")
    with col4:
        st.metric("异常数量", str(dashboard_data["anomaly_count"]))

    detail_col1, detail_col2, detail_col3 = st.columns(3)
    with detail_col1:
        st.markdown(f"**常用时间段**: {baseline.get('common_hours', [])}")
    with detail_col2:
        st.markdown(f"**常用 IP**: {baseline.get('common_ips', [])}")
    with detail_col3:
        st.markdown(f"**常用地点**: {baseline.get('common_locations', [])}")

    st.markdown("**摘要**")
    st.json(summary)

    st.markdown("**异常列表**")
    anomalies = dashboard_data["anomalies"]
    if anomalies:
        st.dataframe(pd.DataFrame(anomalies), use_container_width=True, hide_index=True)
    else:
        st.info("当前演示数据未检测到异常")


# ==================== 真实接口层 ====================

REALTIME_LOG_TYPE_FILTERS = {
    "全部": None,
    "VPN 登录": "vpn",
    "API 调用": "api",
    "系统日志": "system",
    "安全设备": "security",
}

REALTIME_LOG_TYPE_LABELS = {
    "vpn": "VPN 登录",
    "api": "API 调用",
    "system": "系统日志",
    "security": "安全设备",
}

REALTIME_REFRESH_SECONDS = {
    "1 秒": 1,
    "5 秒": 5,
    "10 秒": 10,
    "30 秒": 30,
}


def _normalize_realtime_log_type_filter(log_type: str) -> str | None:
    """把页面展示文案转换为 logs_structured.log_type 的真实取值。"""
    if log_type in REALTIME_LOG_TYPE_FILTERS:
        return REALTIME_LOG_TYPE_FILTERS[log_type]
    if log_type and log_type != "全部":
        return log_type
    return None


def _format_realtime_log_type(log_type: Any) -> str:
    """把数据库 log_type 转为页面展示文案，未知值保留原样。"""
    raw_type = str(log_type or "未知")
    return REALTIME_LOG_TYPE_LABELS.get(raw_type, raw_type)


def _risk_label_from_score(score: Any, risk_level: Any = None) -> str:
    """把 UEBA/日志风险分映射为页面展示风险。"""
    level = str(risk_level or "").upper()
    if level in {"CRITICAL", "HIGH"}:
        return "🔴 高危"
    if level == "MEDIUM":
        return "🟠 中危"
    if level == "LOW":
        return "🟡 低危"
    try:
        numeric_score = int(score or 0)
    except (TypeError, ValueError):
        numeric_score = 0
    if numeric_score >= 75:
        return "🔴 高危"
    if numeric_score >= 50:
        return "🔴 高危"
    if numeric_score >= 25:
        return "🟠 中危"
    if numeric_score > 0:
        return "🟡 低危"
    return "🟢 正常"


def _fallback_realtime_risk_score(raw_log: Any, event_type: Any, result: Any, is_unusual_ip: Any) -> int:
    """validation 尚未写入时，根据持续生成器 marker 给实时日志即时风险兜底。"""
    raw = str(raw_log or "")
    if "mode=critical_combo" in raw or "mode=combo_anomaly" in raw:
        return 100
    if "mode=high_country_failed" in raw:
        return 60
    if "mode=medium_country" in raw or "mode=new_country" in raw:
        return 38
    if "mode=new_ip" in raw:
        return 32
    if "mode=failed_login" in raw:
        return 20
    if "mode=off_hours" in raw:
        return 10
    event = str(event_type or "").upper()
    res = str(result or "").upper()
    if event == "LOGIN_FAIL" or res in {"FAILED", "FAIL"}:
        return 20
    if bool(is_unusual_ip):
        return 20
    return 0


def _refresh_interval_seconds(refresh_rate: str) -> int:
    """返回实时页面刷新间隔秒数。"""
    return REALTIME_REFRESH_SECONDS.get(refresh_rate, 5)


def fetch_realtime_logs(log_type="全部", limit=100):
    """从 ClickHouse logs_structured 表获取真实日志，使用 settings 配置。"""
    import clickhouse_connect

    safe_limit = min(max(int(limit), 1), 1000)
    log_type_filter = _normalize_realtime_log_type_filter(log_type)
    client = None
    try:
        client = clickhouse_connect.get_client(
            host=settings.clickhouse_host,
            port=settings.clickhouse_port,
            username=settings.clickhouse_user,
            password=settings.clickhouse_password,
            database=settings.clickhouse_database,
            connect_timeout=5
        )
    except Exception as e:
        logger.warning(f"ClickHouse 连接失败: {e}")
        return get_sample_logs(log_type)

    filters = []
    parameters = {}
    if log_type_filter is not None:
        filters.append("l.log_type = %(log_type)s")
        parameters["log_type"] = log_type_filter

    where_clause = f"WHERE {' AND '.join(filters)}" if filters else ""
    query = f"""
    SELECT
        toTimezone(l.timestamp, 'Asia/Shanghai') AS local_timestamp,
        l.log_type,
        l.username,
        l.source_ip,
        l.action,
        l.src_city,
        l.risk_score,
        l.event_type,
        l.raw_log,
        l.result,
        l.is_unusual_ip,
        v.ueba_score,
        v.ueba_risk_level
    FROM {settings.clickhouse_table} AS l
    LEFT JOIN (
        SELECT
            source_log_id,
            argMax(ueba_score, validated_at) AS ueba_score,
            argMax(ueba_risk_level, validated_at) AS ueba_risk_level
        FROM ueba_validation_results
        WHERE source_log_id > 0
        GROUP BY source_log_id
    ) AS v ON v.source_log_id = l.id
    {where_clause}
    ORDER BY l.indexed_at DESC, l.timestamp DESC, l.id DESC
    LIMIT {safe_limit}
    """
    try:
        result = client.query(query, parameters=parameters)
        logs = []
        for row in result.result_rows:
            local_time = row[0]
            log_type_val = _format_realtime_log_type(row[1])
            username = row[2] or "未知"
            source_ip = row[3] or "未知"
            action = row[4] or ""
            city = row[5] if len(row) > 5 and row[5] else "未知"
            risk_score = row[6] if row[6] is not None else None
            event_type = row[7] if len(row) > 7 else ""
            raw_log = row[8] if len(row) > 8 else ""
            result_value = row[9] if len(row) > 9 else ""
            is_unusual_ip = row[10] if len(row) > 10 else False
            ueba_score = row[11] if len(row) > 11 else None
            ueba_risk_level = row[12] if len(row) > 12 else None

            # 状态判断
            if event_type == "LOGIN_SUCCESS":
                status = "✅ 成功"
            elif event_type == "LOGIN_FAIL":
                status = "❌ 失败"
            elif action in ("LOGIN", "LOGOUT", "API_CALL"):
                status = "✅ 成功"
            else:
                status = "❓ 未知"

            effective_score = ueba_score
            effective_level = ueba_risk_level
            if effective_score is None and not effective_level:
                effective_score = risk_score
            if effective_score is None and not effective_level:
                effective_score = _fallback_realtime_risk_score(raw_log, event_type, result_value, is_unusual_ip)
            risk = _risk_label_from_score(effective_score, effective_level)

            logs.append({
                "时间": local_time.strftime("%Y-%m-%d %H:%M:%S") if local_time else "",
                "类型": log_type_val,
                "用户": username,
                "IP": source_ip,
                "状态": status,
                "地点": city,
                "风险": risk
            })
        if logs:
            logger.info(f"从 ClickHouse 获取 {len(logs)} 条真实日志")
            return logs
        else:
            logger.info("ClickHouse 中无数据，使用模拟数据")
            return get_sample_logs(log_type)
    except Exception as e:
        logger.error(f"查询 {settings.clickhouse_table} 失败: {e}")
        return get_sample_logs(log_type)
    finally:
        if client is not None:
            client.close()


def fetch_anomaly_users(time_range="最近 24 小时", limit=10):
    """从 ClickHouse 获取真实异常用户数据"""
    client = get_clickhouse_client()
    
    time_map = {
        "最近 24 小时": 24,
        "最近 7 天": 168,
        "最近 30 天": 720
    }
    hours = time_map.get(time_range, 24)
    try:
        client = clickhouse_connect.get_client(
            host=settings.clickhouse_host,
            port=settings.clickhouse_port,
            username=settings.clickhouse_user,
            password=settings.clickhouse_password,
            database=settings.clickhouse_database,
            connect_timeout=5
        )
    except Exception as e:
        logger.error(f"ClickHouse 连接失败: {e}")
        return get_sample_anomaly_users()

    query = f"""
    SELECT
        username,
        max(anomaly_score) as max_score,
        count() as anomaly_count,
        max(detection_time) as last_time
    FROM anomaly_detection
    WHERE detection_time >= now() - INTERVAL {hours} HOUR
    GROUP BY username
    ORDER BY max_score DESC
    LIMIT {limit}
    """
    try:
        result = client.query(query)
        users = []
        for i, row in enumerate(result.result_rows, 1):
            users.append({
                "排名": i,
                "用户名": row[0],
                "异常评分": round(row[1], 2),
                "风险等级": "🔴 高危" if row[1] >= 0.8 else ("🟠 中危" if row[1] >= 0.5 else "🟡 低危"),
                "异常事件数": row[2],
                "最近异常时间": row[3].strftime("%Y-%m-%d %H:%M") if row[3] else ""
            })
        client.close()
        return users
    except Exception as e:
        logger.error(f"查询 anomaly_detection 失败: {e}")
        return get_sample_anomaly_users()


def fetch_security_metrics():
    """从 ClickHouse 获取安全指标（基于 logs_structured + ueba_validation_results）"""
    client = get_clickhouse_client()
    try:
        # 总日志量
        total = client.query("SELECT count() FROM log_analysis.logs_structured").result_rows[0][0]

        # 今日异常事件（从 ueba_validation_results 取 HIGH+CRITICAL）
        anomaly = client.query("""
            SELECT count() FROM log_analysis.ueba_validation_results
            WHERE ueba_risk_level IN ('HIGH','CRITICAL')
        """).result_rows[0][0]
        anomaly_count = int(anomaly or 0)

        # 高危用户数
        high = client.query("""
            SELECT uniqExact(username) FROM log_analysis.ueba_validation_results
            WHERE ueba_risk_level IN ('HIGH','CRITICAL')
        """).result_rows[0][0]
        high_risk_count = int(high or 0)

        # 总用户数
        users = client.query("SELECT uniqExact(username) FROM log_analysis.logs_structured WHERE username != ''").result_rows[0][0]
        total_users = int(users or 1)

        # 安全评分 = 100 - (高危比例 * 50)
        security_score = max(0, 100 - int(high_risk_count / max(total_users, 1) * 50))

        client.close()
        return {
            "security_score": security_score,
            "anomaly_count": anomaly_count,
            "high_risk_count": high_risk_count,
            "disposed_count": 0,
        }
    except Exception as e:
        logger.error(f"获取安全指标失败: {e}")
        client.close()
        return {"security_score": 75, "anomaly_count": 0, "high_risk_count": 0, "disposed_count": 0}


def fetch_security_trend(days=7):
    """从 ueba_validation_results 获取每日风险占比趋势"""
    client = get_clickhouse_client()
    try:
        query = f"""
            SELECT toDate(timestamp) AS d,
                   countIf(ueba_risk_level IN ('HIGH','CRITICAL')) AS high_cnt,
                   count() AS total
            FROM log_analysis.ueba_validation_results
            WHERE timestamp >= now() - INTERVAL {days} DAY
            GROUP BY d
            ORDER BY d
        """
        result = client.query(query)
        data = {"日期": [], "安全评分": []}
        date_list = [(datetime.now() - timedelta(days=i)).date() for i in range(days-1, -1, -1)]
        row_dict = {}
        for row in result.result_rows:
            row_dict[row[0]] = (int(row[1] or 0), int(row[2] or 0))
        for d in date_list:
            data["日期"].append(d.strftime("%Y-%m-%d"))
            h, t = row_dict.get(d, (0, 1))
            score = max(0, 100 - int(h / max(t, 1) * 80))
            data["安全评分"].append(score)
        client.close()
        return pd.DataFrame(data)
    except Exception:
        client.close()
        return pd.DataFrame({"日期": [], "安全评分": []})


def fetch_risk_distribution():
    """从 ueba_validation_results 获取风险等级分布"""
    client = get_clickhouse_client()
    try:
        query = """
            SELECT ueba_risk_level, count()
            FROM log_analysis.ueba_validation_results
            GROUP BY ueba_risk_level
            ORDER BY count() DESC
        """
        result = client.query(query)
        data = {"风险等级": [], "事件数": []}
        level_map = {"CRITICAL": "🔴 CRITICAL", "HIGH": "🟠 HIGH", "MEDIUM": "🟡 MEDIUM", "LOW": "🟢 LOW"}
        for row in result.result_rows:
            data["风险等级"].append(level_map.get(row[0], row[0]))
            data["事件数"].append(int(row[1] or 0))
        client.close()
        return pd.DataFrame(data)
    except Exception:
        client.close()
        return pd.DataFrame({"风险等级": [], "事件数": []})

def fetch_threat_stats():
    """从 ueba_validation_results 按 ueba_risk_level 统计替代威胁类型"""
    client = get_clickhouse_client()
    try:
        query = """
            SELECT ueba_risk_level, count()
            FROM log_analysis.ueba_validation_results
            WHERE ueba_risk_level IN ('HIGH','CRITICAL')
            GROUP BY ueba_risk_level
            ORDER BY count() DESC
        """
        result = client.query(query)
        data = {"威胁类型": [], "数量": []}
        for row in result.result_rows:
            data["威胁类型"].append(row[0] or "未知")
            data["数量"].append(int(row[1] or 0))
        client.close()
        return pd.DataFrame(data)
    except Exception:
        client.close()
        return pd.DataFrame({"威胁类型": [], "数量": []})


def fetch_ai_suggestions(status_filter="全部", risk_filter="全部"):
    """从 ClickHouse 获取真实 AI 处置建议（从 anomaly_detection 表读取）"""
    client = get_clickhouse_client()
    query = """
        SELECT id, username, threat_type, risk_level, description,
               ai_analysis, `处置建议`, anomaly_score, is_processed, detection_time
        FROM anomaly_detection
        WHERE 1=1
    """
    params = {}
    if risk_filter != "全部":
        risk_map = {"🔴 高危": "HIGH", "🟠 中危": "MEDIUM", "🟡 低危": "LOW"}
        db_risk = risk_map.get(risk_filter)
        if db_risk:
            query += " AND risk_level = %(risk_level)s"
            params['risk_level'] = db_risk
    if status_filter != "全部":
        if status_filter == "待处置":
            query += " AND is_processed = 0"
        elif status_filter == "已处置":
            query += " AND is_processed = 1"
        else:
            # 处置中、误报暂不支持
            return []
    query += " ORDER BY detection_time DESC LIMIT 100"
    try:
        result = client.query(query, parameters=params)
        suggestions = []
        for row in result.result_rows:
            confidence = int(row[7] * 100) if row[7] else 0
            suggestions.append({
                "id": row[0],
                "用户": row[1],
                "威胁类型": row[2] or "未知",
                "风险等级": _risk_level_to_icon(row[3]),
                "异常描述": row[4] or "",
                "AI 分析": row[5] or "暂无 AI 分析",
                "处置建议": row[6] or "请人工审查",
                "置信度": f"{confidence}%",
                "处置状态": "已处置" if row[8] else "待处置",
                "生成时间": row[9].strftime("%Y-%m-%d %H:%M") if row[9] else ""
            })
        client.close()
        return suggestions
    except Exception as e:
        logger.error(f"查询 anomaly_detection 失败: {e}")
        return []

def _risk_level_to_icon(level: str) -> str:
    """将数据库中的风险等级转换为前端图标"""
    level = level.upper() if level else ""
    if level == "CRITICAL" or level == "HIGH":
        return "🔴 高危"
    elif level == "MEDIUM":
        return "🟠 中危"
    elif level == "LOW":
        return "🟡 低危"
    else:
        return "🟡 低危"


def fetch_history_logs(start_time=None, end_time=None, username=None, source_ip=None,
                       log_type="全部", status="全部"):
    """从 ClickHouse 搜索真实历史日志（修复参数绑定和风险评分）"""
    client = get_clickhouse_client()
    params: Dict[str, Any] = {}
    conditions = ["username != ''"]

    if start_time:
        if isinstance(start_time, datetime):
            start_str = start_time.strftime("%Y-%m-%d")
        else:
            start_str = str(start_time)
        conditions.append(f"timestamp >= '{start_str} 00:00:00'")
    if end_time:
        if isinstance(end_time, datetime):
            end_str = end_time.strftime("%Y-%m-%d")
        else:
            end_str = str(end_time)
        conditions.append(f"timestamp < '{end_str} 23:59:59'")
    if username:
        conditions.append("username = %(user)s")
        params["user"] = username
    if source_ip:
        conditions.append("source_ip = %(ip)s")
        params["ip"] = source_ip
    if log_type and log_type != "全部":
        conditions.append("log_type = %(ltype)s")
        params["ltype"] = log_type
    if status and status != "全部":
        if status in ("SUCCESS", "成功"):
            conditions.append("result = 'SUCCESS'")
        elif status in ("FAIL", "失败"):
            conditions.append("(result = 'FAIL' OR event_type LIKE '%FAIL%')")
        elif status == "WARNING":
            conditions.append("result = 'WARNING'")

    query = f"""
        SELECT
            timestamp, username, log_type, source_ip, result, src_city,
            is_off_hours, is_unusual_ip, event_type
        FROM log_analysis.logs_structured
        WHERE {' AND '.join(conditions)}
        ORDER BY timestamp DESC
        LIMIT 200
    """
    try:
        result = client.query(query, parameters=params)
        logs = []
        for row in result.result_rows:
            # 启发式风险
            is_fail = str(row[4] or "").upper() in ("FAIL", "FAILED") or "FAIL" in str(row[8] or "").upper()
            is_off = row[6] in (1, True)
            is_unusual = row[7] in (1, True)
            risk_score_val = 0
            if is_fail:
                risk_score_val += 40
            if is_unusual:
                risk_score_val += 30
            if is_off:
                risk_score_val += 20
            risk = "🔴 高危" if risk_score_val >= 60 else "🟠 中危" if risk_score_val >= 30 else "🟢 正常"

            logs.append({
                "时间": row[0].strftime("%Y-%m-%d %H:%M:%S") if row[0] else "-",
                "用户": row[1] or "-",
                "类型": row[2] or "-",
                "IP": row[3] or "-",
                "状态": "❌ 失败" if is_fail else "✅ 成功",
                "地点": row[5] or "-",
                "风险等级": risk,
            })
        client.close()
        return logs
    except Exception as e:
        logger.error(f"查询历史日志失败: {e}")
        client.close()
        return []
        return get_sample_search_results()

def get_recent_usernames_from_clickhouse(limit: int = 20) -> List[str]:
    """从 logs_structured 表获取最近有活动的用户名"""
    import clickhouse_connect
    try:
        client = clickhouse_connect.get_client(
            host=settings.clickhouse_host,
            port=settings.clickhouse_port,
            username=settings.clickhouse_user,
            password=settings.clickhouse_password,
            database=settings.clickhouse_database,
            connect_timeout=5
        )
    except Exception as e:
        logger.error(f"ClickHouse 连接失败: {e}")
        return ["zhangsan", "lisi", "wangwu", "zhaoliu", "sunqi"]

    query = f"""
    SELECT DISTINCT username
    FROM {settings.clickhouse_table}
    WHERE username != ''
    ORDER BY max(timestamp) DESC
    LIMIT {limit}
    """
    try:
        result = client.query(query)
        usernames = [row[0] for row in result.result_rows if row[0]]
        client.close()
        return usernames
    except Exception as e:
        logger.error(f"获取真实用户名失败: {e}")
        return ["zhangsan", "lisi", "wangwu", "zhaoliu", "sunqi"]

# ==================== 数据访问层（统一入口） ====================

def get_realtime_logs(log_type="全部", limit=100):
    """获取实时日志数据（统一入口）"""
    if STORAGE_AVAILABLE:
        try:
            data = fetch_realtime_logs(log_type, limit)
            logger.info(f"📡 当前显示: 实时数据 - 从 ClickHouse 获取 {len(data)} 条日志")
            return data
        except Exception as e:
            logger.error(f"❌ 获取实时日志失败: {e}")
    
    logger.info("📡 当前显示: 模拟数据 - 实时日志")
    return get_sample_logs(log_type)


def get_anomaly_users(time_range="最近 24 小时", limit=10):
    """获取异常用户数据（统一入口）"""
    if STORAGE_AVAILABLE:
        try:
            data = fetch_anomaly_users(time_range, limit)
            logger.info(f"👥 当前显示: 实时数据 - 从 ClickHouse 获取 {len(data)} 个异常用户")
            return data
        except Exception as e:
            logger.error(f"❌ 获取异常用户失败: {e}")
    
    logger.info("👥 当前显示: 模拟数据 - 异常用户排行")
    return get_sample_anomaly_users()


def get_ueba_ranking_from_clickhouse(time_range: str = "最近 24 小时", limit: int = 10) -> Dict[str, Any]:
    """从 ClickHouse 获取用户风险排行。

    优先查 ueba_validation_results（UEBA 验证后的真实评分），
    无数据时降级到 logs_structured 做启发式评分（失败次数/非活跃时段/异常IP）。
    """
    import clickhouse_connect
    time_map = {
        "最近 24 小时": 24,
        "最近 7 天": 24 * 7,
        "最近 30 天": 24 * 30,
    }
    end_dt = datetime.now()
    start_dt = end_dt - timedelta(hours=time_map.get(time_range, 24))
    return (
        start_dt.strftime("%Y-%m-%d %H:%M:%S"),
        end_dt.strftime("%Y-%m-%d %H:%M:%S"),
    )


def _format_behavior_error(error: Any) -> str:
    """把 behavior API 的错误结构转为页面可展示文本。"""
    if isinstance(error, dict):
        return str(error.get("message") or error.get("code") or "未知错误")
    if error:
        return str(error)
    return "未知错误"


UEBA_RISK_OPTION_CODES = {
    "🔴 高危": {"HIGH", "CRITICAL"},
    "🟠 中危": {"MEDIUM"},
    "🟡 低危": {"LOW"},
}


def _resolve_ueba_time_window(time_range: str) -> tuple[str, str]:
    """将页面时间范围选项转为 (start_time, end_time) 字符串。"""
    now = datetime.now()
    if time_range == "最近 24 小时":
        start = now - timedelta(hours=24)
    elif time_range == "最近 7 天":
        start = now - timedelta(days=7)
    else:  # 最近 30 天
        start = now - timedelta(days=30)
    return start.strftime("%Y-%m-%d %H:%M:%S"), now.strftime("%Y-%m-%d %H:%M:%S")


def _selected_ueba_risk_codes(risk_filter: list[str] | tuple[str, ...] | None) -> set[str]:
    """把 UEBA 页面风险多选映射为内部风险等级集合。"""
    if not risk_filter:
        return {"LOW", "MEDIUM", "HIGH", "CRITICAL"}
    selected: set[str] = set()
    for option in risk_filter:
        selected.update(UEBA_RISK_OPTION_CODES.get(option, set()))
    return selected or {"LOW", "MEDIUM", "HIGH", "CRITICAL"}


def _ueba_event_matches_risk(event: Dict[str, Any], selected_codes: set[str]) -> bool:
    """判断单条 UEBA 事件是否符合页面风险筛选。"""
    return str(event.get("ueba_risk_level") or "").upper() in selected_codes


def _get_behavior_clickhouse_client():
    """复用 dashboard 现有 ClickHouse 配置创建 behavior API client。"""
    return clickhouse_connect.get_client(
        host=settings.clickhouse_host,
        port=settings.clickhouse_port,
        username=settings.clickhouse_user,
        password=settings.clickhouse_password,
        database=settings.clickhouse_database,
        connect_timeout=10,
    )


def get_ueba_ranking_from_clickhouse(
    time_range: str = "最近 24 小时",
    limit: int = 10,
    risk_filter: list[str] | tuple[str, ...] | None = None,
) -> Dict[str, Any]:
    """从 ClickHouse 获取用户风险排行（validation 优先 → 启发式降级）。"""
    time_map = {"最近 24 小时": 24, "最近 7 天": 24 * 7, "最近 30 天": 24 * 30}
    hours = time_map.get(time_range, 24)
    client = None
    try:
        client = _get_behavior_clickhouse_client()
    except Exception as exc:
        logger.error(f"ClickHouse 连接失败: {exc}")
        return {"success": False, "ranking": [], "error": str(exc)}

    # --- 方案 A: 从 ueba_validation_results 读取真实评分 ---
    try:
        vr_query = f"""
        SELECT
            username,
            max(ueba_score) AS score,
            count() AS event_count,
            max(validated_at) AS last_event_time
        FROM {settings.clickhouse_database}.ueba_validation_results
        WHERE validated_at >= now() - INTERVAL {hours} HOUR
          AND username != ''
        GROUP BY username
        ORDER BY score DESC, event_count DESC
        LIMIT {limit}
        """
        vr_result = client.query(vr_query)
        if vr_result and vr_result.result_rows:
            ranking = []
            for idx, row in enumerate(vr_result.result_rows, start=1):
                username = str(row[0]) if row[0] else ""
                score = float(row[1]) if row[1] is not None else 0
                event_count = int(row[2]) if row[2] is not None else 0
                last_time = row[3]
                last_str = last_time.strftime("%Y-%m-%d %H:%M") if last_time else ""
                ranking.append({
                    "rank": idx,
                    "username": username,
                    "score": min(score / 100.0, 1.0),
                    "risk_level": _format_ueba_risk_level(score / 100.0),
                    "event_count": event_count,
                    "last_event_time": last_str,
                })
            client.close()
            logger.info(f"UEBA 排行从 validation 表获取 {len(ranking)} 条")
            return {"success": True, "ranking": ranking}
    except Exception as e:
        logger.debug(f"validation 表查询跳过: {e}")

    # --- 方案 B: 从 logs_structured 做启发式评分 ---
    try:
        query = f"""
        SELECT
            username,
            count() AS event_count,
            countIf(result = 'FAIL' OR result = 'fail' OR event_type LIKE '%FAIL%') AS fail_cnt,
            countIf(is_off_hours = 1) AS off_hours_cnt,
            countIf(is_unusual_ip = 1) AS unusual_cnt,
            max(timestamp) AS last_event_time
        FROM {settings.clickhouse_database}.{settings.clickhouse_table}
        WHERE timestamp >= now() - INTERVAL {hours} HOUR
          AND username != ''
        GROUP BY username
        ORDER BY fail_cnt DESC, off_hours_cnt DESC, event_count DESC
        LIMIT {limit}
        """
        result = client.query(query)
        ranking = []
        for idx, row in enumerate(result.result_rows, start=1):
            username = str(row[0]) if row[0] else ""
            event_count = int(row[1]) if row[1] is not None else 0
            fail_cnt = int(row[2]) if row[2] is not None else 0
            off_hours_cnt = int(row[3]) if row[3] is not None else 0
            unusual_cnt = int(row[4]) if row[4] is not None else 0
            last_time = row[5]
            last_str = last_time.strftime("%Y-%m-%d %H:%M") if last_time else ""
            # 启发式评分: 0.1 base + 失败率权重 + 非活跃权重 + 异常IP权重
            heuristic = 0.1
            if event_count > 0:
                heuristic += (fail_cnt / event_count) * 0.5
                heuristic += (off_hours_cnt / event_count) * 0.25
                heuristic += (unusual_cnt / event_count) * 0.15
            score = min(heuristic, 1.0)
            ranking.append({
                "rank": idx,
                "username": username,
                "score": round(score, 4),
                "risk_level": _format_ueba_risk_level(score),
                "event_count": event_count,
                "last_event_time": last_str,
            })
        client.close()
        logger.info(f"UEBA 排行从 logs_structured 启发式获取 {len(ranking)} 条")
        return {"success": True, "ranking": ranking}
    except Exception as e:
        logger.error(f"启发式查询排行失败: {e}", exc_info=True)
        client.close()
        return {"success": False, "ranking": []}


def _format_ueba_risk_level(score: float) -> str:
    """将 0~1 风险分映射为英文等级（与 multiselect 选项一致）。"""
    if score >= 0.8:
        return "CRITICAL"
    if score >= 0.5:
        return "HIGH"
    if score >= 0.25:
        return "MEDIUM"
    return "LOW"


def _demo_ranking_to_rows(sample_data: Dict[str, List[Any]]) -> List[Dict[str, Any]]:
    """把原有 demo 字典转成统一排行行结构。"""
    return [
        {
            "rank": sample_data["排名"][index],
            "username": sample_data["用户名"][index],
            "score": sample_data["异常评分"][index],
            "risk_level": sample_data["风险等级"][index],
            "event_count": sample_data["异常事件数"][index],
            "last_event_time": sample_data["最近异常时间"][index],
        }
        for index in range(len(sample_data["用户名"]))
    ]


def _ranking_rows_to_dataframe(rows: List[Dict[str, Any]]) -> pd.DataFrame:
    """把统一排行结构转成原页面使用的中文列。"""
    return pd.DataFrame(
        [
            {
                "排名": row["rank"],
                "用户名": row["username"],
                "异常评分": row["score"],
                "风险等级": row["risk_level"],
                "异常事件数": row["event_count"],
                "最近异常时间": row["last_event_time"],
            }
            for row in rows
        ]
    )




def get_security_metrics():
    """获取安全指标数据（统一入口）"""
    if STORAGE_AVAILABLE:
        try:
            data = fetch_security_metrics()
            logger.info("🛡️ 当前显示: 实时数据 - 安全指标")
            return data
        except Exception as e:
            logger.error(f"❌ 获取安全指标失败: {e}")
    
    logger.info("🛡️ 当前显示: 模拟数据 - 安全指标")
    return get_sample_security_metrics()


def get_security_trend(days=7):
    """获取安全评分趋势（统一入口）"""
    if STORAGE_AVAILABLE:
        try:
            data = fetch_security_trend(days)
            logger.info(f"📈 当前显示: 实时数据 - 安全评分趋势 ({days}天)")
            return data
        except Exception as e:
            logger.error(f"❌ 获取安全趋势失败: {e}")
    
    logger.info("📈 当前显示: 模拟数据 - 安全评分趋势")
    return get_sample_security_trend(days)


def get_risk_distribution():
    """获取风险等级分布（统一入口）"""
    if STORAGE_AVAILABLE:
        try:
            data = fetch_risk_distribution()
            logger.info("⚠️ 当前显示: 实时数据 - 风险分布")
            return data
        except Exception as e:
            logger.error(f"❌ 获取风险分布失败: {e}")
    
    logger.info("⚠️ 当前显示: 模拟数据 - 风险分布")
    return get_sample_risk_distribution()


def get_threat_stats():
    """获取威胁类型统计（统一入口）"""
    if STORAGE_AVAILABLE:
        try:
            data = fetch_threat_stats()
            logger.info("🎯 当前显示: 实时数据 - 威胁类型统计")
            return data
        except Exception as e:
            logger.error(f"❌ 获取威胁统计失败: {e}")
    
    logger.info("🎯 当前显示: 模拟数据 - 威胁类型统计")
    return get_sample_threat_stats()


def get_ai_suggestions(status_filter="全部", risk_filter="全部"):
    """获取 AI 处置建议（统一入口）"""
    # 直接调用 ClickHouse 查询，不使用 STORAGE_AVAILABLE 标志
    try:
        data = fetch_ai_suggestions(status_filter, risk_filter)
        if data:
            logger.info(f"🤖 当前显示: 实时数据 - 从 ClickHouse 获取 {len(data)} 条 AI 建议")
            return data
        # ClickHouse 无数据时降级到模拟数据
        logger.info("🤖 当前显示: 模拟数据 - AI 处置建议 (ClickHouse 无数据)")
        return get_sample_ai_suggestions(status_filter, risk_filter)
    except Exception as e:
        logger.error(f"❌ 获取 AI 建议失败: {e}")
        # 对于无法映射的状态（处置中、误报），直接返回空列表，不显示 demo
        if status_filter in ("处置中", "误报"):
            logger.info(f"状态 '{status_filter}' 暂不支持，返回空列表")
            return []
        logger.info("🤖 当前显示: 模拟数据 - AI 处置建议 (降级)")
        return get_sample_ai_suggestions(status_filter, risk_filter)


def _get_heuristic_anomaly_events(
    start_time: str, end_time: str, limit: int = 20,
) -> List[Dict[str, Any]]:
    """从 logs_structured 表做启发式异常检测（UEBA 验证未运行时降级使用）。

    检测失败事件、非活跃时段事件和异常IP事件作为候选异常。
    """
    import clickhouse_connect
    try:
        ch = clickhouse_connect.get_client(
            host=settings.clickhouse_host, port=settings.clickhouse_port,
            username=settings.clickhouse_user, password=settings.clickhouse_password,
            database=settings.clickhouse_database,
        )
        # 注意：不能用 %(param)s 参数风格 + LIKE '%FAIL%' 混用，
        # clickhouse-connect 内部用 Python % 格式化导致冲突。
        # 时间参数直接 f-string 内插（值已在调用方用 strftime 格式化）
        query = f"""
        SELECT
            id, timestamp, username, source_ip, src_country, src_city,
            destination_ip, vpn_gateway, action, event_type, result,
            auth_method, client_software, protocol, is_off_hours, is_unusual_ip,
            raw_log
        FROM log_analysis.logs_structured
        WHERE timestamp >= '{start_time}' AND timestamp < '{end_time}'
          AND username != ''
          AND (result = 'FAIL' OR event_type LIKE '%FAIL%' OR is_off_hours = 1 OR is_unusual_ip = 1)
        ORDER BY timestamp DESC
        LIMIT {limit}
        """
        result = ch.query(query)
        ch.close()
        events = []
        for row in result.result_rows:
            cols = [c for c in result.column_names]
            ev = dict(zip(cols, row))
            ev["ueba_score"] = 45
            ev["ueba_risk_level"] = "MEDIUM"
            ev["validation_status"] = "启发式检测"
            ev["ueba_anomaly_reasons"] = []
            reasons = []
            if str(ev.get("result", "")).upper() in ("FAIL", "FAILED"):
                reasons.append("登录失败")
            if ev.get("is_off_hours") in (1, True):
                reasons.append("非活跃时段")
            if ev.get("is_unusual_ip") in (1, True):
                reasons.append("异常来源IP")
            ev["ueba_anomaly_reasons"] = reasons
            events.append(ev)
        return events
    except Exception as e:
        logger.error(f"启发式异常检测失败: {e}")
        return []


def get_ueba_ai_suggestions(
    time_range: str = "最近 7 天",
    risk_filter: str = "全部",
    status_filter: str = "全部",
) -> List[Dict[str, Any]]:
    """从 UEBA 用户基线分析结果获取异常事件，调用 AI 分析后返回处置建议。

    流程：
    1. 通过 behavior api 获取异常事件列表（或降级到启发式检测）
    2. 对每条异常事件调用 AI analyzer（使用 prompt_templates.py 模板）分析
    3. 将分析结果格式化为前端展示结构
    """
    time_map = {
        "最近 24 小时": 24,
        "最近 7 天": 24 * 7,
        "最近 30 天": 24 * 30,
    }
    hours = time_map.get(time_range, 24 * 7)
    start_time = (datetime.now() - timedelta(hours=hours)).strftime("%Y-%m-%d %H:%M:%S")
    end_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    # 1. 从 behavior api 获取异常事件
    try:
        behavior_result = analyze_behavior_from_clickhouse(
            start_time=start_time,
            end_time=end_time,
        )
    except Exception as e:
        logger.error(f"从 behavior api 获取异常事件失败: {e}")
        return []

    if not behavior_result.get("success"):
        logger.warning(f"behavior 分析失败: {behavior_result.get('error', '未知错误')}")
        return []

    events = behavior_result.get("events", [])
    if not events:
        logger.info("UEBA 验证无异常事件，降级到 logs_structured 启发式异常检测")
        events = _get_heuristic_anomaly_events(
            start_time=start_time,
            end_time=end_time,
            limit=20,
        )
        if not events:
            logger.info("启发式检测也无异常事件")
            return []

    # 2. 批量调用 AI 分析（最多分析 5 条以控制延迟，其余跳过）
    MAX_AI_EVENTS = 5
    ai_analyzer = get_ai_analyzer()
    suggestions = []

    for idx, event in enumerate(events):
        needs_ai = idx < MAX_AI_EVENTS
        username = event.get("username", "未知")
        risk_level = event.get("ueba_risk_level", "LOW")
        score = event.get("ueba_score", 0)
        reasons = event.get("ueba_anomaly_reasons", [])
        event_time = event.get("timestamp", "")
        source_ip = event.get("source_ip", "-")
        location = event.get("location", "-")

        reason_str = ", ".join(str(r) for r in reasons) if reasons else "行为偏离基线"
        anomaly_description = (
            f"用户 {username} 在 {event_time} 触发异常：{reason_str}。"
            f"来源IP: {source_ip}，地点: {location}，风险评分: {score}，风险等级: {risk_level}。"
        )

        # 调用 AI 分析（仅前 MAX_AI_EVENTS 条调 API，其余跳过）
        ai_result = None
        if ai_analyzer is not None and needs_ai:
            try:
                ai_result = ai_analyzer.analyze_anomaly(
                    username=username,
                    anomaly_description=anomaly_description,
                )
            except Exception as e:
                logger.error(f"AI 分析失败 (user={username}): {e}")
                ai_result = None

        # 风险等级映射
        risk_icon = _risk_level_to_icon(risk_level)

        # 处置状态映射
        validation_status = event.get("validation_status", "")
        if validation_status == "VALIDATED":
            dispose_status = "待处置"
        elif validation_status == "NO_BASELINE":
            dispose_status = "无基线"
        elif validation_status == "UNRELIABLE_BASELINE":
            dispose_status = "基线不可靠"
        else:
            dispose_status = "待处置"

        suggestion = {
            "id": idx + 1,
            "用户": username,
            "威胁类型": ai_result.get("threat_type", "待分析") if ai_result else "待分析",
            "风险等级": risk_icon,
            "异常描述": anomaly_description,
            "AI 分析": ai_result.get("description", "暂无 AI 分析") if ai_result else "暂无 AI 分析",
            "处置建议": ai_result.get("suggestion", "请人工审查") if ai_result else "请人工审查",
            "置信度": f"{min(int(score), 100)}%" if score else "-",
            "处置状态": dispose_status,
            "生成时间": str(event_time)[:19] if event_time else "-",
            "_raw_event": event,
        }
        suggestions.append(suggestion)

    # 3. 前端过滤
    filtered = []
    for s in suggestions:
        if risk_filter != "全部" and s["风险等级"] != risk_filter:
            continue
        if status_filter != "全部" and s["处置状态"] != status_filter:
            continue
        filtered.append(s)

    return filtered


def search_history_logs(start_time=None, end_time=None, username=None, source_ip=None, 
                        log_type="全部", status="全部"):
    """搜索历史日志（统一入口）

    优先从 ClickHouse 获取真实数据。当有 ClickHouse 连接但无数据时，
    返回空列表（不再降级到硬编码模拟数据）。
    """
    if STORAGE_AVAILABLE:
        try:
            data = fetch_history_logs(start_time, end_time, username, source_ip, log_type, status)
            if data:
                logger.info(f"🔍 当前显示: 实时数据 - 从 ClickHouse 获取 {len(data)} 条")
                return data
            logger.info("🔍 ClickHouse 无匹配记录，返回空列表")
            return []
        except Exception as e:
            logger.error(f"❌ 搜索历史日志失败: {e}")

    logger.info("🔍 存储模块不可用，返回空列表")
    return []


def create_sidebar():
    """创建侧边栏导航（卡片式）"""
    with st.sidebar:
        # 品牌头
        st.markdown("""
        <div style="background:linear-gradient(135deg,#1a237e,#3949ab);padding:1rem 1rem;border-radius:10px;text-align:center;margin-bottom:1rem;">
            <div style="font-size:2rem;">🔍</div>
            <div style="color:white;font-weight:700;font-size:1rem;margin-top:0.2rem;">日志分析 AI 助手</div>
            <div style="color:rgba(255,255,255,0.6);font-size:0.7rem;">UEBA + AI 安全分析平台</div>
        </div>
        """, unsafe_allow_html=True)

        # 导航菜单
        pages = [
            ("实时日志流", "📡"),
            ("UEBA 异常排行", "👥"),
            ("安全评分看板", "🛡️"),
            ("AI分析+强化基线", "🧠"),
            ("历史查询", "🔍"),
        ]

        for page, icon in pages:
            active = st.session_state.current_page == page
            btn_type = "primary" if active else "secondary"
            if st.button(
                f"{icon} {page}",
                use_container_width=True,
                type=btn_type,
                key=f"nav_{page}",
            ):
                st.session_state.current_page = page
                st.rerun()

        st.markdown("<hr style='margin:0.8rem 0;border-color:#eee;'>", unsafe_allow_html=True)

        # 系统状态
        st.markdown("<div style='font-size:0.85rem;font-weight:600;color:#555;margin-bottom:0.4rem;'>📊 系统概览</div>", unsafe_allow_html=True)

        try:
            ch = get_clickhouse_client()
            cnt = ch.query("SELECT count() FROM log_analysis.logs_structured")
            total_logs = cnt.result_rows[0][0] if cnt.result_rows else "N/A"
            ch.close()
        except Exception:
            total_logs = "—"

        st.metric("日志总量", f"{total_logs:,}" if isinstance(total_logs, int) else total_logs)
        st.metric("数据源", st.session_state.get("data_source", "模拟数据"))
        st.metric("数据保留", "90 天")

        st.markdown("<hr style='margin:0.8rem 0;border-color:#eee;'>", unsafe_allow_html=True)
        st.caption(f"v1.2.0 · {datetime.now().strftime('%Y-%m-%d')}")


def _render_realtime_log_list(log_type: str, is_running: bool) -> None:
    """渲染实时日志列表；供 fragment 周期重跑。"""
    st.subheader("📋 日志列表")

    logs_data = get_realtime_logs(log_type)
    df_logs = pd.DataFrame(logs_data)
    st.dataframe(df_logs, use_container_width=True, height=400)

    if is_running:
        st.success("🔄 实时刷新中... 上次更新: " + datetime.now().strftime("%H:%M:%S"))
    else:
        st.warning("⏸️ 已暂停刷新")


def show_realtime_logs():
    """显示实时日志流"""
    # 控制栏 — 一行紧凑布局
    ctrl = st.columns([2, 2, 2, 1])
    with ctrl[0]:
        log_type = st.selectbox("日志类型", ["全部", "VPN 登录", "API 调用", "系统日志"], label_visibility="collapsed")
    with ctrl[1]:
        is_running = st.toggle("🔄 实时刷新", value=True)
    with ctrl[2]:
        refresh_rate = st.selectbox("刷新频率", ["5 秒", "10 秒", "30 秒"], label_visibility="collapsed")
    with ctrl[3]:
        st.markdown("<br>", unsafe_allow_html=True)
        if st.button("🔄 刷新", use_container_width=True):
            st.rerun()

    # 实时日志表格
    logs_data = get_realtime_logs(log_type)
    df_logs = pd.DataFrame(logs_data)
    st.dataframe(df_logs, use_container_width=True, height=380)

    # 状态行
    status_icon = "🟢" if is_running else "⏸️"
    st.caption(f"{status_icon} 上次更新: {datetime.now().strftime('%H:%M:%S')}  ·  共 {len(logs_data)} 条")

    # 指标行 — 从 ClickHouse 实时查询
    try:
        import clickhouse_connect
        _ch = clickhouse_connect.get_client(
            host=settings.clickhouse_host, port=settings.clickhouse_port,
            username=settings.clickhouse_user, password=settings.clickhouse_password,
            database=settings.clickhouse_database,
        )
        _total = _ch.query("SELECT count() FROM log_analysis.logs_structured").result_rows[0][0]
        _today_str = datetime.now().strftime("%Y-%m-%d")
        _today = _ch.query(f"SELECT count() FROM log_analysis.logs_structured WHERE toDate(timestamp) = '{_today_str}'").result_rows[0][0]
        _users = _ch.query(f"SELECT uniqExact(username) FROM log_analysis.logs_structured WHERE toDate(timestamp) = '{_today_str}' AND username != ''").result_rows[0][0]
        _high = _ch.query("SELECT count() FROM log_analysis.ueba_validation_results WHERE ueba_risk_level IN ('HIGH','CRITICAL')").result_rows[0][0]
        _ch.close()
    except Exception:
        _total, _today, _users, _high = "—", "—", "—", "—"

    st.markdown(f"""
    <div class="metric-group">
        <div class="metric-item"><div class="value">{_total}</div><div class="label">日志总量</div></div>
        <div class="metric-item"><div class="value">{_today}</div><div class="label">今日新增</div></div>
        <div class="metric-item"><div class="value">{_users}</div><div class="label">活跃用户</div></div>
        <div class="metric-item"><div class="value">{_high}</div><div class="label">高风险事件</div></div>
    </div>
    """, unsafe_allow_html=True)


def _rl_emoji(level: str) -> str:
    """英文风险等级 → 带 emoji 显示标签。"""
    mapping = {
        "CRITICAL": "🔴 CRITICAL",
        "HIGH": "🟠 HIGH",
        "MEDIUM": "🟡 MEDIUM",
        "LOW": "🟢 LOW",
    }
    return mapping.get(level.upper(), level)


def _rl_css_class(level: str) -> str:
    """英文风险等级 → CSS class。"""
    return level.lower() if level.upper() in ("CRITICAL", "HIGH", "MEDIUM", "LOW") else "low"


def show_ueba_ranking():
    """显示 UEBA 异常用户排行"""
    # 筛选栏
    col1, col2 = st.columns([1, 2])
    with col1:
        time_range = st.selectbox("时间范围", ["最近 24 小时", "最近 7 天", "最近 30 天"], index=2, label_visibility="collapsed")
    with col2:
        risk_filter = st.multiselect(
            "风险等级",
            ["CRITICAL", "HIGH", "MEDIUM", "LOW"],
            default=["CRITICAL", "HIGH", "MEDIUM", "LOW"],
            label_visibility="collapsed",
        )

    ranking_result = get_ueba_ranking_from_clickhouse(time_range, limit=20)
    if not ranking_result.get("success"):
        st.error(f"查询失败: {ranking_result.get('error', '未知错误')}")
        # 加调试输出
        try:
            import clickhouse_connect
            ch = clickhouse_connect.get_client(
                host=settings.clickhouse_host, port=settings.clickhouse_port,
                username=settings.clickhouse_user, password=settings.clickhouse_password,
                database=settings.clickhouse_database,
            )
            cnt = ch.query("SELECT count() FROM log_analysis.logs_structured").result_rows[0][0]
            ch.close()
            st.caption(f"调试: logs_structured 表共有 {cnt} 条记录")
        except Exception as e_debug:
            st.caption(f"调试: ClickHouse 连接失败 — {e_debug}")
        return

    ranking_rows = ranking_result["ranking"]
    if not ranking_rows:
        st.warning("当前时间窗口内无用户行为数据，请尝试选择「最近 30 天」")
        try:
            import clickhouse_connect
            ch = clickhouse_connect.get_client(
                host=settings.clickhouse_host, port=settings.clickhouse_port,
                username=settings.clickhouse_user, password=settings.clickhouse_password,
                database=settings.clickhouse_database,
            )
            cnt = ch.query("SELECT count() FROM log_analysis.logs_structured").result_rows[0][0]
            ch.close()
            st.caption(f"调试: logs_structured 表共有 {cnt} 条记录 (但查询时间内无匹配)")
        except Exception as e_debug:
            st.caption(f"调试: ClickHouse 连接失败 — {e_debug}")
        return

    # 应用风险等级筛选
    if risk_filter:
        ranking_rows = [r for r in ranking_rows if r.get("risk_level", "LOW").upper() in risk_filter]

    if not ranking_rows:
        st.info(f"当前筛选条件下无匹配用户（已选风险等级: {', '.join(risk_filter)}）")
        return

    # --- 用户排行卡片 ---
    st.markdown(f"<div style='font-size:0.95rem;font-weight:600;margin:0.5rem 0;'>🔴 异常用户 TOP{len(ranking_rows)}</div>", unsafe_allow_html=True)
    cards_html = ""
    for i, r in enumerate(ranking_rows[:10]):
        rl_raw = r.get("risk_level", "LOW")
        rl_css = _rl_css_class(rl_raw)
        rl_label = _rl_emoji(rl_raw)
        score = r.get("score", 0)
        score_pct = f"{score*100:.0f}" if isinstance(score, float) else str(score)
        cards_html += f"""
        <div class="risk-card {rl_css}">
            <div style="display:flex;justify-content:space-between;align-items:center;">
                <span class="card-title">#{i+1} {r.get('username', '-')}</span>
                <span class="badge {rl_css}">{rl_label}</span>
            </div>
            <div class="card-meta">
                评分 {score_pct}/100 · 事件 {r.get('event_count', 0)} 起 · 最近 {str(r.get('last_event_time', '-'))[:16]}
            </div>
        </div>
        """
    st.markdown(cards_html, unsafe_allow_html=True)

    # --- 用户行为详情 ---
    st.markdown("<div style='font-size:0.95rem;font-weight:600;margin:0.5rem 0;'>📋 用户行为分析详情</div>", unsafe_allow_html=True)

    real_usernames = [row["username"] for row in ranking_rows if row.get("username")]
    if not real_usernames:
        st.info("暂无用户数据")
        return

    selected_user = st.selectbox("选择用户", real_usernames, label_visibility="collapsed")
    behavior_result = analyze_behavior_from_clickhouse(
        username=selected_user,
        limit=100,
    )

    if not behavior_result.get("success"):
        st.caption(f"⚠️ 行为分析暂不可用: {behavior_result.get('error', '未知错误')}")
        return

    events = behavior_result.get("events", [])
    if not events:
        st.caption("ℹ️ 该用户暂无异常事件")
        return

    # 分页状态
    page_size = 10
    total_pages = max(1, (len(events) + page_size - 1) // page_size)
    page_key = f"ueba_page_{selected_user}"
    if page_key not in st.session_state:
        st.session_state[page_key] = 1
    current_page = st.session_state[page_key]
    if current_page > total_pages:
        current_page = 1
        st.session_state[page_key] = 1

    # 折叠展开状态（只允许同时展开一条）
    expand_key = f"ueba_expand_{selected_user}"
    if expand_key not in st.session_state:
        st.session_state[expand_key] = None

    start_idx = (current_page - 1) * page_size
    end_idx = min(start_idx + page_size, len(events))
    page_events = events[start_idx:end_idx]

    # 分页信息 + 跳转控件
    st.markdown(f"<div style='display:flex;justify-content:space-between;align-items:center;margin:0.3rem 0;font-size:0.85rem;color:var(--text-secondary);'><span>共 {len(events)} 条</span><span>第 {current_page}/{total_pages} 页</span></div>", unsafe_allow_html=True)

    if total_pages > 1:
        pg_cols = st.columns([1, 3, 1])
        with pg_cols[0]:
            if st.button("◀ 上一页", key=f"{page_key}_prev", disabled=(current_page <= 1), use_container_width=True):
                st.session_state[page_key] = max(1, current_page - 1)
                st.rerun()
        with pg_cols[1]:
            goto = st.number_input("跳转到", min_value=1, max_value=total_pages, value=current_page, key=f"{page_key}_input", label_visibility="collapsed")
            if goto != current_page:
                st.session_state[page_key] = goto
                st.rerun()
        with pg_cols[2]:
            if st.button("下一页 ▶", key=f"{page_key}_next", disabled=(current_page >= total_pages), use_container_width=True):
                st.session_state[page_key] = min(total_pages, current_page + 1)
                st.rerun()

    # 事件列表（可折叠，每次只展开一条）
    for idx, ev in enumerate(page_events):
        rl_raw = ev.get("ueba_risk_level", "LOW")
        rl_css = _rl_css_class(rl_raw)
        rl_label = _rl_emoji(rl_raw)
        ts = str(ev.get("timestamp", "-"))[:16]
        score = ev.get("ueba_score", "-")
        reasons = ev.get("ueba_anomaly_reasons", [])
        reason_text = "; ".join(str(r) for r in reasons) if reasons else "—"
        status = ev.get("validation_status", "-")
        is_expanded = (st.session_state[expand_key] == idx)

        # 折叠按钮（始终显示两行摘要）
        header_col1, header_col2 = st.columns([5, 1])
        with header_col1:
            btn_label = f"⚠️ {ts}  {rl_label}  评分 {score}  ·  {status}"
            st.markdown(f"""
            <div style="padding:0.4rem 0;font-size:0.9rem;cursor:pointer;border-bottom:1px solid #eee;">
                <span class="badge {rl_css}" style="margin-right:0.4rem;">{rl_label}</span>
                <strong>{ts}</strong>  评分 {score}  ·  {status}  ·  {reason_text[:60]}{'...' if len(reason_text)>60 else ''}
            </div>
            """, unsafe_allow_html=True)
        with header_col2:
            btn_key = f"ev_expand_{selected_user}_{start_idx + idx}"
            if st.button("🔼" if is_expanded else "🔽", key=btn_key, use_container_width=True):
                st.session_state[expand_key] = None if is_expanded else idx
                st.rerun()

        # 展开详情（仅当此条被选中时显示）
        if is_expanded:
            source_ip = ev.get("source_ip", "-")
            dest_ip = ev.get("destination_ip", "-")
            country = ev.get("source_country", ev.get("src_country", "-"))
            city = ev.get("source_city", ev.get("src_city", "-"))
            vpn = ev.get("vpn_gateway", "-")
            auth = ev.get("auth_method", "-")
            proto = ev.get("protocol", "-")
            action = ev.get("action", "-")
            event_type = ev.get("event_type", "-")
            result = ev.get("result", "-")

            st.markdown(f"""
            <div class="risk-card {rl_css}" style="margin-top:-0.3rem;">
                <div style="font-size:0.85rem;line-height:1.8;display:grid;grid-template-columns:1fr 1fr;gap:0.2rem 1rem;">
                    <div><strong>来源IP:</strong> {source_ip}</div>
                    <div><strong>目标IP:</strong> {dest_ip}</div>
                    <div><strong>国家:</strong> {country}</div>
                    <div><strong>城市:</strong> {city}</div>
                    <div><strong>VPN网关:</strong> {vpn}</div>
                    <div><strong>认证方式:</strong> {auth}</div>
                    <div><strong>协议:</strong> {proto}</div>
                    <div><strong>动作:</strong> {action}</div>
                    <div><strong>事件类型:</strong> {event_type}</div>
                    <div><strong>结果:</strong> {result}</div>
                    <div><strong>评分:</strong> {score}</div>
                    <div><strong>状态:</strong> {status}</div>
                </div>
                <div style="font-size:0.85rem;margin-top:0.3rem;color:var(--text-secondary);"><strong>异常原因:</strong> {reason_text}</div>
            </div>
            """, unsafe_allow_html=True)



def show_security_score():
    """显示安全评分看板"""
    # 指标行 (CSS metric-group 替代 st.columns + st.metric)
    metrics = get_security_metrics()
    score_color = "#d32f2f" if metrics["security_score"] < 50 else "#f57c00" if metrics["security_score"] < 75 else "#2e7d32"
    st.markdown(f"""
    <div style="display:flex;gap:1rem;flex-wrap:wrap;margin-bottom:1rem;">
        <div class="metric-item">
            <div class="score-ring" style="background:conic-gradient({score_color} {metrics['security_score']}%, #eee {metrics['security_score']}%);">
                <span style="background:#1a237e;width:80px;height:80px;border-radius:50%;display:flex;align-items:center;justify-content:center;color:white;">{metrics['security_score']}</span>
            </div>
            <div class="label">安全评分</div>
        </div>
        <div class="metric-item"><div class="value">{metrics['anomaly_count']}</div><div class="label">今日异常事件</div></div>
        <div class="metric-item"><div class="value">{metrics['high_risk_count']}</div><div class="label">高危用户数</div></div>
        <div class="metric-item"><div class="value">{metrics['disposed_count']}</div><div class="label">已处置事件</div></div>
    </div>
    """, unsafe_allow_html=True)

    # 趋势 + 分布
    trend_col, dist_col = st.columns([3, 2])
    with trend_col:
        st.markdown("<div style='font-size:0.9rem;font-weight:600;margin-bottom:0.3rem;'>📈 安全评分趋势（近7天）</div>", unsafe_allow_html=True)
        score_data = get_security_trend(days=7)
        st.line_chart(score_data.set_index("日期")["安全评分"], height=200)

    with dist_col:
        st.markdown("<div style='font-size:0.9rem;font-weight:600;margin-bottom:0.3rem;'>⚠️ 风险等级分布</div>", unsafe_allow_html=True)
        risk_data = get_risk_distribution()
        st.bar_chart(risk_data.set_index("风险等级"), height=200)

    # 威胁类型 + 日报
    threat_col, report_col = st.columns([1, 1])
    with threat_col:
        st.markdown("<div style='font-size:0.9rem;font-weight:600;margin-bottom:0.3rem;'>🎯 威胁类型统计</div>", unsafe_allow_html=True)
        threat_data = get_threat_stats()
        st.bar_chart(threat_data.set_index("威胁类型"), height=200)

    with report_col:
        st.markdown("<div style='font-size:0.9rem;font-weight:600;margin-bottom:0.3rem;'>📄 安全简报</div>", unsafe_allow_html=True)

        try:
            import clickhouse_connect
            ch = clickhouse_connect.get_client(
                host=settings.clickhouse_host, port=settings.clickhouse_port,
                username=settings.clickhouse_user, password=settings.clickhouse_password,
                database=settings.clickhouse_database,
            )
            total = ch.query("SELECT count() FROM logs_structured").result_rows[0][0]
            users = ch.query("SELECT uniq(username) FROM logs_structured WHERE username != ''").result_rows[0][0]
            validated = ch.query("SELECT count() FROM ueba_validation_results").result_rows[0][0]
            ch.close()
            brief = f"""
<div style="background:var(--bg-card);border-radius:10px;padding:1rem;box-shadow:var(--border-card);font-size:0.85rem;line-height:1.7;color:var(--text-primary);">
    📅 <strong>{datetime.now().strftime('%Y-%m-%d')}</strong><br>
    📊 日志总量: {total:,} 条<br>
    👥 活跃用户: {users} 人<br>
    ✅ 已评分事件: {int(validated or 0):,} 条<br>
    🚦 安全评分: {metrics['security_score']}/100<br>
    🔴 高危用户: {metrics['high_risk_count']} 人<br>
    🟠 异常事件: {metrics['anomaly_count']} 起
</div>
"""
        except Exception:
            brief = '<div style="color:#999;font-size:0.85rem;">暂无数据，请先采集日志并运行 UEBA。</div>'

        st.markdown(brief, unsafe_allow_html=True)


def show_ai_suggestions():
    # 筛选栏
    col1, col2, col3 = st.columns([1, 1, 1])
    with col1:
        time_range = st.selectbox("时间范围", ["最近 24 小时", "最近 7 天", "最近 30 天"], index=2, label_visibility="collapsed")
    with col2:
        risk_filter = st.selectbox("风险等级", ["全部", "CRITICAL", "HIGH", "MEDIUM", "LOW"], label_visibility="collapsed")
    with col3:
        status_filter = st.selectbox("处置状态", ["全部", "待处置", "无基线", "基线不可靠"], label_visibility="collapsed")

    # 缓存：相同筛选条件下 1 小时内复用结果（首次加载约 30~60 秒，后续秒开）
    cache_key = f"ai_cache"
    cache_meta_key = f"ai_cache_meta"
    CACHE_TTL_SEC = 3600

    use_cache = False
    if cache_key in st.session_state and cache_meta_key in st.session_state:
        ct, cr, crf, csf = st.session_state[cache_meta_key]
        if (datetime.now() - ct).seconds < CACHE_TTL_SEC and cr == time_range and crf == risk_filter and csf == status_filter:
            use_cache = True

    if use_cache:
        suggestions = st.session_state[cache_key]
    else:
        with st.spinner(f"正在分析异常事件 ({time_range})..."):
            try:
                suggestions = get_ueba_ai_suggestions(
                    time_range=time_range,
                    risk_filter=risk_filter,
                    status_filter=status_filter,
                )
            except Exception as e:
                logger.error(f"AI 分析异常: {e}")
                suggestions = []
        # 无论成功失败都存缓存，避免重复调用
        st.session_state[cache_key] = suggestions
        st.session_state[cache_meta_key] = (datetime.now(), time_range, risk_filter, status_filter)

    if not suggestions:
        st.info("当前时间窗口无异常事件 — 尝试选择「最近 30 天」扩大搜索范围，或确认已通过 `python -m src.main` 采集日志")
        return

    # ---- 用户级概览（按用户分组） ----
    from collections import defaultdict
    user_groups: dict[str, list] = defaultdict(list)
    for s in suggestions:
        user_groups[s.get("用户", "未知")].append(s)

    # 风险等级排序（兼容中文图标和英文）
    risk_score_map = {"🔴 高危": 0, "🟠 中危": 1, "🟡 低危": 2, "CRITICAL": 0, "HIGH": 1, "MEDIUM": 2, "LOW": 3}
    def _rl_score(rl: str) -> int:
        for k, v in risk_score_map.items():
            if k in str(rl).upper():
                return v
        return 9
    sorted_users = sorted(user_groups.keys(), key=lambda u: min(_rl_score(s.get("风险等级", "LOW")) for s in user_groups[u]))

    # 用户下拉筛选
    all_users_option = "全部用户"
    user_options = [all_users_option] + sorted_users
    sel_user = st.selectbox("👤 筛选用户", user_options, label_visibility="collapsed", key="ai_user_filter")

    # 统计数据
    total_users = len(user_groups)
    total_events = len(suggestions)
    st.markdown(f"<div style='display:flex;justify-content:space-between;font-size:0.85rem;color:var(--text-secondary);margin-bottom:0.5rem;'><span>共 {total_users} 个用户 · {total_events} 条异常事件</span></div>", unsafe_allow_html=True)

    # 展开状态
    expand_key = "ai_user_expand"
    if expand_key not in st.session_state:
        st.session_state[expand_key] = None

    for username in sorted_users:
        if sel_user != all_users_option and username != sel_user:
            continue

        items = user_groups[username]
        max_rl = min((_rl_score(s.get("风险等级", "LOW")) for s in items), default=9)
        rl_css = "critical" if max_rl <= 0 else "high" if max_rl <= 1 else "medium" if max_rl <= 2 else "low"
        rl_label = "CRITICAL" if max_rl <= 0 else "HIGH" if max_rl <= 1 else "MEDIUM" if max_rl <= 2 else "LOW"
        latest_ts = max(s.get("生成时间", "") for s in items)
        def _parse_conf(v: str) -> int:
            raw = str(v).replace("%", "").strip()
            return int(raw) if raw.isdigit() else 0
        latest_score = max((_parse_conf(s.get("置信度", "0")) for s in items), default=0)
        has_ai = any(s.get("AI 分析") and "暂无" not in s.get("AI 分析","") for s in items)
        is_expanded = (st.session_state[expand_key] == username)

        # 用户概览卡片（Streamlit 原生组件）
        with st.container():
            c1, c2, c3, c4 = st.columns([3, 1, 1, 1])
            with c1:
                st.markdown(f"**👤 {username}**  ")
            with c2:
                st.markdown(f"<span class='badge {rl_css}'>{rl_label}</span>  {len(items)} 条", unsafe_allow_html=True)
            with c3:
                if has_ai:
                    st.markdown("<span class='badge info'>🧬 已强化</span>", unsafe_allow_html=True)
            with c4:
                btn_label = "🔼" if is_expanded else "🔽"
                if st.button(btn_label, key=f"ai_u_{username}", use_container_width=True):
                    st.session_state[expand_key] = None if is_expanded else username
                    st.rerun()
        st.caption(f"最近 {latest_ts[:10]} · 最高评分 {latest_score}")

        # 展开后显示该用户的事件列表 + AI 分析
        if is_expanded:
            for ev in items[:10]:
                rl = "critical" if ev.get("风险等级") in ("CRITICAL",) else "high" if ev.get("风险等级") in ("HIGH",) else "medium"
                ts = ev.get("生成时间", "")[:16]
                threat = ev.get("威胁类型", "待分析")
                desc = ev.get("异常描述", "-")[:150]
                ai_txt = ev.get("AI 分析", "暂无")[:200]
                suggestion_txt = ev.get("处置建议", "请人工审查")[:200]
                score = ev.get("置信度", "-")

                st.markdown(f"""
                <div class="risk-card {rl}" style="margin:0.3rem 0 0.3rem 1.5rem;">
                    <div style="display:flex;justify-content:space-between;align-items:center;">
                        <span><span class="badge {rl}" style="margin-right:0.3rem;">{ev.get('风险等级','LOW')}</span> <strong>{ts}</strong> · {threat}</span>
                        <span style="font-size:0.8rem;color:var(--text-secondary);">评分 {score}</span>
                    </div>
                    <div style="font-size:0.83rem;margin-top:0.3rem;line-height:1.5;">
                        <div><strong>📝</strong> {desc}</div>
                        <div style="margin-top:0.15rem;"><strong>🧠</strong> {ai_txt}</div>
                        <div style="margin-top:0.15rem;"><strong>💡</strong> {suggestion_txt}</div>
                    </div>
                </div>
                """, unsafe_allow_html=True)

            if len(items) > 10:
                st.caption(f"... 还有 {len(items) - 10} 条事件")

    # 处置统计
    st.markdown("<hr style='margin:0.8rem 0;border-color:#eee;'>", unsafe_allow_html=True)
    stat_counts = {"待处置": 0, "无基线": 0, "基线不可靠": 0}
    for s in suggestions:
        sts = s.get("处置状态", "")
        if sts in stat_counts:
            stat_counts[sts] += 1
    st.markdown(f"""
    <div class="metric-group">
        <div class="metric-item"><div class="value">{stat_counts['待处置']}</div><div class="label">待处置</div></div>
        <div class="metric-item"><div class="value">{stat_counts['无基线']}</div><div class="label">无基线</div></div>
        <div class="metric-item"><div class="value">{stat_counts['基线不可靠']}</div><div class="label">基线不可靠</div></div>
        <div class="metric-item"><div class="value">{total_events}</div><div class="label">总计</div></div>
    </div>
    """, unsafe_allow_html=True)

    # ================================================================
    # AI 基线强化建议区块（合并到同一页面底部）
    # ================================================================
    st.markdown("<hr style='margin:1.2rem 0;border-color:#ddd;border-width:2px;'>", unsafe_allow_html=True)
    _show_baseline_reinforcement()


def _show_baseline_reinforcement():
    """展示 AI 基线强化建议（从 baseline_ai_refinements 表读取）。"""
    import clickhouse_connect
    try:
        ch = clickhouse_connect.get_client(
            host=settings.clickhouse_host, port=settings.clickhouse_port,
            username=settings.clickhouse_user, password=settings.clickhouse_password,
            database=settings.clickhouse_database,
        )
        rows = ch.query("""
            SELECT username, model_version, analysis_summary, pattern_type,
                   is_baseline_stale, stale_features, suggested_adjustments,
                   new_watch_features, reinforced_baseline_delta, confidence,
                   ai_platform, validated_at, anomaly_event_count
            FROM baseline_ai_refinements
            ORDER BY validated_at DESC
            LIMIT 20
        """)
        ch.close()
        refinements = []
        for row in rows.result_rows:
            refinements.append(dict(zip(rows.column_names, row)))
    except Exception:
        refinements = []

    # 按钮始终显示（即使无数据也能手动触发）
    st.caption("💡 AI 强化在 `python -m src.main` 启动时自动执行，也可点下方按钮手动触发")
    # 操作按钮行
    btn_cols = st.columns([1, 1, 3])
    with btn_cols[0]:
        if st.button("🔄 刷新数据", use_container_width=True):
            st.rerun()
    with btn_cols[1]:
        if st.button("🚀 启动 AI 强化", use_container_width=True, type="primary"):
            with st.spinner("正在调用 AI 分析所有高风险用户（约 30~60 秒）..."):
                try:
                    from src.ai.client import AIClient
                    from src.behavior.baseline_store import BaselineStore
                    from src.behavior.baseline_reinforcement import BaselineReinforcementService
                    import clickhouse_connect
                    ch = clickhouse_connect.get_client(
                        host=settings.clickhouse_host, port=settings.clickhouse_port,
                        username=settings.clickhouse_user, password=settings.clickhouse_password,
                        database=settings.clickhouse_database,
                    )
                    config = settings.current_ai_config
                    ai = AIClient(api_key=config["api_key"], platform=config["platform"], model=config.get("model"))
                    bs = BaselineStore(client=ch, database=settings.clickhouse_database)
                    _mv = ch.query("SELECT model_version FROM log_analysis.user_behavior_baselines ORDER BY created_at DESC LIMIT 1")
                    _model_version = str(_mv.result_rows[0][0]) if _mv.result_rows else "ueba_baseline_v1"
                    svc = BaselineReinforcementService(clickhouse_client=ch, ai_client=ai, baseline_store=bs)
                    results = svc.reinforce_all_users(
                        model_version=_model_version,
                        start_time=(datetime.now() - timedelta(days=30)).strftime("%Y-%m-%d %H:%M:%S"),
                        end_time=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                    )
                    ch.close()
                    st.success(f"AI 强化完成！处理了 {len(results)} 个用户")
                    st.rerun()
                except Exception as e:
                    st.error(f"AI 强化失败: {e}")

    # 有数据时才展示统计 + 筛选 + 卡片
    if refinements:
        usernames = sorted(set(r.get("username", "") for r in refinements if r.get("username")))
        sel_user = st.selectbox("👤 筛选用户", ["全部用户"] + usernames, label_visibility="collapsed", key="reinforce_user_filter")
        if sel_user != "全部用户":
            refinements = [r for r in refinements if r.get("username") == sel_user]

        attack_count = sum(1 for r in refinements if r.get("pattern_type") == "ATTACK")
        behavior_count = sum(1 for r in refinements if r.get("pattern_type") == "BEHAVIOR_CHANGE")
        st.markdown(f"""
        <div class="metric-group">
            <div class="metric-item"><div class="value">{len(refinements)}</div><div class="label">强化记录</div></div>
            <div class="metric-item"><div class="value">{attack_count}</div><div class="label">攻击模式</div></div>
            <div class="metric-item"><div class="value">{behavior_count}</div><div class="label">行为变化</div></div>
            <div class="metric-item"><div class="value">{sum(1 for r in refinements if r.get('is_baseline_stale'))}</div><div class="label">基线过时</div></div>
        </div>
        """, unsafe_allow_html=True)

    # 每一条强化建议展示
    for ref in refinements:
        pt = (ref.get("pattern_type") or "UNKNOWN").upper()
        rl_css = "critical" if pt == "ATTACK" else "high" if pt in ("BEHAVIOR_CHANGE",) else "low"

        import json
        adjustments = []
        try:
            raw = ref.get("suggested_adjustments", "[]")
            adjustments = json.loads(raw) if isinstance(raw, str) else (raw or [])
        except Exception:
            adjustments = []

        new_features = []
        try:
            raw2 = ref.get("new_watch_features", "[]")
            new_features = json.loads(raw2) if isinstance(raw2, str) else (raw2 or [])
        except Exception:
            new_features = []

        stale_raw = ref.get("stale_features", "[]")
        try:
            stale_list = json.loads(stale_raw) if isinstance(stale_raw, str) else (stale_raw or [])
        except Exception:
            stale_list = []

        confidence = ref.get("confidence", 0)
        summary = ref.get("analysis_summary", "")
        username = ref.get("username", "-")
        vt = str(ref.get("validated_at", ""))[:16]

        # 用 st.container 替代纯 HTML，避免多层嵌套解析失败
        with st.container():
            h1, h2 = st.columns([3, 1])
            with h1:
                st.markdown(f"**🧬 {username}**")
            with h2:
                st.markdown(f"<span class='badge {rl_css}'>{pt}</span> <span class='badge info'>{confidence:.0%}</span>", unsafe_allow_html=True)
            st.caption(f"{vt} · {ref.get('ai_platform','-')}")
            st.markdown(summary)
            if stale_list:
                st.caption(f"薄弱特征: {', '.join(stale_list)}")
            # 逐条渲染调整建议（不用 f-string 拼 HTML）
            for a in adjustments[:5]:
                sev = (a.get("severity") or "MEDIUM").lower()
                st.markdown(f"""
                <span class='badge {sev}'>{a.get('severity','MEDIUM')}</span>
                <strong>{a.get('field','')}</strong>: {a.get('suggested_change','')}
                """, unsafe_allow_html=True)
            for nf in new_features[:3]:
                st.markdown(f"<span class='badge info'>{nf.get('feature','')}: {nf.get('value','')}</span>", unsafe_allow_html=True)


def show_history_search():
    """显示历史查询"""
    # 查询条件（直接展示，不折叠）
    col1, col2 = st.columns(2)
    with col1:
        st.markdown("**📅 开始日期**")
        start_time = st.date_input("", value=datetime.now() - timedelta(days=7), label_visibility="collapsed")
        st.markdown("**👤 用户名**")
        try:
            import clickhouse_connect
            _ch = clickhouse_connect.get_client(host=settings.clickhouse_host, port=settings.clickhouse_port, username=settings.clickhouse_user, password=settings.clickhouse_password, database=settings.clickhouse_database)
            _users = _ch.query("SELECT DISTINCT username FROM log_analysis.logs_structured WHERE username != '' ORDER BY username LIMIT 500")
            _ch.close()
            user_options = ["全部"] + [str(r[0]) for r in _users.result_rows if r[0]]
        except Exception:
            user_options = ["全部"]
        username = st.selectbox("", user_options, label_visibility="collapsed")
        st.markdown("**📋 日志类型**")
        log_type = st.selectbox("", ["全部", "vpn", "api", "system", "network"], label_visibility="collapsed")
    with col2:
        st.markdown("**📅 结束日期**")
        end_time = st.date_input(" ", value=datetime.now(), label_visibility="collapsed")
        st.markdown("**🌐 IP 地址**")
        try:
            import clickhouse_connect
            _ch = clickhouse_connect.get_client(host=settings.clickhouse_host, port=settings.clickhouse_port, username=settings.clickhouse_user, password=settings.clickhouse_password, database=settings.clickhouse_database)
            _ips = _ch.query("SELECT DISTINCT source_ip FROM log_analysis.logs_structured WHERE source_ip != '' ORDER BY source_ip LIMIT 500")
            _ch.close()
            ip_options = ["全部"] + [str(r[0]) for r in _ips.result_rows if r[0]]
        except Exception:
            ip_options = ["全部"]
        source_ip = st.selectbox("", ip_options, label_visibility="collapsed")
        st.markdown("**📊 状态**")
        status = st.selectbox(" ", ["全部", "SUCCESS", "FAIL", "WARNING"], label_visibility="collapsed")

    q_cols = st.columns([4, 1, 1])
    with q_cols[1]:
        search_triggered = st.button("🔍 查询", type="primary", use_container_width=True)
    with q_cols[2]:
        if st.button("🗑️ 重置", use_container_width=True):
            st.rerun()

    search_results = search_history_logs(
        start_time=start_time,
        end_time=end_time,
        username=username if username and username != "全部" else None,
        source_ip=source_ip if source_ip and source_ip != "全部" else None,
        log_type=log_type,
        status=status,
    )

    df_results = pd.DataFrame(search_results)
    st.dataframe(df_results, use_container_width=True, height=350)

    # 导出功能
    meta_cols = st.columns([2, 1, 1, 1])
    with meta_cols[0]:
        st.caption(f"共 {len(search_results)} 条记录")
    with meta_cols[1]:
        csv_data = pd.DataFrame(search_results).to_csv(index=False).encode('utf-8-sig')
        st.download_button("📥 CSV", data=csv_data, file_name=f"query_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv", mime="text/csv", use_container_width=True)
    with meta_cols[2]:
        excel_buf = io.BytesIO()
        pd.DataFrame(search_results).to_excel(excel_buf, index=False, engine='openpyxl')
        st.download_button("📥 Excel", data=excel_buf.getvalue(), file_name=f"query_{datetime.now().strftime('%Y%m%d_%H%M%S')}.xlsx", mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", use_container_width=True)
    with meta_cols[3]:
        pdf_data = generate_pdf_report("history", search_results)
        st.download_button("📥 PDF", data=pdf_data or b"", file_name=f"query_{datetime.now().strftime('%Y%m%d_%H%M%S')}.pdf", mime="application/pdf", use_container_width=True)

def manual_ai_analyze(anomaly_id: int, username: str, description: str, related_log_ids: list):
    """手动触发 AI 分析并更新 anomaly_detection 表"""
    try:
        ai_config = settings.current_ai_config
        if not ai_config or not ai_config.get('api_key'):
            return "AI 分析器未配置，请检查 .env 文件"
        ai_analyzer = AIAnalyzer(
            api_key=ai_config['api_key'],
            platform=ai_config['platform'],
            model=ai_config.get('model'),
            base_url=ai_config.get('base_url')
        )

        log_context = ""
        if related_log_ids:
            import clickhouse_connect
            client = clickhouse_connect.get_client(
                host=settings.clickhouse_host,
                port=settings.clickhouse_port,
                username=settings.clickhouse_user,
                password=settings.clickhouse_password,
                database=settings.clickhouse_database,
                connect_timeout=5
            )
            ids_str = ','.join(str(i) for i in related_log_ids)
            context_query = f"SELECT raw_log FROM {settings.clickhouse_table} WHERE id IN ({ids_str})"
            context_res = client.query(context_query)
            log_context = "\n".join(row[0] for row in context_res.result_rows if row[0])
            client.close()

        ai_result = ai_analyzer.analyze_anomaly(
            username=username,
            anomaly_description=description,
            log_context=log_context
        )

        import clickhouse_connect
        client = clickhouse_connect.get_client(
            host=settings.clickhouse_host,
            port=settings.clickhouse_port,
            username=settings.clickhouse_user,
            password=settings.clickhouse_password,
            database=settings.clickhouse_database,
            connect_timeout=5
        )
        update_sql = """
        ALTER TABLE anomaly_detection UPDATE
            threat_type = %(threat_type)s,
            ai_analysis = %(ai_analysis)s,
            `处置建议` = %(suggestion)s,
            is_processed = 1,
            processed_at = now()
        WHERE id = %(id)s
        """
        client.command(update_sql, parameters={
            'id': anomaly_id,
            'threat_type': ai_result.get('threat_type', 'UNKNOWN'),
            'ai_analysis': ai_result.get('description', ''),
            'suggestion': ai_result.get('suggestion', '')
        })
        client.close()
        return "AI 分析成功"
    except Exception as e:
        return f"AI 分析失败: {e}"

def _inject_css():
    """注入全局 CSS 样式（跟随系统亮/暗模式）"""
    st.markdown("""
<style>
/* ---- 基础（亮色模式 / 默认） ---- */
:root {
    --bg-page: #f5f7fa;
    --bg-card: #ffffff;
    --bg-header: linear-gradient(135deg,#1a237e,#283593);
    --text-primary: #1a237e;
    --text-secondary: #666;
    --text-on-header: #ffffff;
    --border-card: 0 1px 4px rgba(0,0,0,0.06);
    --border-card-hover: 0 2px 8px rgba(0,0,0,0.1);
    --hr-color: #eee;
    --badge-critical-bg: #ffebee;
    --badge-critical-fg: #c62828;
    --badge-high-bg: #fff3e0;
    --badge-high-fg: #e65100;
    --badge-medium-bg: #fffde7;
    --badge-medium-fg: #f9a825;
    --badge-low-bg: #e8f5e9;
    --badge-low-fg: #2e7d32;
    --badge-info-bg: #e3f2fd;
    --badge-info-fg: #1565c0;
}
@media (prefers-color-scheme: dark) {
    :root {
        --bg-page: #0e1117;
        --bg-card: #1e2028;
        --text-primary: #e0e0e0;
        --text-secondary: #aaa;
        --border-card: 0 1px 4px rgba(255,255,255,0.06);
        --border-card-hover: 0 2px 8px rgba(255,255,255,0.1);
        --hr-color: #333;
        --badge-critical-bg: #3e1a1a;
        --badge-critical-fg: #ef9a9a;
        --badge-high-bg: #3e2a1a;
        --badge-high-fg: #ffcc80;
        --badge-medium-bg: #3e3a1a;
        --badge-medium-fg: #fff59d;
        --badge-low-bg: #1a3e1a;
        --badge-low-fg: #a5d6a7;
        --badge-info-bg: #1a2a3e;
        --badge-info-fg: #90caf9;
    }
}

.main > div { padding-top:0.5rem !important; }
.stApp { background:var(--bg-page); }
.page-header { background:var(--bg-header); color:var(--text-on-header); padding:0.8rem 1.5rem; border-radius:10px; margin-bottom:1.2rem; display:flex; align-items:center; gap:0.8rem; }
.page-header h2 { margin:0; font-weight:600; font-size:1.3rem; }
.page-header .subtitle { font-size:0.85rem; opacity:0.85; margin-left:auto; }
.risk-card { background:var(--bg-card); border-radius:10px; padding:1rem 1.2rem; box-shadow:var(--border-card); border-left:4px solid #e0e0e0; margin-bottom:0.6rem; }
.risk-card:hover { box-shadow:var(--border-card-hover); }
.risk-card.critical { border-left-color:#d32f2f; }
.risk-card.high { border-left-color:#f57c00; }
.risk-card.medium { border-left-color:#fbc02d; }
.risk-card.low { border-left-color:#388e3c; }
.risk-card .card-title { font-weight:600; font-size:0.95rem; }
.risk-card .card-meta { font-size:0.8rem; color:var(--text-secondary); margin-top:0.2rem; }
.badge { display:inline-block; padding:0.15rem 0.6rem; border-radius:12px; font-size:0.75rem; font-weight:600; text-transform:uppercase; }
.badge.critical { background:var(--badge-critical-bg); color:var(--badge-critical-fg); }
.badge.high { background:var(--badge-high-bg); color:var(--badge-high-fg); }
.badge.medium { background:var(--badge-medium-bg); color:var(--badge-medium-fg); }
.badge.low { background:var(--badge-low-bg); color:var(--badge-low-fg); }
.badge.info { background:var(--badge-info-bg); color:var(--badge-info-fg); }
.metric-group { display:flex; gap:1rem; flex-wrap:wrap; margin-bottom:1rem; }
.metric-item { background:var(--bg-card); border-radius:10px; padding:0.8rem 1.2rem; flex:1; min-width:120px; box-shadow:var(--border-card); text-align:center; }
.metric-item .value { font-size:1.6rem; font-weight:700; color:var(--text-primary); }
.metric-item .label { font-size:0.78rem; color:var(--text-secondary); margin-top:0.1rem; }
.score-ring { width:100px; height:100px; border-radius:50%; display:flex; align-items:center; justify-content:center; margin:0 auto; font-size:1.6rem; font-weight:700; color:white; }
hr { margin:0.8rem 0; border-color:var(--hr-color); }
/* stMetric 文字颜色适配 */
div[data-testid="stMetricValue"] { color:var(--text-primary) !important; }
div[data-testid="stMetricLabel"] { color:var(--text-secondary) !important; }
/* 正文颜色 */
.stMarkdown, .stMarkdown p, .stMarkdown li, .stMarkdown span { color:var(--text-primary); }
</style>
""", unsafe_allow_html=True)


def main():
    """主函数"""
    init_session_state()
    _inject_css()
    create_sidebar()
    
    # 根据选择显示对应页面
    if st.session_state.current_page == "实时日志流":
        show_realtime_logs()
    elif st.session_state.current_page == "UEBA 异常排行":
        show_ueba_ranking()
    elif st.session_state.current_page == "安全评分看板":
        show_security_score()
    elif st.session_state.current_page == "AI分析+强化基线":
        show_ai_suggestions()
    elif st.session_state.current_page == "历史查询":
        show_history_search()


if __name__ == "__main__":
    main()
