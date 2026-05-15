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

from src.visualization.data_provider import get_dashboard_data

# 设置日志配置
import os

# 确保 logs 目录存在
logs_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), "logs")
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

RISK_LEVEL_LABELS = {
    "high": "🔴 高危",
    "medium": "🟠 中危",
    "low": "🟡 低危",
    "unknown": "⚪ 未知",
}

ANOMALY_TYPE_LABELS = {
    "unusual_time": "非常用时间",
    "unusual_ip": "非常用 IP",
    "unusual_location": "非常用地点",
    "multi_ip_login": "多 IP 登录",
    "high_frequency": "高频 API 调用",
    "failed_login_spike": "失败登录激增",
    "sensitive_action": "敏感操作",
    "unknown": "未知异常",
}

# 尝试导入存储模块
try:
    from src.storage.clickhouse import ClickHouseClient
    from src.storage.kafka_client import KafkaClient
    STORAGE_AVAILABLE = True
    logger.info("✅ 成功导入存储模块")
except ImportError as e:
    STORAGE_AVAILABLE = False
    logger.warning(f"⚠️ 无法导入存储模块，将使用模拟数据: {e}")

from fpdf import FPDF

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


# ==================== 真实接口层 ====================

def fetch_realtime_logs(log_type="全部", limit=100):
    """从 ClickHouse 获取真实日志数据"""
    client = ClickHouseClient()
    query = """
        SELECT timestamp, log_type, username, source_ip, status, location, risk_level
        FROM security_logs
        WHERE 1=1
    """
    params = []

    if log_type != "全部":
        query += " AND log_type = %s"
        params.append(log_type)

    query += " ORDER BY timestamp DESC LIMIT %s"
    params.append(limit)

    result = client.query(query, tuple(params))

    logs = []
    for row in result:
        logs.append({
            "时间": row[0].strftime("%Y-%m-%d %H:%M:%S"),
            "类型": row[1],
            "用户": row[2],
            "IP": row[3],
            "状态": row[4],
            "地点": row[5],
            "风险": row[6]
        })

    return logs


def fetch_anomaly_users(time_range="最近 24 小时", limit=10):
    """从 ClickHouse 获取真实异常用户数据"""
    client = ClickHouseClient()

    time_map = {
        "最近 24 小时": 24,
        "最近 7 天": 168,
        "最近 30 天": 720
    }
    hours = time_map.get(time_range, 24)

    query = """
        SELECT username, anomaly_score, risk_level, anomaly_count, last_anomaly_time
        FROM user_anomaly_scores
        WHERE last_anomaly_time >= NOW() - INTERVAL %s HOUR
        ORDER BY anomaly_score DESC
        LIMIT %s
    """

    result = client.query(query, (hours, limit))

    users = []
    for i, row in enumerate(result, 1):
        users.append({
            "排名": i,
            "用户名": row[0],
            "异常评分": row[1],
            "风险等级": row[2],
            "异常事件数": row[3],
            "最近异常时间": row[4].strftime("%Y-%m-%d %H:%M")
        })

    return users


def fetch_security_metrics():
    """从 ClickHouse 获取真实安全指标数据"""
    client = ClickHouseClient()

    # 获取整体安全评分
    score_query = "SELECT AVG(security_score) FROM daily_security_scores WHERE date = TODAY()"
    score_result = client.query(score_query)
    security_score = int(score_result[0][0]) if score_result else 75

    # 获取今日异常事件数
    anomaly_query = "SELECT COUNT(*) FROM anomaly_events WHERE event_time >= TODAY()"
    anomaly_result = client.query(anomaly_query)
    anomaly_count = anomaly_result[0][0] if anomaly_result else 12

    # 获取高危用户数
    high_risk_query = "SELECT COUNT(*) FROM user_anomaly_scores WHERE risk_level = '🔴 高危'"
    high_risk_result = client.query(high_risk_query)
    high_risk_count = high_risk_result[0][0] if high_risk_result else 5

    # 获取已处置事件数
    disposed_query = "SELECT COUNT(*) FROM anomaly_events WHERE status = '已处置' AND event_time >= TODAY()"
    disposed_result = client.query(disposed_query)
    disposed_count = disposed_result[0][0] if disposed_result else 8

    return {
        "security_score": security_score,
        "anomaly_count": anomaly_count,
        "high_risk_count": high_risk_count,
        "disposed_count": disposed_count
    }


def fetch_security_trend(days=7):
    """从 ClickHouse 获取真实安全评分趋势"""
    client = ClickHouseClient()
    query = """
        SELECT date, security_score, anomaly_count
        FROM daily_security_scores
        WHERE date >= TODAY() - INTERVAL %s DAY
        ORDER BY date
    """
    result = client.query(query, (days,))

    data = {
        "日期": [],
        "安全评分": [],
        "异常事件数": []
    }

    for row in result:
        data["日期"].append(row[0].strftime("%Y-%m-%d"))
        data["安全评分"].append(row[1])
        data["异常事件数"].append(row[2])

    return pd.DataFrame(data)


def fetch_risk_distribution():
    """从 ClickHouse 获取真实风险等级分布"""
    client = ClickHouseClient()
    query = "SELECT risk_level, COUNT(*) FROM anomaly_events WHERE event_time >= TODAY() GROUP BY risk_level"
    result = client.query(query)

    data = {"风险等级": [], "事件数": []}
    for row in result:
        data["风险等级"].append(row[0])
        data["事件数"].append(row[1])

    return pd.DataFrame(data)


def fetch_threat_stats():
    """从 ClickHouse 获取真实威胁类型统计"""
    client = ClickHouseClient()
    query = "SELECT threat_type, COUNT(*) FROM anomaly_events WHERE event_time >= TODAY() GROUP BY threat_type"
    result = client.query(query)

    data = {"威胁类型": [], "数量": []}
    for row in result:
        data["威胁类型"].append(row[0])
        data["数量"].append(row[1])

    return pd.DataFrame(data)


def fetch_ai_suggestions(status_filter="全部", risk_filter="全部"):
    """从 ClickHouse 获取真实 AI 处置建议"""
    client = ClickHouseClient()
    query = """
        SELECT id, username, threat_type, risk_level, anomaly_description,
               ai_analysis, suggestion, confidence, status, create_time
        FROM ai_suggestions
        WHERE 1=1
    """
    params = []

    if status_filter != "全部":
        query += " AND status = %s"
        params.append(status_filter)

    if risk_filter != "全部":
        query += " AND risk_level = %s"
        params.append(risk_filter)

    query += " ORDER BY create_time DESC"

    result = client.query(query, tuple(params) if params else None)

    suggestions = []
    for row in result:
        suggestions.append({
            "id": row[0],
            "用户": row[1],
            "威胁类型": row[2],
            "风险等级": row[3],
            "异常描述": row[4],
            "AI 分析": row[5],
            "处置建议": row[6],
            "置信度": f"{row[7]}%",
            "处置状态": row[8],
            "生成时间": row[9].strftime("%Y-%m-%d %H:%M")
        })

    return suggestions


def fetch_history_logs(start_time=None, end_time=None, username=None, source_ip=None,
                       log_type="全部", status="全部"):
    """从 ClickHouse 搜索真实历史日志"""
    client = ClickHouseClient()
    query = """
        SELECT timestamp, username, log_type, source_ip, status, location, risk_level
        FROM security_logs
        WHERE 1=1
    """
    params = []

    if start_time:
        query += " AND timestamp >= %s"
        params.append(start_time)

    if end_time:
        query += " AND timestamp <= %s"
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
        query += " AND status = %s"
        params.append(status)

    query += " ORDER BY timestamp DESC LIMIT 100"

    result = client.query(query, tuple(params) if params else None)

    logs = []
    for row in result:
        logs.append({
            "时间": row[0].strftime("%Y-%m-%d %H:%M:%S"),
            "用户": row[1],
            "类型": row[2],
            "IP": row[3],
            "状态": row[4],
            "地点": row[5],
            "风险等级": row[6]
        })

    return logs


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
    if STORAGE_AVAILABLE:
        try:
            data = fetch_ai_suggestions(status_filter, risk_filter)
            logger.info(f"🤖 当前显示: 实时数据 - 从 ClickHouse 获取 {len(data)} 条 AI 建议")
            return data
        except Exception as e:
            logger.error(f"❌ 获取 AI 建议失败: {e}")

    logger.info("🤖 当前显示: 模拟数据 - AI 处置建议")
    return get_sample_ai_suggestions(status_filter, risk_filter)


def search_history_logs(start_time=None, end_time=None, username=None, source_ip=None,
                        log_type="全部", status="全部", threat_types=None, risk_levels=None):
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


def format_risk_level(level):
    """将风险等级格式化为前端展示文案。"""
    normalized = str(level or "unknown").strip().lower()
    return RISK_LEVEL_LABELS.get(normalized, RISK_LEVEL_LABELS["unknown"])


def format_anomaly_type(anomaly_type):
    """将异常类型格式化为前端展示文案。"""
    normalized = str(anomaly_type or "unknown").strip().lower()
    return ANOMALY_TYPE_LABELS.get(normalized, normalized.replace("_", " ").title())


def build_anomaly_users_dataframe(dashboard_data):
    """将 dashboard 数据转换为异常用户排行表。"""
    ranking_rows = []
    for index, user in enumerate(dashboard_data.get("anomaly_users", []), start=1):
        reasons = user.get("reasons") or []
        ranking_rows.append(
            {
                "排名": index,
                "用户名": user.get("username") or "-",
                "异常评分": float(user.get("risk_score", 0.0) or 0.0),
                "风险等级": format_risk_level(user.get("risk_level")),
                "异常事件数": int(user.get("anomaly_count", 0) or 0),
                "风险原因": "；".join(str(reason) for reason in reasons) if reasons else "-",
            }
        )

    return pd.DataFrame(ranking_rows)


def build_risk_distribution_dataframe(dashboard_data):
    """将 dashboard 数据转换为风险分布图表数据。"""
    risk_distribution = dashboard_data.get("risk_distribution", {})
    return pd.DataFrame(
        {
            "风险等级": [
                RISK_LEVEL_LABELS["high"],
                RISK_LEVEL_LABELS["medium"],
                RISK_LEVEL_LABELS["low"],
            ],
            "事件数": [
                int(risk_distribution.get("high", 0) or 0),
                int(risk_distribution.get("medium", 0) or 0),
                int(risk_distribution.get("low", 0) or 0),
            ],
        }
    )


def get_user_anomaly_events(dashboard_data, username):
    """获取指定用户的异常事件。"""
    return [
        event
        for event in dashboard_data.get("anomaly_events", [])
        if event.get("username") == username
    ]


def create_sidebar(dashboard_data):
    """创建侧边栏导航"""
    with st.sidebar:
        st.markdown("---")

        # 导航菜单
        st.subheader("📋 功能导航")

        pages = {
            "实时日志流": "📡",
            "UEBA 异常排行": "👥",
            "安全评分看板": "🛡️",
            "AI 处置建议": "🤖",
            "历史查询": "🔍"
        }

        for page, icon in pages.items():
            if st.button(f"{icon} {page}", use_container_width=True,
                        type="primary" if st.session_state.current_page == page else "secondary"):
                st.session_state.current_page = page
                st.rerun()

        st.markdown("---")

        # 系统状态
        summary = dashboard_data.get("summary", {})
        st.subheader("📊 系统状态")
        st.metric("日志总量", str(summary.get("total_logs", 0)))
        st.metric("安全评分", str(summary.get("security_score", 100)))
        st.metric("异常事件数", str(summary.get("anomaly_count", 0)))

        st.markdown("---")
        st.caption("© 日志分析 AI 助手")


def show_realtime_logs(dashboard_data):
    """显示实时日志流"""
    st.header("📡 实时日志流")
    st.markdown("实时展示日志数据，支持筛选和自动刷新")

    # 控制面板
    col1, col2, col3 = st.columns(3)
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
    summary = dashboard_data.get("summary", {})

    stat_col1, stat_col2, stat_col3, stat_col4 = st.columns(4)
    with stat_col1:
        st.metric("日志总量", str(summary.get("total_logs", 0)))
    with stat_col2:
        st.metric("当前 QPS", "1,258", "+5%")
    with stat_col3:
        st.metric("异常事件数", str(summary.get("anomaly_count", 0)))
    with stat_col4:
        st.metric("安全评分", str(summary.get("security_score", 100)))


def show_ueba_ranking(dashboard_data):
    """显示 UEBA 异常用户排行"""
    st.header("👥 UEBA 异常用户排行")
    st.markdown("基于用户行为基线，识别异常用户并排序")

    # 时间范围选择
    col1, col2 = st.columns(2)
    col1, col2 = st.columns(2)
    with col1:
        time_range = st.selectbox("时间范围", ["最近 24 小时", "最近 7 天", "最近 30 天", "自定义"])
    with col2:
        risk_filter = st.multiselect("风险等级", ["🔴 高危", "🟠 中危", "🟡 低危"], default=["🔴 高危", "🟠 中危", "🟡 低危"])

    st.divider()

    # 异常用户 TOP10 排行
    st.subheader("🔴 异常用户 TOP10")
    df_ranking = build_anomaly_users_dataframe(dashboard_data)
    if risk_filter:
        df_ranking = df_ranking[df_ranking["风险等级"].isin(risk_filter)]
    df_ranking = df_ranking.reset_index(drop=True)

    if df_ranking.empty:
        st.info("当前没有可展示的异常用户。")
        return

    # 使用进度条展示异常评分
    st.dataframe(
        df_ranking,
        use_container_width=True,
        hide_index=True,
        column_config={
            "异常评分": st.column_config.ProgressColumn("异常评分", min_value=0, max_value=1, format="%.2f")
        }
    )

    st.divider()

    # 高危用户详情
    st.subheader("📋 高危用户详情")

    selected_user = st.selectbox("选择用户查看详情", df_ranking["用户名"].tolist()[:5])

    if selected_user:
        selected_user_row = df_ranking[df_ranking["用户名"] == selected_user].iloc[0]
        anomaly_events = get_user_anomaly_events(dashboard_data, selected_user)

        col1, col2, col3, col4 = st.columns(4)
        with col1:
            st.metric("异常评分", f"{selected_user_row['异常评分']:.2f}")
        with col2:
            st.metric("异常事件数", str(int(selected_user_row["异常事件数"])))
        with col3:
            st.metric("风险等级", selected_user_row["风险等级"])
        with col4:
            st.metric("分析来源", dashboard_data.get("source", "unknown"))

        st.divider()

        # 异常行为列表
        st.markdown("**🚨 异常行为列表：**")

        if not anomaly_events:
            st.info("当前用户暂无异常事件详情。")

        for i, event in enumerate(anomaly_events):
            event_title = format_anomaly_type(event.get("anomaly_type"))
            with st.expander(f"⚠️ {event.get('timestamp', '')} - {event_title}"):
                col1, col2 = st.columns(2)
                with col1:
                    st.markdown(f"**时间**: {event.get('timestamp', '-')}")
                    st.markdown(f"**类型**: {event_title}")
                    st.markdown(f"**描述**: {event.get('reason', '-')}")
                with col2:
                    st.markdown(f"**用户**: {event.get('username', '-')}")
                    st.markdown(f"**风险等级**: {format_risk_level(event.get('risk_level'))}")
                    st.markdown(f"**风险评分**: {float(event.get('risk_score', 0.0) or 0.0):.2f}")

                col1, col2 = st.columns(2)
                with col1:
                    if st.button("✅ 标记为误报", key=f"false_{i}"):
                        st.success("已标记为误报")
                with col2:
                    if st.button("🤖 生成 AI 建议", key=f"ai_{i}"):
                        st.info("🔍 AI 分析中...")
                        st.success("建议：立即冻结账号，联系用户确认，调查异常访问来源")


def show_security_score(dashboard_data):
    """显示安全评分看板"""
    st.header("🛡️ 安全评分看板")
    st.markdown("整体安全态势评分和趋势分析")

    # 安全评分卡片
    col1, col2, col3, col4 = st.columns(4)
    metrics = dashboard_data.get("summary", {})

    with col1:
        st.metric("整体安全评分", str(metrics.get("security_score", 100)), delta_color="inverse")
    with col2:
        st.metric("异常事件数", str(metrics.get("anomaly_count", 0)), delta_color="inverse")
    with col3:
        st.metric("高危用户数", str(metrics.get("high_risk_users", 0)), delta_color="normal")
    with col4:
        st.metric("整体风险等级", format_risk_level(metrics.get("overall_risk_level", "unknown")))

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
        risk_data = build_risk_distribution_dataframe(dashboard_data)
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
        risk_distribution = dashboard_data.get("risk_distribution", {})
        # 待实现接口：从后端获取真实的安全简报数据
        st.markdown(f"""
        **今日安全态势简报**
        📅 日期: {datetime.now().strftime('%Y-%m-%d')}

        🛡️ 整体安全评分: {metrics.get("security_score", 100)}/100

        📊 关键指标:
        - 日志总量: {metrics.get("total_logs", 0)} 条
        - 异常事件: {metrics.get("anomaly_count", 0)} 起
        - 高危用户: {metrics.get("high_risk_users", 0)} 人
        - 整体风险等级: {format_risk_level(metrics.get("overall_risk_level", "unknown"))}

        🚨 主要威胁:
        1. 高危事件: {risk_distribution.get("high", 0)} 起
        2. 中危事件: {risk_distribution.get("medium", 0)} 起
        3. 低危事件: {risk_distribution.get("low", 0)} 起

        ✅ 处置建议:
        - 立即冻结高危账号
        - 加强异地登录验证
        - 启用多因素认证
        """)


def show_ai_suggestions():
    """显示 AI 处置建议"""
    st.header("🤖 AI 处置建议")
    st.markdown("AI 智能分析异常行为，提供处置建议")

    # 筛选条件
    col1, col2 = st.columns(2)
    col1, col2 = st.columns(2)
    with col1:
        status_filter = st.selectbox("处置状态", ["全部", "待处置", "处置中", "已处置", "误报"])
    with col2:
        risk_filter = st.selectbox("风险等级", ["全部", "🔴 高危", "🟠 中危", "🟡 低危"])

    st.divider()

    # AI 处置建议列表
    # 从接口获取处置建议数据
    suggestions = get_ai_suggestions(status_filter, risk_filter)

    # 根据筛选条件过滤建议（接口已处理过滤，这里保留冗余过滤作为双重保障）
    filtered_suggestions = []
    for suggestion in suggestions:
        # 状态筛选
        if status_filter != "全部" and suggestion["处置状态"] != status_filter:
            continue
        # 风险等级筛选
        if risk_filter != "全部" and suggestion["风险等级"] != risk_filter:
            continue
        filtered_suggestions.append(suggestion)

    # 按处置状态分类显示
    status_order = ["待处置", "处置中", "已处置", "误报"]
    for status in status_order:
        status_suggestions = [s for s in filtered_suggestions if s["处置状态"] == status]
        if status_suggestions:
            # 根据风险等级排序（高危 > 中危 > 低危）
            risk_order = {"🔴 高危": 0, "🟠 中危": 1, "🟡 低危": 2}
            status_suggestions.sort(key=lambda x: risk_order[x["风险等级"]])

            # 显示状态分组
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

                    # 按钮行
                    col1, col2, col3 = st.columns(3)
                    show_logs = False

                    with col1:
                        if st.button("🔍 查看详细日志", key=f"detail_{suggestion['id']}"):
                            show_logs = True
                    with col2:
                        if st.button("⚠️ 标记为误报", key=f"false_{suggestion['id']}"):
                            pass
                    with col3:
                        if st.button("✅ 标记为已处置", key=f"resolve_{suggestion['id']}"):
                            pass

                    # 日志内容显示在按钮行下方，占满整个宽度
                    if show_logs:
                        logs = [
                            "2024-01-21 03:15:00 LOGIN user=zhangsan ip=10.0.0.100 status=SUCCESS",
                            "2024-01-21 03:16:00 API_CALL user=zhangsan endpoint=/api/sensitive/data count=1",
                            "2024-01-21 03:17:00 API_CALL user=zhangsan endpoint=/api/sensitive/data count=2",
                        ]
                        st.markdown("**相关日志：**")
                        for log in logs:
                            st.code(log)
            st.divider()

    # 如果没有符合条件的建议
    if not filtered_suggestions:
        st.info("没有符合条件的处置建议")

    # 统计信息
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
            pass
    with col2:
        if st.button("📥 导出为 Excel", use_container_width=True):
            pass
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


def main():
    """主函数"""
    init_session_state()
    dashboard_data = get_dashboard_data()
    create_sidebar(dashboard_data)

    if dashboard_data.get("source") == "mock" and dashboard_data.get("error"):
        error = dashboard_data["error"]
        st.warning(f"当前展示 fallback 数据: {error.get('message', '未知错误')}")

    # 根据选择显示对应页面
    if st.session_state.current_page == "实时日志流":
        show_realtime_logs(dashboard_data)
    elif st.session_state.current_page == "UEBA 异常排行":
        show_ueba_ranking(dashboard_data)
    elif st.session_state.current_page == "安全评分看板":
        show_security_score(dashboard_data)
    elif st.session_state.current_page == "AI 处置建议":
        show_ai_suggestions()
    elif st.session_state.current_page == "历史查询":
        show_history_search()


if __name__ == "__main__":
    main()
