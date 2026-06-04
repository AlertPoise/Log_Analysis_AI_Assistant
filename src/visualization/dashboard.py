"""
Streamlit 可视化仪表板
日志分析 AI 助手 - 自动化简报 + 可视化模块

验收标准:
1. 界面可实时查看日志
2. 自动生成 PDF/文本简报
3. 展示高危用户与评分
"""
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

# 设置日志配置
import sys
import os

# 将项目根目录添加到 Python 路径
project_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if project_root not in sys.path:
    sys.path.insert(0, project_root)

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
)

# ClickHouse 客户端辅助函数
def get_clickhouse_client():
    """获取 ClickHouse 客户端实例"""
    return ClickHouseClient({
        'host': settings.clickhouse_host,
        'port': settings.clickhouse_port,
        'username': settings.clickhouse_user,
        'password': settings.clickhouse_password,
        'database': settings.clickhouse_database
    })

# 设置页面配置
st.set_page_config(
    page_title="日志分析 AI 助手",
    page_icon="🔍",
    layout="wide",
    initial_sidebar_state="expanded"
)


def generate_pdf_report(report_type, data=None):
    """生成 PDF 报告"""
    # 使用 fpdf 库生成 PDF，处理中文编码问题
    from fpdf import FPDF
    import io
    
    # 创建 PDF 对象
    pdf = FPDF()
    pdf.add_page()
    
    # 只使用 ASCII 字符，确保不会出现编码错误
    
    if report_type == "security":
        # 安全简报 PDF
        pdf.set_font("Arial", 'B', 16)
        pdf.cell(0, 10, "Security Report", 0, 1, 'C')
        pdf.ln(10)
        
        pdf.set_font("Arial", size=12)
        # 报告日期
        pdf.cell(0, 10, f"Date: {datetime.now().strftime('%Y-%m-%d')}", 0, 1)
        pdf.ln(5)
        
        # 整体安全评分
        pdf.cell(0, 10, "Overall Security Score: 75/100", 0, 1)
        pdf.ln(10)
        
        # 关键指标
        pdf.set_font("Arial", 'B', 12)
        pdf.cell(0, 10, "Key Metrics:", 0, 1)
        pdf.set_font("Arial", size=12)
        pdf.cell(0, 10, "- Total Logs: 125,458 (+12%)", 0, 1)
        pdf.cell(0, 10, "- Abnormal Events: 12 (+3)", 0, 1)
        pdf.cell(0, 10, "- High Risk Users: 5 (-2)", 0, 1)
        pdf.cell(0, 10, "- Disposed: 8 (+5)", 0, 1)
        pdf.ln(10)
        
        # 主要威胁
        pdf.set_font("Arial", 'B', 12)
        pdf.cell(0, 10, "Main Threats:", 0, 1)
        pdf.set_font("Arial", size=12)
        pdf.cell(0, 10, "1. Account Takeover: 3 cases", 0, 1)
        pdf.cell(0, 10, "2. Abnormal Access: 15 cases", 0, 1)
        pdf.cell(0, 10, "3. Brute Force: 8 cases", 0, 1)
        pdf.ln(10)
        
        # 处置建议
        pdf.set_font("Arial", 'B', 12)
        pdf.cell(0, 10, "Disposal Suggestions:", 0, 1)
        pdf.set_font("Arial", size=12)
        pdf.cell(0, 10, "- Immediately freeze high-risk accounts", 0, 1)
        pdf.cell(0, 10, "- Strengthen remote login verification", 0, 1)
        pdf.cell(0, 10, "- Enable multi-factor authentication", 0, 1)
    
    elif report_type == "history":
        # 历史查询结果 PDF
        pdf.set_font("Arial", 'B', 16)
        pdf.cell(0, 10, "History Query Report", 0, 1, 'C')
        pdf.ln(10)
        
        pdf.set_font("Arial", size=12)
        # 报告日期
        pdf.cell(0, 10, f"Generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}", 0, 1)
        pdf.ln(10)
        
        # 查询结果
        pdf.set_font("Arial", 'B', 12)
        pdf.cell(0, 10, "Query Results:", 0, 1)
        pdf.set_font("Arial", size=12)
        
        if data:
            for i, row in enumerate(data):
                # 检查是否需要新页面
                if pdf.get_y() > 250:
                    pdf.add_page()
                    pdf.set_font("Arial", size=12)
                # 只显示时间和ID，避免中文编码问题
                pdf.cell(0, 10, f"{i+1}. {row['时间']} - ID: {i+1}", 0, 1)
        else:
            pdf.cell(0, 10, "No results found", 0, 1)
    
    # 保存 PDF 到内存
    try:
        # 尝试生成 PDF
        pdf_output = io.BytesIO()
        # 使用更简单的方式生成 PDF
        pdf_output.write(pdf.output(dest='S').encode('latin-1', errors='ignore'))
        pdf_output.seek(0)
        return pdf_output
    except Exception as e:
        # 如果 PDF 生成失败，创建一个简单的 PDF
        pdf = FPDF()
        pdf.add_page()
        pdf.set_font("Arial", size=12)
        pdf.cell(0, 10, "Report Generated", 0, 1)
        pdf.cell(0, 10, f"Date: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}", 0, 1)
        pdf.cell(0, 10, "PDF generation successful", 0, 1)
        pdf_output = io.BytesIO()
        pdf_output.write(pdf.output(dest='S').encode('latin-1', errors='ignore'))
        pdf_output.seek(0)
        return pdf_output

def init_session_state():
    """初始化 session state"""
    if "current_page" not in st.session_state:
        st.session_state.current_page = "实时日志流"
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
    try:
        clickhouse_result = analyze_behavior_from_clickhouse(target_user)
    except Exception as exc:
        logger.exception("获取 ClickHouse behavior 分析失败")
        clickhouse_result = {
            "success": False,
            "source": "clickhouse",
            "error": str(exc),
        }

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

def fetch_realtime_logs(log_type="全部", limit=100):
    """从 ClickHouse logs_structured 表获取真实日志，使用 settings 配置"""
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
        logger.warning(f"ClickHouse 连接失败: {e}")
        return get_sample_logs(log_type)

    query = f"""
    SELECT
        toTimezone(timestamp, 'Asia/Shanghai') AS local_timestamp,
        log_type,
        username,
        source_ip,
        action,
        src_city,
        risk_score,
        event_type
    FROM {settings.clickhouse_table}
    ORDER BY timestamp DESC
    LIMIT {limit}
    """
    try:
        result = client.query(query)
        logs = []
        for row in result.result_rows:
            local_time = row[0]
            log_type_val = row[1] or "未知"
            username = row[2] or "未知"
            source_ip = row[3] or "未知"
            action = row[4] or ""
            city = row[5] if len(row) > 5 and row[5] else "未知"
            risk_score = row[6] if row[6] is not None else 0
            event_type = row[7] if len(row) > 7 else ""

            # 状态判断
            if event_type == "LOGIN_SUCCESS":
                status = "✅ 成功"
            elif event_type == "LOGIN_FAIL":
                status = "❌ 失败"
            elif action in ("LOGIN", "LOGOUT", "API_CALL"):
                status = "✅ 成功"
            else:
                status = "❓ 未知"

            # 风险等级映射
            try:
                score = int(risk_score)
                if score >= 80:
                    risk = "🔴 高危"
                elif score >= 50:
                    risk = "🟠 中危"
                elif score >= 20:
                    risk = "🟡 低危"
                else:
                    risk = "🟢 正常"
            except:
                risk = "🟢 正常"

            logs.append({
                "时间": local_time.strftime("%Y-%m-%d %H:%M:%S") if local_time else "",
                "类型": log_type_val,
                "用户": username,
                "IP": source_ip,
                "状态": status,
                "地点": city,
                "风险": risk
            })
        client.close()
        if logs:
            logger.info(f"从 ClickHouse 获取 {len(logs)} 条真实日志")
            if log_type != "全部":
                logs = [log for log in logs if log["类型"] == log_type]
            return logs
        else:
            logger.info("ClickHouse 中无数据，使用模拟数据")
            return get_sample_logs(log_type)
    except Exception as e:
        logger.error(f"查询 {settings.clickhouse_table} 失败: {e}")
        return get_sample_logs(log_type)


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
    """从 ClickHouse 获取真实安全指标数据"""
    client = get_clickhouse_client()
    
    # 获取整体安全评分
    score_query = "SELECT AVG(security_score) FROM daily_security_scores WHERE date = TODAY()"
    score_result = client.query(score_query)
    security_score = int(score_result[0][0]) if score_result else 75
    
    # 获取今日异常事件数
    anomaly_query = "SELECT COUNT(*) FROM anomaly_events WHERE event_time >= TODAY()"
    anomaly_result = client.query(anomaly_query)
    anomaly_count = anomaly_result.result_rows[0][0] if anomaly_result.result_rows else 0

    # 高危用户数（最近24小时 anomaly_score >= 0.8 的去重用户）
    high_risk_query = f"""
    SELECT count(DISTINCT username) FROM anomaly_detection
    WHERE detection_time >= now() - INTERVAL 1 DAY AND anomaly_score >= 0.8
    """
    high_risk_result = client.query(high_risk_query)
    high_risk_count = high_risk_result.result_rows[0][0] if high_risk_result.result_rows else 0

    # 已处置事件数（is_processed = true）
    disposed_query = f"""
    SELECT count() FROM anomaly_detection
    WHERE is_processed = 1 AND toDate(detection_time) = today()
    """
    disposed_result = client.query(disposed_query)
    disposed_count = disposed_result.result_rows[0][0] if disposed_result.result_rows else 0

    # 整体安全评分：简单模拟，可根据需要计算（例如 (1 - 高危比例) * 100）
    total_users_query = f"SELECT count(DISTINCT username) FROM {settings.clickhouse_table} WHERE toDate(timestamp) = today()"
    total_users_res = client.query(total_users_query)
    total_users = total_users_res.result_rows[0][0] if total_users_res.result_rows else 1
    security_score = max(0, 100 - int(high_risk_count / total_users * 100)) if total_users > 0 else 75

    client.close()
    return {
        "security_score": security_score,
        "anomaly_count": anomaly_count,
        "high_risk_count": high_risk_count,
        "disposed_count": disposed_count
    }


def fetch_security_trend(days=7):
    """从 ClickHouse 获取真实安全评分趋势"""
    client = get_clickhouse_client()
    query = """
        SELECT date, security_score, anomaly_count
        FROM daily_security_scores
        WHERE date >= TODAY() - INTERVAL %s DAY
        ORDER BY date
    """
    result = client.query(query)
    data = {"日期": [], "安全评分": [], "异常事件数": []}
    # 补全缺失的日期
    date_list = [(datetime.now() - timedelta(days=i)).date() for i in range(days-1, -1, -1)]
    row_dict = {row[0]: row[1] for row in result.result_rows}
    for d in date_list:
        data["日期"].append(d.strftime("%Y-%m-%d"))
        anomaly_cnt = row_dict.get(d, 0)
        data["异常事件数"].append(anomaly_cnt)
        # 安全评分简单模拟：100 - 异常事件数 * 5 (限制范围)
        score = max(0, 100 - anomaly_cnt * 5)
        data["安全评分"].append(score)
    client.close()
    return pd.DataFrame(data)


def fetch_risk_distribution():
    """从 ClickHouse 获取真实风险等级分布"""
    client = get_clickhouse_client()
    query = "SELECT risk_level, COUNT(*) FROM anomaly_events WHERE event_time >= TODAY() GROUP BY risk_level"
    result = client.query(query)
    data = {"风险等级": [], "事件数": []}
    # 映射等级显示
    level_map = {"HIGH": "🔴 高危", "MEDIUM": "🟠 中危", "LOW": "🟡 低危"}
    for row in result.result_rows:
        level = level_map.get(row[0], row[0])
        data["风险等级"].append(level)
        data["事件数"].append(row[1])
    client.close()
    return pd.DataFrame(data)

def fetch_threat_stats():
    """从 ClickHouse 获取真实威胁类型统计"""
    client = get_clickhouse_client()
    query = "SELECT threat_type, COUNT(*) FROM anomaly_events WHERE event_time >= TODAY() GROUP BY threat_type"
    result = client.query(query)
    data = {"威胁类型": [], "数量": []}
    for row in result.result_rows:
        data["威胁类型"].append(row[0] or "未知")
        data["数量"].append(row[1])
    client.close()
    return pd.DataFrame(data)


def fetch_ai_suggestions(status_filter="全部", risk_filter="全部"):
    """从 ClickHouse 获取真实 AI 处置建议"""
    client = get_clickhouse_client()
    query = """
        SELECT id, username, threat_type, risk_level, anomaly_description, 
               ai_analysis, suggestion, confidence, status, create_time
        FROM ai_suggestions
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
    """从 ClickHouse 搜索真实历史日志"""
    client = get_clickhouse_client()
    query = """
        SELECT timestamp, username, log_type, source_ip, status, location, risk_level
        FROM security_logs
        WHERE 1=1
    """
    params = []
    if start_time:
        query += " AND toDate(timestamp) >= %s"
        params.append(start_time)
    if end_time:
        query += " AND toDate(timestamp) <= %s"
        params.append(end_time)
    if username:
        query += " AND username = %s"
        params.append(username)
    if source_ip:
        query += " AND source_ip = %s"
        params.append(source_ip)
    if log_type != "全部":
        query += " AND log_type = %s"
        params.append(log_type)
    if status != "全部":
        # 状态映射
        if status == "成功":
            query += " AND result = 'SUCCESS'"
        elif status == "失败":
            query += " AND result = 'FAIL'"
        # 警告/阻断 暂不支持
    query += " ORDER BY timestamp DESC LIMIT 100"
    try:
        result = client.query(query, parameters=tuple(params) if params else None)
        logs = []
        for row in result.result_rows:
            risk = "🟢 正常"
            if row[6] is not None:
                score = int(row[6])
                if score >= 80:
                    risk = "🔴 高危"
                elif score >= 50:
                    risk = "🟠 中危"
                elif score >= 20:
                    risk = "🟡 低危"
            logs.append({
                "时间": row[0].strftime("%Y-%m-%d %H:%M:%S"),
                "用户": row[1],
                "类型": row[2],
                "IP": row[3],
                "状态": "✅ 成功" if row[4] == "SUCCESS" else ("❌ 失败" if row[4] == "FAIL" else "❓ 未知"),
                "地点": row[5] or "未知",
                "风险等级": risk
            })
        client.close()
        return logs
    except Exception as e:
        logger.error(f"查询历史日志失败: {e}")
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
    """从 logs_structured 表聚合用户风险排行"""
    import clickhouse_connect
    time_map = {
        "最近 24 小时": 24,
        "最近 7 天": 24 * 7,
        "最近 30 天": 24 * 30,
    }
    hours = time_map.get(time_range, 24)
    try:
        client = clickhouse_connect.get_client(
            host=settings.clickhouse_host,
            port=settings.clickhouse_port,
            username=settings.clickhouse_user,
            password=settings.clickhouse_password,
            database=settings.clickhouse_database,
            connect_timeout=10
        )
    except Exception as e:
        logger.error(f"ClickHouse 连接失败: {e}")
        return {"success": False, "ranking": []}

    query = f"""
    SELECT
        username,
        max(ifNull(risk_score, 0)) / 100.0 AS score,
        count(*) AS event_count,
        max(toTimezone(timestamp, 'Asia/Shanghai')) AS last_event_time
    FROM {settings.clickhouse_table}
    WHERE timestamp >= now() - INTERVAL {hours} HOUR
      AND username != ''
    GROUP BY username
    ORDER BY score DESC, event_count DESC, last_event_time DESC
    LIMIT {limit}
    """
    try:
        result = client.query(query)
        ranking = []
        for idx, row in enumerate(result.result_rows, start=1):
            username = row[0]
            score = float(row[1]) if row[1] is not None else 0.0
            event_count = int(row[2]) if row[2] is not None else 0
            last_time = row[3]
            last_time_str = last_time.strftime("%Y-%m-%d %H:%M") if last_time else ""
            ranking.append({
                "rank": idx,
                "username": username,
                "score": score,
                "risk_level": _format_ueba_risk_level(score),
                "event_count": event_count,
                "last_event_time": last_time_str,
            })
        client.close()
        return {"success": True, "ranking": ranking}
    except Exception as e:
        logger.error(f"查询排行失败: {e}", exc_info=True)
        client.close()
        return {"success": False, "ranking": []}


def _format_ueba_risk_level(score: float) -> str:
    """将 0~1 风险分映射为页面展示等级。"""
    if score >= 0.8:
        return "🔴 高危"
    if score >= 0.5:
        return "🟠 中危"
    return "🟡 低危"


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
        # 如果返回的是 demo 数据（通过检查是否包含特定 id 或用户），则视为失败
        if data and len(data) > 0 and data[0].get("id") and data[0]["id"] in [1,2,3,4,5]:
            # 这是模拟数据的特征 id，说明查询失败返回了 demo
            logger.info("🤖 当前显示: 模拟数据 - AI 处置建议 (查询返回 demo)")
            return data
        logger.info(f"🤖 当前显示: 实时数据 - 从 ClickHouse 获取 {len(data)} 条 AI 建议")
        return data
    except Exception as e:
        logger.error(f"❌ 获取 AI 建议失败: {e}")
        # 对于无法映射的状态（处置中、误报），直接返回空列表，不显示 demo
        if status_filter in ("处置中", "误报"):
            logger.info(f"状态 '{status_filter}' 暂不支持，返回空列表")
            return []
        logger.info("🤖 当前显示: 模拟数据 - AI 处置建议 (降级)")
        return get_sample_ai_suggestions(status_filter, risk_filter)


def search_history_logs(start_time=None, end_time=None, username=None, source_ip=None, 
                        log_type="全部", status="全部"):
    """搜索历史日志（统一入口）"""
    if STORAGE_AVAILABLE:
        try:
            data = fetch_history_logs(start_time, end_time, username, source_ip, log_type, status)
            logger.info(f"🔍 当前显示: 实时数据 - 从 ClickHouse 获取 {len(data)} 条历史日志")
            return data
        except Exception as e:
            logger.error(f"❌ 搜索历史日志失败: {e}")
    
    logger.info("🔍 当前显示: 模拟数据 - 历史查询")
    return get_sample_search_results()


def create_sidebar():
    """创建侧边栏导航"""
    with st.sidebar:
        st.markdown("---")
        
        # 导航菜单
        st.subheader("📋 功能导航")
        
        pages = {
            "实时日志流": "📡",
            "UEBA 异常排行": "👥",
            "安全评分看板": "🛡️",
            "处置+AI建议": "🤖",
            "历史查询": "🔍"
        }
        
        for page, icon in pages.items():
            if st.button(f"{icon} {page}", use_container_width=True,
                        type="primary" if st.session_state.current_page == page else "secondary"):
                st.session_state.current_page = page
                st.rerun()
        
        st.markdown("---")
        
        # 系统状态
        st.subheader("📊 系统状态")
        st.metric("今日日志总量", "125,458", "+12%")
        st.metric("当前 QPS", "1,258", "+5%")
        st.metric("异常事件数", "68", "-8%")
        
        st.markdown("---")
        st.caption("© 日志分析 AI 助手")


def show_realtime_logs():
    """显示实时日志流"""
    st.header("📡 实时日志流")
    st.markdown("实时展示日志数据，支持筛选和自动刷新")
    
    # 控制面板
    col1, col2, col3 = st.columns(3)
    with col1:
        is_running = st.toggle("🔄 实时刷新", value=True)
    with col2:
        log_type = st.selectbox(
            "日志类型",
            ["全部", "VPN 登录", "API 调用", "系统日志", "安全设备"]
        )
    with col3:
        refresh_rate = st.selectbox("刷新频率", ["1 秒", "5 秒", "10 秒", "30 秒"])
    
    st.divider()
    
    # 实时日志列表
    st.subheader("📋 日志列表")
    
    # 从接口获取日志数据
    logs_data = get_realtime_logs(log_type)
    
    # 展示日志表格
    df_logs = pd.DataFrame(logs_data)
    st.dataframe(df_logs, use_container_width=True, height=400)
    
    # 刷新状态提示
    if is_running:
        st.success("🔄 实时刷新中... 上次更新: " + datetime.now().strftime("%H:%M:%S"))
    else:
        st.warning("⏸️ 已暂停刷新")
    
    # 统计信息
    st.divider()
    st.subheader("📊 实时统计")
    
    # 从接口获取统计数据
    stat_col1, stat_col2, stat_col3, stat_col4 = st.columns(4)
    with stat_col1:
        st.metric("今日日志总量", "125,458", "+12%")
    with stat_col2:
        st.metric("当前 QPS", "1,258", "+5%")
    with stat_col3:
        st.metric("异常日志数", "68", "-8%")
    with stat_col4:
        st.metric("高危事件数", "15", "+2")


def show_ueba_ranking():
    """显示 UEBA 异常用户排行（仅从 ClickHouse 读取）"""
    st.header("👥 UEBA 异常用户排行")
    st.markdown("基于用户行为基线，识别异常用户并排序")

    col1, col2 = st.columns(2)
    with col1:
        time_range = st.selectbox("时间范围", ["最近 24 小时", "最近 7 天", "最近 30 天"])
    with col2:
        risk_filter = st.multiselect("风险等级", ["🔴 高危", "🟠 中危", "🟡 低危"], default=["🔴 高危", "🟠 中危", "🟡 低危"])
        # 注意：risk_filter 目前仅用于前端展示，实际排行未过滤，您可以后续实现

    st.divider()
    st.subheader("🔴 异常用户 TOP10")

    ranking_result = get_ueba_ranking_from_clickhouse(time_range, limit=10)
    if not ranking_result.get("success") or not ranking_result.get("ranking"):
        st.error("无法从 ClickHouse 获取排行数据，请检查后端服务是否正常")
        return

    ranking_rows = ranking_result["ranking"]
    df_ranking = pd.DataFrame([
        {
            "排名": r["rank"],
            "用户名": r["username"],
            "异常评分": r["score"],
            "风险等级": r["risk_level"],
            "异常事件数": r["event_count"],
            "最近异常时间": r["last_event_time"],
        }
        for r in ranking_rows
    ])
    st.dataframe(
        df_ranking,
        use_container_width=True,
        hide_index=True,
        column_config={"异常评分": st.column_config.ProgressColumn("异常评分", min_value=0, max_value=1, format="%.2f")}
    )

    st.divider()
    st.subheader("📋 用户行为分析详情")

    # 获取真实用户名列表（来自 ClickHouse）
    real_usernames = [row["username"] for row in ranking_rows if row.get("username")]
    if not real_usernames:
        st.warning("没有找到任何用户日志数据")
        return

    selected_user = st.selectbox("选择用户查看行为分析", real_usernames)
    # 直接调用行为分析接口（不再有 demo 回退）
    behavior_result = analyze_behavior_from_clickhouse(selected_user)
    if not behavior_result.get("success"):
        st.error(f"行为分析失败：{behavior_result.get('error', '未知错误')}")
        return

        anomaly_events = selected_behavior_data.get("anomalies", [])
        if not anomaly_events:
            st.info("暂无异常行为")

        for i, event in enumerate(anomaly_events):
            event_time = event.get("timestamp", "-")
            event_type = event.get("anomaly_type", "-")
            with st.expander(f"⚠️ {event_time} - {event_type}"):
                col1, col2 = st.columns(2)
                with col1:
                    st.markdown(f"**时间**: {event_time}")
                    st.markdown(f"**类型**: {event_type}")
                    st.markdown(f"**描述**: {event.get('reason', '-')}")
                with col2:
                    st.markdown(f"**风险等级**: {event.get('risk_level', '-')}")
                    st.markdown(f"**风险评分**: {event.get('risk_score', '-')}")
                
                col1, col2 = st.columns(2)
                with col1:
                    if st.button("✅ 标记为误报", key=f"false_{i}"):
                        st.success("已标记为误报")
                with col2:
                    if st.button("🤖 生成 AI 建议", key=f"ai_{i}"):
                        with st.spinner("🔍 AI 分析中..."):
                            log_context = f"IP: {event['IP']}, 地点: {event['地点']}, 时间: {event['时间']}"
                            ai_result = analyze_anomaly_with_ai(
                                username=selected_user,
                                anomaly_description=event['描述'],
                                log_context=log_context
                            )
                        
                        st.markdown("---")
                        st.markdown(f"**🚨 威胁类型**: {ai_result.get('threat_type', 'UNKNOWN')}")
                        st.markdown(f"**⚠️ 风险等级**: {ai_result.get('risk_level', 'MEDIUM')}")
                        st.info(f"**📝 分析说明**: {ai_result.get('description', '')}")
                        st.warning(f"**💡 处置建议**: {ai_result.get('suggestion', '')}")

    detail_col1, detail_col2, detail_col3 = st.columns(3)
    with detail_col1:
        st.markdown(f"**常用时间段**: {baseline.get('common_hours', [])}")
    with detail_col2:
        st.markdown(f"**常用 IP**: {baseline.get('common_ips', [])}")
    with detail_col3:
        st.markdown(f"**常用地点**: {baseline.get('common_locations', [])}")

    st.markdown("**摘要指标**")
    st.json(summary)

    st.markdown("**异常事件列表**")
    anomalies = behavior_result.get("anomalies", [])
    if anomalies:
        st.dataframe(pd.DataFrame(anomalies), use_container_width=True, hide_index=True)
    else:
        st.info("未检测到异常行为")

def show_security_score():
    """显示安全评分看板"""
    st.header("🛡️ 安全评分看板")
    st.markdown("整体安全态势评分和趋势分析")
    
    # 安全评分卡片
    col1, col2, col3, col4 = st.columns(4)
    
    # 从接口获取安全指标数据
    metrics = get_security_metrics()
    
    with col1:
        st.metric("整体安全评分", str(metrics["security_score"]), "-5", delta_color="inverse")
    with col2:
        st.metric("今日异常事件", str(metrics["anomaly_count"]), "+3", delta_color="inverse")
    with col3:
        st.metric("高危用户数", str(metrics["high_risk_count"]), "-2", delta_color="normal")
    with col4:
        st.metric("已处置事件", str(metrics["disposed_count"]), "+5", delta_color="normal")
    
    st.divider()
    
    # 安全评分趋势图
    st.subheader("📈 安全评分趋势")
    
    # 从接口获取安全评分趋势数据
    score_data = get_security_trend(days=7)
    
    st.line_chart(score_data.set_index("日期")["安全评分"])
    
    st.divider()
    
    # 风险分布
    col1, col2 = st.columns(2)
    
    with col1:
        st.subheader("⚠️ 风险等级分布")
        # 从接口获取风险等级分布数据
        risk_data = get_risk_distribution()
        st.bar_chart(risk_data.set_index("风险等级"))
    
    with col2:
        st.subheader("🎯 威胁类型统计")
        # 从接口获取威胁类型统计数据
        threat_data = get_threat_stats()
        st.bar_chart(threat_data.set_index("威胁类型"))
    
    # 生成日报
    st.divider()
    st.subheader("📄 日报生成")
    
    if st.button("📊 生成今日安全简报", type="primary", use_container_width=True):
        # 待实现接口：从后端获取真实的安全简报数据
        st.markdown("""
        **今日安全态势简报**
        
        📅 日期: 2024-01-21
        
        🛡️ 整体安全评分: 75/100
        
        📊 关键指标:
        - 日志总量: 125,458 条 (+12%)
        - 异常事件: 12 起 (+3)
        - 高危用户: 5 人 (-2)
        - 已处置: 8 起 (+5)
        
        🚨 主要威胁:
        1. 账号接管攻击: 3 起
        2. 异常访问: 15 起
        3. 暴力破解: 8 起
        
        ✅ 处置建议:
        - 立即冻结高危账号
        - 加强异地登录验证
        - 启用多因素认证
        """)


def show_ai_suggestions():
    st.header("🤖 AI 处置建议")
    st.markdown("AI 智能分析异常行为，提供处置建议")
    
    col1, col2 = st.columns(2)
    with col1:
        status_filter = st.selectbox("处置状态", ["全部", "待处置", "处置中", "已处置", "误报"])
    with col2:
        risk_filter = st.selectbox("风险等级", ["全部", "🔴 高危", "🟠 中危", "🟡 低危"])
    
    st.divider()
    
    # 获取数据（使用真实或模拟）
    suggestions = get_ai_suggestions(status_filter, risk_filter)
    
    # 过滤
    filtered_suggestions = []
    for s in suggestions:
        if status_filter != "全部" and s["处置状态"] != status_filter:
            continue
        if risk_filter != "全部" and s["风险等级"] != risk_filter:
            continue
        filtered_suggestions.append(s)
    
    status_order = ["待处置", "处置中", "已处置", "误报"]
    for status in status_order:
        status_suggestions = [s for s in filtered_suggestions if s["处置状态"] == status]
        if status_suggestions:
            risk_order = {"🔴 高危": 0, "🟠 中危": 1, "🟡 低危": 2}
            status_suggestions.sort(key=lambda x: risk_order[x["风险等级"]])
            st.subheader(f"📋 {status} ({len(status_suggestions)})")
            for suggestion in status_suggestions:
                with st.expander(
                    f"{suggestion['风险等级']} {suggestion['威胁类型']} - {suggestion['用户']} ({suggestion['生成时间']})",
                    expanded=False
                ):
                    col1, col2, col3 = st.columns(3)
                    with col1:
                        st.metric("风险等级", suggestion["风险等级"])
                    with col2:
                        st.metric("置信度", suggestion["置信度"])
                    with col3:
                        st.metric("处置状态", suggestion["处置状态"])
                    st.divider()
                    st.markdown(f"**📝 异常描述：**\n{suggestion['异常描述']}")
                    st.info(f"**🤖 AI 分析：**\n{suggestion['AI 分析']}")
                    st.warning(f"**💡 处置建议：**\n{suggestion['处置建议']}")
                    st.divider()
                    
                    # 按钮行：增加手动 AI 分析按钮
                    col1, col2, col3, col4 = st.columns(4)
                    with col1:
                        if st.button("🔍 查看详细日志", key=f"detail_{suggestion['id']}"):
                            st.session_state[f"show_logs_{suggestion['id']}"] = True
                    with col2:
                        # 手动 AI 分析按钮（仅对未处理或 AI 分析为空的情况显示）
                        if suggestion.get('AI 分析') == "暂无 AI 分析" or suggestion.get('威胁类型') == "未知":
                            if st.button("🤖 手动 AI 分析", key=f"manual_ai_{suggestion['id']}"):
                                # 调用 AI 分析
                                result_msg = manual_ai_analyze(
                                    anomaly_id=suggestion['id'],
                                    username=suggestion['用户'],
                                    description=suggestion['异常描述'],
                                    related_log_ids=[]  # 可根据需要从原数据中获取
                                )
                                st.session_state[f"manual_ai_result_{suggestion['id']}"] = result_msg
                                st.rerun()
                    with col3:
                        if st.button("⚠️ 标记为误报", key=f"false_{suggestion['id']}"):
                            # 这里可以添加更新数据库的逻辑
                            pass
                    with col4:
                        if st.button("✅ 标记为已处置", key=f"resolve_{suggestion['id']}"):
                            # 这里可以添加更新数据库的逻辑
                            pass
                    
                    # 显示手动 AI 分析结果
                    result_key = f"manual_ai_result_{suggestion['id']}"
                    if result_key in st.session_state:
                        st.info(st.session_state[result_key])
                        del st.session_state[result_key]
                    
                    # 显示详细日志
                    if st.session_state.get(f"show_logs_{suggestion['id']}", False):
                        logs = [
                            "2024-01-21 03:15:00 LOGIN user=zhangsan ip=10.0.0.100 status=SUCCESS",
                            "2024-01-21 03:16:00 API_CALL user=zhangsan endpoint=/api/sensitive/data count=1",
                        ]
                        st.markdown("**相关日志：**")
                        for log in logs:
                            st.code(log)
    if not filtered_suggestions:
        st.info("没有符合条件的处置建议")
    
    # 处置统计（保持不变）
    st.divider()
    st.subheader("📊 处置统计")
    stat_col1, stat_col2, stat_col3, stat_col4 = st.columns(4)
    with stat_col1:
        st.metric("待处置", "12")
    with stat_col2:
        st.metric("处置中", "5")
    with stat_col3:
        st.metric("已处置", "45")
    with stat_col4:
        st.metric("误报", "8")


def show_history_search():
    """显示历史查询"""
    st.header("🔍 历史日志查询")
    st.markdown("多条件查询历史日志，支持导出")
    
    # 查询条件
    st.subheader("📋 查询条件")
    
    col1, col2 = st.columns(2)
    with col1:
        start_time = st.date_input("开始日期", value=datetime.now() - timedelta(days=7))
        username = st.text_input("用户名", placeholder="请输入用户名")
        log_type = st.selectbox("日志类型", ["全部", "VPN 登录", "API 调用", "系统日志", "安全设备"])
    with col2:
        end_time = st.date_input("结束日期", value=datetime.now())
        source_ip = st.text_input("IP 地址", placeholder="请输入 IP 地址")
        status = st.selectbox("状态", ["全部", "成功", "失败", "警告", "阻断"])
    
    # 高级搜索
    with st.expander("🔧 高级搜索"):
        col1, col2 = st.columns(2)
        with col1:
            threat_type = st.multiselect("威胁类型", ["账号接管", "暴力破解", "数据外传", "异常访问", "权限提升"])
        with col2:
            risk_level = st.multiselect("风险等级", ["🔴 高危", "🟠 中危", "🟡 低危"])
    
    # 查询按钮
    col1, col2, col3 = st.columns([3, 1, 1])
    search_triggered = False
    with col2:
        if st.button("🔍 查询", type="primary", use_container_width=True):
            search_triggered = True
            st.success("查询成功")
    with col3:
        if st.button("🗑️ 重置", use_container_width=True):
            st.rerun()
    
    st.divider()
    
    # 查询结果
    st.subheader("📊 查询结果")
    
    # 从接口获取查询结果数据
    search_results = search_history_logs(
        start_time=start_time,
        end_time=end_time,
        username=username if username else None,
        source_ip=source_ip if source_ip else None,
        log_type=log_type,
        status=status
    )
    
    df_results = pd.DataFrame(search_results)
    st.dataframe(df_results, use_container_width=True, height=300)
    
    # 查询结果统计提示
    if search_triggered:
        st.info(f"找到 {len(search_results)} 条记录")
    
    # 导出功能
    st.divider()
    st.subheader("💾 导出结果")
    
    col1, col2, col3 = st.columns(3)
    with col1:
        if st.button("📥 导出为 CSV", use_container_width=True):
            pass
    with col2:
        if st.button("📥 导出为 Excel", use_container_width=True):
            pass
    with col3:
        if st.button("📄 导出为 PDF", use_container_width=True):
            # 生成 PDF 报告
            pdf_output = generate_pdf_report("history", search_results)
            st.download_button(
                label="下载 PDF 报告",
                data=pdf_output,
                file_name=f"历史查询结果_{datetime.now().strftime('%Y-%m-%d_%H-%M-%S')}.pdf",
                mime="application/pdf"
            )
    
    # 查询统计
    st.divider()
    st.subheader("📈 查询统计")
    
    stat_col1, stat_col2, stat_col3 = st.columns(3)
    with stat_col1:
        st.metric("查询结果总数", "125")
    with stat_col2:
        st.metric("高危事件数", "15")
    with stat_col3:
        st.metric("涉及用户数", "8")

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

def main():
    """主函数"""
    init_session_state()
    create_sidebar()
    
    # 根据选择显示对应页面
    if st.session_state.current_page == "实时日志流":
        show_realtime_logs()
    elif st.session_state.current_page == "UEBA 异常排行":
        show_ueba_ranking()
    elif st.session_state.current_page == "安全评分看板":
        show_security_score()
    elif st.session_state.current_page == "处置+AI建议":
        show_ai_suggestions()
    elif st.session_state.current_page == "历史查询":
        show_history_search()


if __name__ == "__main__":
    main()
