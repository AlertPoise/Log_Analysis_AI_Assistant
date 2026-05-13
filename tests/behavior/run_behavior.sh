#!/bin/bash

# Behavior 模块一键运行脚本
# 使用方法：
#   bash tests/behavior/run_behavior.sh
# 或：
#   chmod +x tests/behavior/run_behavior.sh && ./tests/behavior/run_behavior.sh

set -e

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
CYAN='\033[0;36m'
NC='\033[0m'

SCRIPT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" &>/dev/null && pwd)
PROJECT_ROOT=$(cd "$SCRIPT_DIR/../.." &>/dev/null && pwd)
PYTHON_BIN=""

print_header() {
    echo -e "${CYAN}================================================${NC}"
    echo -e "${CYAN}  Behavior 模块一键运行脚本${NC}"
    echo -e "${CYAN}================================================${NC}"
}

print_ok() {
    echo -e "${GREEN}[OK]${NC} $1"
}

print_warn() {
    echo -e "${YELLOW}[WARN]${NC} $1"
}

print_error() {
    echo -e "${RED}[ERROR]${NC} $1"
}

detect_python() {
    if command -v python3 &>/dev/null; then
        PYTHON_BIN="python3"
        return
    fi

    if command -v python &>/dev/null; then
        PYTHON_BIN="python"
        return
    fi

    print_error "未找到 python3 或 python，请先安装 Python 3.9+"
    exit 1
}

activate_venv() {
    if [ -d "$PROJECT_ROOT/.venv" ]; then
        # shellcheck disable=SC1091
        source "$PROJECT_ROOT/.venv/bin/activate"
        print_ok "已激活虚拟环境: $PROJECT_ROOT/.venv"
        return
    fi

    if [ -d "$PROJECT_ROOT/venv" ]; then
        # shellcheck disable=SC1091
        source "$PROJECT_ROOT/venv/bin/activate"
        print_ok "已激活虚拟环境: $PROJECT_ROOT/venv"
        return
    fi

    print_warn "未找到 .venv/ 或 venv/，将使用当前系统 Python 环境"
}

check_environment() {
    echo -e "${BLUE}项目根目录:${NC} $PROJECT_ROOT"

    activate_venv
    detect_python

    echo -e "${BLUE}Python 解释器:${NC} $PYTHON_BIN"
    echo -e "${BLUE}Python 版本:${NC} $($PYTHON_BIN --version)"

    if ! "$PYTHON_BIN" -m pytest --version &>/dev/null; then
        print_error "当前环境未安装 pytest"
        echo "请先在当前环境中安装依赖后重试"
        exit 1
    fi

    print_ok "pytest 可用"
}

run_formal_tests() {
    echo -e "${BLUE}运行正式行为模块测试...${NC}"
    cd "$PROJECT_ROOT"
    "$PYTHON_BIN" -m pytest -v tests/behavior
}

run_behavior_demo() {
    echo -e "${BLUE}运行 Behavior 模块完整流程测试（含过程展示）...${NC}"
    cd "$PROJECT_ROOT"
    "$PYTHON_BIN" -m pytest -v -s tests/behavior/test_behavior.py
}

run_unit_tests() {
    echo -e "${BLUE}运行 Behavior 模块核心单元测试...${NC}"
    cd "$PROJECT_ROOT"
    "$PYTHON_BIN" -m pytest -v tests/behavior/test_behavior_unit.py
}

configure_behavior() {
    echo -e "${BLUE}打开 Behavior 模块交互式配置...${NC}"
    cd "$PROJECT_ROOT"
    "$PYTHON_BIN" tests/behavior/behavior_config_cli.py
}

show_behavior_config() {
    echo -e "${BLUE}显示当前 Behavior 配置...${NC}"
    cd "$PROJECT_ROOT"
    "$PYTHON_BIN" tests/behavior/behavior_config_cli.py --show
}

reset_behavior_config() {
    echo -e "${BLUE}重置 Behavior 配置为默认值...${NC}"
    cd "$PROJECT_ROOT"
    "$PYTHON_BIN" tests/behavior/behavior_config_cli.py --reset
}

show_info() {
    echo -e "${BLUE}Behavior 模块相关路径:${NC}"
    echo "  - 模块目录: $PROJECT_ROOT/src/behavior"
    echo "  - 行为配置文件: $PROJECT_ROOT/config/behavior.env"
    echo "  - 正式测试目录: $PROJECT_ROOT/tests/behavior"
    echo "  - 行为脚本目录: $PROJECT_ROOT/tests/behavior"
    echo ""
    echo -e "${BLUE}推荐命令:${NC}"
    echo "  $PYTHON_BIN -m pytest -v tests/behavior"
    echo "  $PYTHON_BIN -m pytest -v tests/behavior/test_behavior_unit.py"
    echo "  $PYTHON_BIN -m pytest -v -s tests/behavior/test_behavior.py"
    echo "  $PYTHON_BIN tests/behavior/behavior_config_cli.py --show"
}

print_menu() {
    echo ""
    echo -e "${YELLOW}请选择要执行的操作:${NC}"
    echo "1. 运行正式行为模块测试 (tests/behavior)"
    echo "2. 运行 Behavior 模块完整流程测试（带过程展示）"
    echo "3. 运行 Behavior 模块核心单元测试"
    echo "4. 交互式配置 Behavior 参数"
    echo "5. 显示当前 Behavior 配置"
    echo "6. 重置 Behavior 配置为默认值"
    echo "7. 显示路径和推荐命令"
    echo "8. 退出"
    echo ""
}

main() {
    print_header
    check_environment
    print_menu
    read -r -p "请输入选项 (1-8): " choice

    case "$choice" in
        1)
            run_formal_tests
            ;;
        2)
            run_behavior_demo
            ;;
        3)
            run_unit_tests
            ;;
        4)
            configure_behavior
            ;;
        5)
            show_behavior_config
            ;;
        6)
            reset_behavior_config
            ;;
        7)
            show_info
            ;;
        8)
            echo "已退出"
            ;;
        *)
            print_error "无效选项: $choice"
            exit 1
            ;;
    esac
}

main
