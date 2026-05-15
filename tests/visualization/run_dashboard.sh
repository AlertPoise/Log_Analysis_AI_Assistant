#!/bin/bash

# 可视化仪表板启动脚本Linux版本
# 使用方法：chmod +x run_dashboard.sh给执行权限
# ./tests/visualization/run_dashboard.sh

echo "========================================"
echo "  日志分析 AI 助手 - 可视化仪表板"
echo "========================================"
echo ""

# 检查 Python 环境
echo "[1/4] 检查 Python 环境..."
if ! command -v python3 &> /dev/null; then
    echo "❌ Python 3 未安装，请先安装 Python 3.9 或更高版本"
    echo ""
    echo "在 Debian/Ubuntu 上执行：sudo apt update && sudo apt install python3 python3-venv python3-pip"
    echo "在 CentOS/RHEL 上执行：sudo yum install python3 python3-venv python3-pip"
    echo ""
    exit 1
fi

python_version=$(python3 --version 2>&1)
echo "✓ Python 已安装：$python_version"

# 检查虚拟环境是否存在
if [ ! -d "./venv" ]; then
    echo ""
    echo "[2/4] 虚拟环境未找到，正在创建..."
    python3 -m venv venv
    if [ $? -ne 0 ]; then
        echo "✗ 虚拟环境创建失败，请检查权限"
        exit 1
    fi
    echo "✓ 虚拟环境创建成功"
else
    echo "[2/4] 虚拟环境已存在"
fi

# 激活虚拟环境
echo "[3/4] 激活虚拟环境..."
source venv/bin/activate
if [ $? -ne 0 ]; then
    echo "✗ 虚拟环境激活失败"
    exit 1
fi

# 安装项目依赖
echo "[4/4] 安装项目依赖..."
if [ -f "requirements.txt" ]; then
    pip install -r requirements.txt
    if [ $? -ne 0 ]; then
        echo "✗ 依赖安装失败，请检查错误信息"
        exit 1
    fi
    echo "✓ 项目依赖安装完成"
fi

# 启动 Streamlit
echo ""
echo "========================================"
echo "  🔄 启动可视化仪表板..."
echo "========================================"
echo ""

# 运行 Streamlit
streamlit run src/visualization/dashboard.py --server.port 8501 --server.address localhost

# 如果运行失败
if [ $? -ne 0 ]; then
    echo ""
    echo "✗ 仪表板运行失败，请检查错误信息"
    exit 1
fi