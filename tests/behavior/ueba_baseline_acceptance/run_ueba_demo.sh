#!/usr/bin/env bash
set -u

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/../../.." && pwd)"
STATE_ROOT="${UEBA_DEMO_STATE_ROOT:-${PROJECT_ROOT}/.tox/manual/ueba_demo_menu}"
BASELINE_OUTPUT_DIR="${STATE_ROOT}/baseline_acceptance"
TRAINING_OUTPUT_DIR="${STATE_ROOT}/training_update"
PYTHON_BIN="${PROJECT_ROOT}/.venv/bin/python"

SERVER_MODULE="tests.behavior.ueba_baseline_acceptance.continuous_login_http_server"
BASELINE_RUNNER_MODULE="tests.behavior.ueba_baseline_acceptance.runner"
TRAINING_RUNNER_MODULE="tests.behavior.ueba_baseline_acceptance.manual_training_update_runner"
CONTINUOUS_ACCEPTANCE_MODULE="tests.behavior.ueba_baseline_acceptance.run_continuous_validation_acceptance"

SERVER_PID_FILE="${STATE_ROOT}/continuous_login_http_server.pid"
SERVER_LOG_FILE="${STATE_ROOT}/continuous_login_http_server.log"
SERVER_STATUS_FILE="${STATE_ROOT}/server_status.json"
LAST_HTTP_RESPONSE_FILE="${STATE_ROOT}/last_http_response.json"
LAST_VALIDATION_RESULT_FILE="${STATE_ROOT}/last_validation_result.json"
CONTINUOUS_ACCEPTANCE_REPORT="${STATE_ROOT}/continuous_validation_acceptance_report.json"
VALIDATION_HISTORY_FILE="${STATE_ROOT}/validation_history.tsv"
SUMMARY_LOG_FILE="${STATE_ROOT}/demo_summary.log"
CURRENT_WINDOW_START_FILE="${STATE_ROOT}/current_window_start"
CURRENT_WINDOW_END_FILE="${STATE_ROOT}/current_window_end"
CURRENT_WINDOW_STATE_FILE="${STATE_ROOT}/current_window_state"

CURRENT_SESSION_SERVER_PID=""

WINDOW_STATE_IDLE="IDLE"
WINDOW_STATE_ACTIVE="ACTIVE"
WINDOW_STATE_CLOSED="CLOSED"

CONTINUOUS_HOST="127.0.0.1"
CONTINUOUS_PORT="8765"
CONTINUOUS_USERNAME="fixture_user_stable_0001"
CONTINUOUS_MARKER="ueba_continuous_fixture"
CONTINUOUS_MODEL_VERSION="ueba_baseline_fixture_v2_monthly"
CONTINUOUS_LOG_TYPE="vpn"
CONTINUOUS_RUN_SECONDS="10"
WINDOW_FLUSH_SECONDS="5"

CLICKHOUSE_HOST="${CLICKHOUSE_HOST:-localhost}"
CLICKHOUSE_PORT="${CLICKHOUSE_PORT:-8123}"
CLICKHOUSE_USERNAME="${CLICKHOUSE_USERNAME:-${CLICKHOUSE_USER:-default}}"
CLICKHOUSE_PASSWORD="${CLICKHOUSE_PASSWORD:-}"
CLICKHOUSE_DATABASE="${CLICKHOUSE_DATABASE:-log_analysis}"

export CLICKHOUSE_HOST CLICKHOUSE_PORT CLICKHOUSE_USERNAME CLICKHOUSE_PASSWORD CLICKHOUSE_DATABASE
export CLICKHOUSE_USER="${CLICKHOUSE_USERNAME}"

ensure_state_dirs() {
    mkdir -p "${STATE_ROOT}" "${BASELINE_OUTPUT_DIR}" "${TRAINING_OUTPUT_DIR}"
}

require_venv_python() {
    if [ ! -x "${PYTHON_BIN}" ]; then
        echo "错误：未找到可执行虚拟环境 Python：${PYTHON_BIN}"
        echo "请先在项目根目录创建并安装 .venv。"
        return 1
    fi
    return 0
}

now_utc() {
    date -u '+%Y-%m-%d %H:%M:%S'
}

print_divider() {
    printf '\n============================================================\n'
}

record_summary() {
    printf '[%s] %s\n' "$(now_utc)" "$*" >> "${SUMMARY_LOG_FILE}"
}

read_input() {
    local __result_var="$1"
    local prompt="$2"
    local eof_message="${3:-检测到输入结束，已取消当前操作。}"
    local value=""

    if ! IFS= read -r -p "${prompt}" value; then
        echo
        echo "${eof_message}"
        return 1
    fi

    printf -v "${__result_var}" '%s' "${value}"
    return 0
}

confirm_token() {
    local expected="$1"
    local prompt="$2"
    local answer=""

    if ! read_input answer "${prompt}" "检测到输入结束，已取消当前操作。"; then
        return 1
    fi
    if [ "${answer}" != "${expected}" ]; then
        echo "已取消当前操作。"
        return 1
    fi
    return 0
}

pretty_print_json_file() {
    local file_path="$1"
    if [ ! -f "${file_path}" ]; then
        echo "文件不存在：${file_path}"
        return 1
    fi
    if ! "${PYTHON_BIN}" -m json.tool "${file_path}" 2>/dev/null; then
        cat "${file_path}"
    fi
    return 0
}

json_get() {
    local key="$1"
    "${PYTHON_BIN}" -c '
import json
import sys

if len(sys.argv) < 2:
    sys.exit(1)
key = sys.argv[1]
raw = sys.stdin.read().strip()
if not raw:
    sys.exit(1)
try:
    obj = json.loads(raw)
except json.JSONDecodeError:
    sys.exit(2)
value = obj.get(key)
if isinstance(value, bool):
    print("true" if value else "false")
elif value is None:
    print("")
else:
    print(value)
' "${key}"
}

clickhouse_base_url() {
    printf 'http://%s:%s' "${CLICKHOUSE_HOST}" "${CLICKHOUSE_PORT}"
}

clickhouse_curl() {
    if [ -n "${CLICKHOUSE_PASSWORD}" ]; then
        curl -f -sS --user "${CLICKHOUSE_USERNAME}:${CLICKHOUSE_PASSWORD}" "$@"
        return
    fi
    if [ "${CLICKHOUSE_USERNAME}" != "default" ]; then
        curl -f -sS --user "${CLICKHOUSE_USERNAME}:" "$@"
        return
    fi
    curl -f -sS "$@"
}

clickhouse_query() {
    local sql="$1"
    clickhouse_curl --data-binary "${sql}" "$(clickhouse_base_url)/?database=${CLICKHOUSE_DATABASE}"
}

clickhouse_scalar() {
    local sql="$1"
    local raw=""
    if ! raw="$(clickhouse_query "${sql}" 2>/dev/null)"; then
        return 1
    fi
    printf '%s\n' "${raw}" | tr -d '\r' | head -n 1
}

is_non_negative_int() {
    case "$1" in
        ''|*[!0-9]*) return 1 ;;
        *) return 0 ;;
    esac
}

require_nonnegative_integer() {
    local value="$1"
    local label="$2"

    if [[ ! "${value}" =~ ^[0-9]+$ ]]; then
        echo "错误：${label} 查询结果不是非负整数（got='${value}'），拒绝继续。" >&2
        return 1
    fi
    return 0
}

is_valid_positive_pid() {
    [[ "$1" =~ ^[1-9][0-9]*$ ]]
}

read_server_pid() {
    if [ -f "${SERVER_PID_FILE}" ]; then
        cat "${SERVER_PID_FILE}"
    fi
}

pid_is_alive() {
    local pid="$1"
    if ! is_valid_positive_pid "${pid}"; then
        return 1
    fi
    kill -0 -- "${pid}" 2>/dev/null
}

server_is_running() {
    has_current_session_server
}

port_is_open() {
    "${PYTHON_BIN}" - "$1" "$2" <<'PYPORT'
import socket
import sys

host = sys.argv[1]
port = int(sys.argv[2])
sock = socket.socket()
sock.settimeout(0.25)
try:
    sock.connect((host, port))
except OSError:
    print("0")
else:
    print("1")
finally:
    sock.close()
PYPORT
}

port_state_text() {
    local open_flag=""
    open_flag="$(port_is_open "${CONTINUOUS_HOST}" "${CONTINUOUS_PORT}")"
    if [ "${open_flag}" != "1" ]; then
        echo "空闲"
        return
    fi
    if server_is_running; then
        echo "由本工具占用"
        return
    fi
    echo "被其他进程占用"
}

get_window_state() {
    if [ -f "${CURRENT_WINDOW_STATE_FILE}" ]; then
        cat "${CURRENT_WINDOW_STATE_FILE}"
    fi
}

get_window_start() {
    if [ -f "${CURRENT_WINDOW_START_FILE}" ]; then
        cat "${CURRENT_WINDOW_START_FILE}"
    fi
}

get_window_end() {
    if [ -f "${CURRENT_WINDOW_END_FILE}" ]; then
        cat "${CURRENT_WINDOW_END_FILE}"
    fi
}

write_window_state() {
    printf '%s\n' "$1" > "${CURRENT_WINDOW_STATE_FILE}"
}

write_window_start() {
    printf '%s\n' "$1" > "${CURRENT_WINDOW_START_FILE}"
}

write_window_end() {
    printf '%s\n' "$1" > "${CURRENT_WINDOW_END_FILE}"
}

clear_window_files() {
    rm -f "${CURRENT_WINDOW_START_FILE}" "${CURRENT_WINDOW_END_FILE}" "${CURRENT_WINDOW_STATE_FILE}"
}

set_window_idle() {
    rm -f "${CURRENT_WINDOW_START_FILE}" "${CURRENT_WINDOW_END_FILE}"
    write_window_state "${WINDOW_STATE_IDLE}"
}

open_active_window() {
    local start_time="$1"
    write_window_start "${start_time}"
    rm -f "${CURRENT_WINDOW_END_FILE}"
    write_window_state "${WINDOW_STATE_ACTIVE}"
}

close_window() {
    local end_time="$1"
    write_window_end "${end_time}"
    write_window_state "${WINDOW_STATE_CLOSED}"
}

append_validation_history() {
    local run_id="$1"
    local start_time="$2"
    local end_time="$3"
    printf '%s\t%s\t%s\n' "${run_id}" "${start_time}" "${end_time}" >> "${VALIDATION_HISTORY_FILE}"
}

server_status_json() {
    http_get_status
}

http_get_status() {
    curl -f -sS --max-time 3 "http://${CONTINUOUS_HOST}:${CONTINUOUS_PORT}/status"
}

http_post_json() {
    local path="$1"
    local payload="$2"
    curl -f -sS --max-time 5 \
        -X POST \
        -H 'Content-Type: application/json' \
        --data "${payload}" \
        "http://${CONTINUOUS_HOST}:${CONTINUOUS_PORT}${path}"
}

is_current_session_server_pid() {
    local pid="$1"

    is_valid_positive_pid "${pid}" || return 1
    [ -n "${CURRENT_SESSION_SERVER_PID}" ] || return 1
    [ "${pid}" = "${CURRENT_SESSION_SERVER_PID}" ] || return 1

    pid_is_alive "${pid}" || return 1

    jobs -pr 2>/dev/null | grep -Fx -- "${pid}" >/dev/null 2>&1 || return 1

    return 0
}

get_paused_value() {
    local __result_var="$1"
    local status_json="$2"
    local value=""

    if ! value="$(printf '%s' "${status_json}" | json_get paused)"; then
        echo "错误：无法解析 Server paused 状态（JSON 解析失败）。" >&2
        return 1
    fi

    case "${value}" in
        true|false)
            printf -v "${__result_var}" '%s' "${value}"
            return 0
            ;;
        "")
            echo "错误：Server 状态中缺少 paused 字段。" >&2
            return 1
            ;;
        *)
            echo "错误：Server paused 字段值非法（got='${value}'），期望 true 或 false。" >&2
            return 1
            ;;
    esac
}

paused_value_is_true() {
    local status_json="$1"
    local paused_val=""
    if ! get_paused_value paused_val "${status_json}"; then
        return 2
    fi
    [ "${paused_val}" = "true" ]
}

paused_value_is_false() {
    local status_json="$1"
    local paused_val=""
    if ! get_paused_value paused_val "${status_json}"; then
        return 2
    fi
    [ "${paused_val}" = "false" ]
}

has_current_session_server() {
    [ -n "${CURRENT_SESSION_SERVER_PID}" ] && is_current_session_server_pid "${CURRENT_SESSION_SERVER_PID}"
}

rollback_just_started_server() {
    local pid="$1"

    if ! is_valid_positive_pid "${pid}"; then
        echo "警告：回滚时发现非法 PID='${pid}'，不执行 kill。" >&2
        return 1
    fi

    if [ -z "${CURRENT_SESSION_SERVER_PID}" ] || [ "${pid}" != "${CURRENT_SESSION_SERVER_PID}" ]; then
        echo "警告：回滚 PID=${pid} 与当前会话 PID=${CURRENT_SESSION_SERVER_PID} 不一致，拒绝操作。" >&2
        return 1
    fi

    echo "回滚：停止刚启动的 Server PID=${pid}..."
    kill -INT -- "${pid}" 2>/dev/null || true

    local attempt=0
    while pid_is_alive "${pid}" && [ "${attempt}" -lt 20 ]; do
        sleep 0.25
        attempt=$((attempt + 1))
    done

    if pid_is_alive "${pid}"; then
        echo "警告：回滚等待后 PID=${pid} 仍未退出，保留 PID 文件和会话 PID 供人工排查。" >&2
        return 1
    fi

    rm -f "${SERVER_PID_FILE}"
    CURRENT_SESSION_SERVER_PID=""
    echo "回滚完成，PID=${pid} 已退出，PID 文件和会话 PID 已清理。"
    return 0
}

wait_for_server_ready() {
    local attempt=0
    while [ "${attempt}" -lt 40 ]; do
        if server_status_json > /dev/null 2>&1; then
            return 0
        fi
        sleep 0.25
        attempt=$((attempt + 1))
    done
    return 1
}

require_server_status_json() {
    local __result_var="$1"
    local status_json=""
    if ! status_json="$(server_status_json 2>/dev/null)"; then
        echo "错误：无法读取持续流量 HTTP Server 状态。"
        return 1
    fi
    printf -v "${__result_var}" '%s' "${status_json}"
    return 0
}

status_is_paused() {
    local status_json="$1"
    local paused=""
    paused="$(printf '%s' "${status_json}" | json_get paused 2>/dev/null || true)"
    [ "${paused}" = "true" ]
}

show_server_status() {
    if ! server_is_running; then
        echo "持续流量 HTTP Server：未运行"
        return 0
    fi
    echo "持续流量 HTTP Server 状态："
    local status_json=""
    if status_json="$(server_status_json 2>/dev/null)"; then
        printf '%s\n' "${status_json}" > "${SERVER_STATUS_FILE}"
        pretty_print_json_file "${SERVER_STATUS_FILE}"
        return 0
    fi
    echo "读取状态失败。"
    return 1
}

print_clickhouse_config() {
    echo "ClickHouse host=${CLICKHOUSE_HOST} port=${CLICKHOUSE_PORT} database=${CLICKHOUSE_DATABASE} username=${CLICKHOUSE_USERNAME} password=***"
}

print_window_state() {
    local state=""
    state="$(get_window_state)"
    if [ -z "${state}" ]; then
        echo "当前窗口状态：无"
        return
    fi
    echo "当前窗口状态：${state}"
    if [ -f "${CURRENT_WINDOW_START_FILE}" ]; then
        echo "window_start=$(get_window_start)"
    fi
    if [ -f "${CURRENT_WINDOW_END_FILE}" ]; then
        echo "window_end=$(get_window_end)"
    fi
}

start_continuous_server() {
    local pid=""
    local existing_state=""
    local paused_rc=0
    existing_state="$(get_window_state)"

    if has_current_session_server; then
        echo "持续流量 HTTP Server 已在运行，PID=${CURRENT_SESSION_SERVER_PID}"
        return 0
    fi

    # Handle stale PID file from previous session
    if [ -f "${SERVER_PID_FILE}" ] && [ -z "${CURRENT_SESSION_SERVER_PID}" ]; then
        if [ "$(port_is_open "${CONTINUOUS_HOST}" "${CONTINUOUS_PORT}")" = "1" ]; then
            echo "错误：${CONTINUOUS_HOST}:${CONTINUOUS_PORT} 被占用且不属于当前菜单会话。"
            echo "PID 文件存在但无法验证归属，拒绝启动以免冲突。"
            return 1
        fi
        echo "检测到历史 PID 文件，端口空闲，已清理。"
        rm -f "${SERVER_PID_FILE}"
    fi

    if [ "$(port_is_open "${CONTINUOUS_HOST}" "${CONTINUOUS_PORT}")" = "1" ]; then
        echo "错误：${CONTINUOUS_HOST}:${CONTINUOUS_PORT} 已被其他进程占用。"
        return 1
    fi
    if [ "${existing_state}" = "${WINDOW_STATE_ACTIVE}" ]; then
        echo "错误：检测到未关闭的 ACTIVE 窗口，请先人工处理窗口状态。"
        return 1
    fi

    echo "正在启动持续流量 HTTP Server（默认 0 条/秒，并在 ready 后立即暂停）..."
    : > "${SERVER_LOG_FILE}"
    (
        cd "${PROJECT_ROOT}" || exit 1
        exec "${PYTHON_BIN}" -m "${SERVER_MODULE}" \
            --host "${CONTINUOUS_HOST}" \
            --port "${CONTINUOUS_PORT}" \
            --logs-per-second 0 \
            --username "${CONTINUOUS_USERNAME}" \
            >> "${SERVER_LOG_FILE}" 2>&1
    ) &
    pid=$!
    CURRENT_SESSION_SERVER_PID="${pid}"
    printf '%s\n' "${pid}" > "${SERVER_PID_FILE}"

    if ! wait_for_server_ready; then
        echo "错误：持续流量 HTTP Server 启动失败，请检查 ${SERVER_LOG_FILE}"
        rollback_just_started_server "${pid}"
        return 1
    fi

    if ! http_post_json "/stop" '{}' > "${LAST_HTTP_RESPONSE_FILE}"; then
        echo "错误：Server ready 后立即暂停失败。"
        rollback_just_started_server "${pid}"
        return 1
    fi
    local status_json=""
    if ! require_server_status_json status_json; then
        rollback_just_started_server "${pid}"
        return 1
    fi
    paused_value_is_true "${status_json}"
    paused_rc=$?
    if [ "${paused_rc}" -ne 0 ]; then
        if [ "${paused_rc}" -eq 2 ]; then
            echo "错误：Server 状态解析失败，拒绝继续。"
        else
            echo "错误：Server 未进入 paused=true 状态，拒绝继续。"
        fi
        rollback_just_started_server "${pid}"
        return 1
    fi

    if [ -z "${existing_state}" ]; then
        write_window_state "${WINDOW_STATE_IDLE}"
    fi

    echo "启动成功：PID=${pid}，地址=http://${CONTINUOUS_HOST}:${CONTINUOUS_PORT}"
    pretty_print_json_file "${LAST_HTTP_RESPONSE_FILE}"
    record_summary "持续流量 HTTP Server 启动成功 pid=${pid} logs_per_second=0 paused=true"
    return 0
}

stop_continuous_server() {
    local pid=""
    local state=""
    local end_time=""
    local paused_rc=0
    local port_occupied=0

    port_occupied="$([ "$(port_is_open "${CONTINUOUS_HOST}" "${CONTINUOUS_PORT}")" = "1" ] && echo 1 || echo 0)"

    if ! has_current_session_server; then
        if [ "${port_occupied}" -eq 1 ]; then
            echo "警告：8765 端口被占用，但不属于当前菜单会话，拒绝停止未知进程。"
            return 1
        fi
        rm -f "${SERVER_PID_FILE}"
        if [ -n "${CURRENT_SESSION_SERVER_PID}" ]; then
            echo "当前会话 PID 无效（进程已退出或不属于本会话）。"
        fi
        echo "持续流量 HTTP Server 当前未运行。"
        return 0
    fi

    pid="${CURRENT_SESSION_SERVER_PID}"
    state="$(get_window_state)"
    echo "先向本菜单 Server 发送 /stop ..."
    http_post_json "/stop" '{}' > "${LAST_HTTP_RESPONSE_FILE}" 2>/dev/null || true

    if [ "${state}" = "${WINDOW_STATE_ACTIVE}" ] && [ -n "$(get_window_start)" ]; then
        local status_json=""
        if ! require_server_status_json status_json; then
            echo "错误：/stop 后无法读取 Server 状态，拒绝关闭窗口。"
            echo "窗口保持 ACTIVE 状态，需人工检查后再操作。"
        else
            paused_value_is_true "${status_json}"
            paused_rc=$?
            if [ "${paused_rc}" -eq 2 ]; then
                echo "错误：/stop 后状态 JSON 解析失败，拒绝关闭窗口。"
                echo "窗口保持 ACTIVE 状态，需人工检查后再操作。"
            elif [ "${paused_rc}" -ne 0 ]; then
                echo "错误：/stop 后 Server paused != true，拒绝关闭窗口。"
                echo "窗口保持 ACTIVE 状态，需人工检查后再操作。"
            else
                echo "停止前等待 ${WINDOW_FLUSH_SECONDS} 秒缓冲，并关闭当前活动窗口..."
                sleep "${WINDOW_FLUSH_SECONDS}"
                end_time="$(now_utc)"
                close_window "${end_time}"
            fi
        fi
    fi

    echo "正在发送 SIGINT 停止持续流量 HTTP Server，PID=${pid}..."
    if ! is_valid_positive_pid "${pid}"; then
        echo "错误：PID='${pid}' 非法，拒绝发送信号。请人工检查。"
        return 1
    fi
    kill -INT -- "${pid}" 2>/dev/null || true

    local attempt=0
    while pid_is_alive "${pid}" && [ "${attempt}" -lt 20 ]; do
        sleep 0.25
        attempt=$((attempt + 1))
    done

    if pid_is_alive "${pid}"; then
        echo "警告：PID=${pid} 在等待后仍未退出，未进一步杀进程。"
        return 1
    fi

    rm -f "${SERVER_PID_FILE}"
    CURRENT_SESSION_SERVER_PID=""
    if [ "$(port_is_open "${CONTINUOUS_HOST}" "${CONTINUOUS_PORT}")" = "1" ]; then
        echo "警告：PID 已退出，但 8765 端口仍被占用，可能已有其他进程接管。"
        return 1
    fi

    echo "持续流量 HTTP Server 已停止，端口已释放。"
    record_summary "持续流量 HTTP Server 已停止 pid=${pid}"
    return 0
}

resume_continuous_server() {
    local state=""
    local status_json=""
    local was_active=0
    local start_time=""
    local paused_rc=0

    if ! has_current_session_server; then
        echo "错误：持续流量 HTTP Server 未运行。"
        return 1
    fi
    state="$(get_window_state)"
    if [ "${state}" = "${WINDOW_STATE_CLOSED}" ]; then
        echo "错误：当前存在 CLOSED 窗口，请先评分或精确 cleanup 后再恢复流量。"
        return 1
    fi
    if ! require_server_status_json status_json; then
        return 1
    fi

    if [ "${state}" = "${WINDOW_STATE_ACTIVE}" ]; then
        was_active=1
    else
        start_time="$(now_utc)"
        open_active_window "${start_time}"
    fi

    echo "正在恢复持续流量..."
    if ! http_post_json "/start" '{}' > "${LAST_HTTP_RESPONSE_FILE}"; then
        if [ "${was_active}" -eq 0 ]; then
            set_window_idle
        fi
        echo "恢复失败。"
        return 1
    fi
    if ! require_server_status_json status_json; then
        if [ "${was_active}" -eq 0 ]; then
            set_window_idle
        fi
        return 1
    fi
    paused_value_is_false "${status_json}"
    paused_rc=$?
    if [ "${paused_rc}" -ne 0 ]; then
        if [ "${was_active}" -eq 0 ]; then
            set_window_idle
        fi
        if [ "${paused_rc}" -eq 2 ]; then
            echo "错误：/start 后状态 JSON 解析失败。"
        else
            echo "错误：/start 后 generator 仍为 paused=true。"
        fi
        return 1
    fi

    pretty_print_json_file "${LAST_HTTP_RESPONSE_FILE}"
    echo "持续流量已恢复。"
    record_summary "持续流量已恢复 state=$(get_window_state) start=$(get_window_start)"
    return 0
}

set_continuous_rate() {
    local rate="$1"
    local state=""
    local status_json=""
    local paused_rc=0
    local created_window=0
    local start_time=""

    if ! is_non_negative_int "${rate}"; then
        echo "错误：日志速率必须为 0..1000 的整数。"
        return 1
    fi
    if [ "${rate}" -gt 1000 ]; then
        echo "错误：日志速率必须为 0..1000 的整数。"
        return 1
    fi
    if ! server_is_running; then
        echo "错误：持续流量 HTTP Server 未运行。"
        return 1
    fi
    state="$(get_window_state)"
    if [ "${state}" = "${WINDOW_STATE_CLOSED}" ] && [ "${rate}" -gt 0 ]; then
        echo "错误：当前存在 CLOSED 窗口，请先评分或 cleanup，再开启新的持续流量。"
        return 1
    fi
    if ! require_server_status_json status_json; then
        return 1
    fi

    # Strict paused parsing: distinguish JSON error from paused=true/false
    paused_value_is_true "${status_json}"
    paused_rc=$?
    if [ "${paused_rc}" -eq 2 ]; then
        echo "错误：无法解析 Server paused 状态，拒绝调速。"
        return 1
    fi

    if [ "${paused_rc}" -ne 0 ]; then
        # paused=false — generator is running
        if [ "${rate}" -gt 0 ] && [ "${state}" != "${WINDOW_STATE_ACTIVE}" ]; then
            start_time="$(now_utc)"
            open_active_window "${start_time}"
            created_window=1
        fi
    fi

    echo "正在调整速率到 ${rate}/秒..."
    if ! http_post_json "/rate" "{\"logs_per_second\": ${rate}}" > "${LAST_HTTP_RESPONSE_FILE}"; then
        if [ "${created_window}" -eq 1 ]; then
            set_window_idle
        fi
        echo "调速失败。"
        return 1
    fi

    pretty_print_json_file "${LAST_HTTP_RESPONSE_FILE}"
    record_summary "持续流量速率调整为 ${rate}/秒 state=$(get_window_state)"
    return 0
}

set_continuous_mode() {
    local mode="$1"
    if ! server_is_running; then
        echo "错误：持续流量 HTTP Server 未运行。"
        return 1
    fi
    echo "正在切换模式到 ${mode}..."
    if ! http_post_json "/mode" "{\"mode\": \"${mode}\"}" > "${LAST_HTTP_RESPONSE_FILE}"; then
        echo "切换模式失败。"
        return 1
    fi
    pretty_print_json_file "${LAST_HTTP_RESPONSE_FILE}"
    record_summary "持续流量模式切换为 ${mode}"
    return 0
}

pause_continuous_server() {
    local state=""
    local status_json=""
    local end_time=""
    local paused_rc=0

    if ! has_current_session_server; then
        echo "错误：持续流量 HTTP Server 未运行。"
        return 1
    fi
    state="$(get_window_state)"

    echo "正在暂停持续流量..."
    if ! http_post_json "/stop" '{}' > "${LAST_HTTP_RESPONSE_FILE}"; then
        echo "暂停失败。"
        return 1
    fi
    if ! require_server_status_json status_json; then
        return 1
    fi
    paused_value_is_true "${status_json}"
    paused_rc=$?
    if [ "${paused_rc}" -eq 2 ]; then
        echo "错误：/stop 后状态 JSON 解析失败。"
        return 1
    elif [ "${paused_rc}" -ne 0 ]; then
        echo "错误：/stop 后 generator 未进入 paused=true。"
        return 1
    fi

    if [ "${state}" = "${WINDOW_STATE_ACTIVE}" ] && [ -n "$(get_window_start)" ]; then
        echo "等待 ${WINDOW_FLUSH_SECONDS} 秒缓冲，让在途批次落库..."
        sleep "${WINDOW_FLUSH_SECONDS}"
        end_time="$(now_utc)"
        close_window "${end_time}"
        echo "已关闭当前持续流量窗口：$(get_window_start) -> ${end_time}"
    elif [ "${state}" = "${WINDOW_STATE_CLOSED}" ]; then
        echo "当前窗口已经是 CLOSED，保持现有窗口信息不变。"
    else
        write_window_state "${WINDOW_STATE_IDLE}"
        rm -f "${CURRENT_WINDOW_START_FILE}" "${CURRENT_WINDOW_END_FILE}"
        echo "当前没有活动窗口，仅将生成器置为 paused=true。"
    fi

    pretty_print_json_file "${LAST_HTTP_RESPONSE_FILE}"
    record_summary "持续流量已暂停 state=$(get_window_state) end=$(get_window_end)"
    return 0
}

print_pollution_summary() {
    local start_time="$1"
    local end_time="$2"
    echo "窗口内用户名摘要（最多 10 条）："
    clickhouse_query "
SELECT username, count() AS cnt
FROM ${CLICKHOUSE_DATABASE}.logs_structured
WHERE log_type = '${CONTINUOUS_LOG_TYPE}'
  AND timestamp >= '${start_time}'
  AND timestamp < '${end_time}'
GROUP BY username
ORDER BY cnt DESC, username ASC
LIMIT 10
" || true
}

check_window_isolation() {
    local start_time="$1"
    local end_time="$2"
    local total_count_raw=""
    local fixture_count_raw=""
    local total_count=""
    local fixture_count=""

    if ! total_count_raw="$(clickhouse_scalar "
SELECT count()
FROM ${CLICKHOUSE_DATABASE}.logs_structured
WHERE log_type = '${CONTINUOUS_LOG_TYPE}'
  AND timestamp >= '${start_time}'
  AND timestamp < '${end_time}'
" 2>/dev/null)"; then
        echo "错误：total_count 查询失败（HTTP 非 2xx 或 SQL 错误），拒绝继续。"
        return 1
    fi

    if ! fixture_count_raw="$(clickhouse_scalar "
SELECT count()
FROM ${CLICKHOUSE_DATABASE}.logs_structured
WHERE log_type = '${CONTINUOUS_LOG_TYPE}'
  AND username = '${CONTINUOUS_USERNAME}'
  AND position(raw_log, '${CONTINUOUS_MARKER}') > 0
  AND timestamp >= '${start_time}'
  AND timestamp < '${end_time}'
" 2>/dev/null)"; then
        echo "错误：fixture_count 查询失败（HTTP 非 2xx 或 SQL 错误），拒绝继续。"
        return 1
    fi

    if ! require_nonnegative_integer "${total_count_raw}" "total_count"; then
        return 1
    fi
    if ! require_nonnegative_integer "${fixture_count_raw}" "fixture_count"; then
        return 1
    fi

    total_count="${total_count_raw}"
    fixture_count="${fixture_count_raw}"
    echo "窗口隔离检查：total_count=${total_count} fixture_count=${fixture_count}"

    if [ "${fixture_count}" -le 0 ]; then
        echo "错误：目标 fixture_count 必须大于 0。"
        print_pollution_summary "${start_time}" "${end_time}"
        return 1
    fi
    if [ "${total_count}" -ne "${fixture_count}" ]; then
        echo "错误：窗口内存在非 fixture 日志，拒绝继续写库评分。"
        print_pollution_summary "${start_time}" "${end_time}"
        return 1
    fi
    return 0
}

BASELINE_RUNNER_TIMEOUT=300
VALIDATION_CLI_TIMEOUT=300

_run_runner_noninteractive() {
    local flag="$1"
    local flag_desc="$2"
    if ! require_venv_python; then
        return 1
    fi
    (
        cd "${PROJECT_ROOT}" || exit 1
        timeout "${BASELINE_RUNNER_TIMEOUT}" "${PYTHON_BIN}" -m "${BASELINE_RUNNER_MODULE}" \
            --output-dir "${BASELINE_OUTPUT_DIR}" \
            "${flag}"
    )
}

run_baseline_generate_expected() {
    _run_runner_noninteractive "--generate-expected" "generate-expected"
}

run_baseline_load_fixture() {
    _run_runner_noninteractive "--load-fixture" "load-fixture"
}

run_baseline_build_action() {
    _run_runner_noninteractive "--build-baseline" "build-baseline"
}

run_baseline_validate() {
    _run_runner_noninteractive "--validate-baselines" "validate-baselines"
}

run_baseline_full_flow() {
    echo "基础准线一键流程当前不可用。"
    echo "原因：交互 Runner pipe 注入模式已废弃；当前共享表现场未恢复。"
    echo "请使用菜单 2 基础准线流程中的分步入口。"
    return 1
}

run_manual_validation_cli() {
    local mode=""
    local write_flag=""
    local run_id=""
    local start_time=""
    local end_time=""
    local state=""
    local output=""
    local rc=0

    if ! require_venv_python; then
        return 1
    fi
    state="$(get_window_state)"
    start_time="$(get_window_start)"
    end_time="$(get_window_end)"

    if [ "${state}" != "${WINDOW_STATE_CLOSED}" ] || [ -z "${start_time}" ] || [ -z "${end_time}" ]; then
        echo "错误：正式评分只接受 CLOSED 窗口，且必须同时存在 start_time 与 end_time。"
        print_window_state
        return 1
    fi
    if [ "${start_time}" \> "${end_time}" ] || [ "${start_time}" = "${end_time}" ]; then
        echo "错误：窗口时间非法，必须满足 start_time < end_time。"
        return 1
    fi

    if ! read_input mode "请输入 YES 执行正式写库评分，或输入 DRYRUN 仅查看不写库：" "检测到输入结束，已取消评分。"; then
        return 1
    fi
    case "${mode}" in
        YES)
            write_flag="--write"
            run_id="menu_$(date -u '+%Y%m%d%H%M%S')"
            ;;
        DRYRUN)
            write_flag="--dry-run"
            run_id="menu_$(date -u '+%Y%m%d%H%M%S')"
            echo "仅查看，不允许写库。"
            ;;
        *)
            echo "已取消评分。"
            return 1
            ;;
    esac

    print_clickhouse_config
    if ! check_window_isolation "${start_time}" "${end_time}"; then
        echo "窗口被污染，默认拒绝继续。"
        return 1
    fi

    output="$(
        cd "${PROJECT_ROOT}" &&
        timeout "${VALIDATION_CLI_TIMEOUT}" "${PYTHON_BIN}" scripts/run_ueba_validation.py \
            --start-time "${start_time}" \
            --end-time "${end_time}" \
            --log-type "${CONTINUOUS_LOG_TYPE}" \
            --model-version "${CONTINUOUS_MODEL_VERSION}" \
            --validation-run-id "${run_id}" \
            ${write_flag} \
            --host "${CLICKHOUSE_HOST}" \
            --port "${CLICKHOUSE_PORT}" \
            --username "${CLICKHOUSE_USERNAME}" \
            --password "${CLICKHOUSE_PASSWORD}" \
            --database "${CLICKHOUSE_DATABASE}" 2>&1
    )"
    rc=$?
    printf '%s\n' "${output}" > "${LAST_VALIDATION_RESULT_FILE}"
    pretty_print_json_file "${LAST_VALIDATION_RESULT_FILE}"

    if [ "${rc}" -eq 0 ] && [ "${mode}" = "YES" ]; then
        append_validation_history "${run_id}" "${start_time}" "${end_time}"
        echo "正式 Validation CLI 写库评分完成。run_id=${run_id}"
        record_summary "Validation CLI 写库评分成功 run_id=${run_id} window=${start_time}..${end_time}"
    elif [ "${rc}" -eq 0 ]; then
        echo "正式 Validation CLI dry-run 已完成。"
        record_summary "Validation CLI dry-run 成功 run_id=${run_id} window=${start_time}..${end_time}"
    else
        echo "正式 Validation CLI 执行失败。"
        record_summary "Validation CLI 执行失败 run_id=${run_id} window=${start_time}..${end_time}"
    fi
    return "${rc}"
}

validation_result_count_for_window() {
    local start_time="$1"
    local end_time="$2"
    local raw=""
    if ! raw="$(clickhouse_scalar "
SELECT count()
FROM ${CLICKHOUSE_DATABASE}.ueba_validation_results
WHERE username = '${CONTINUOUS_USERNAME}'
  AND startsWith(validation_run_id, 'menu_')
  AND baseline_model_version = '${CONTINUOUS_MODEL_VERSION}'
  AND log_type = '${CONTINUOUS_LOG_TYPE}'
  AND timestamp >= '${start_time}'
  AND timestamp < '${end_time}'
")"; then
        return 1
    fi
    printf '%s\n' "${raw}"
}

continuous_log_count_for_window() {
    local start_time="$1"
    local end_time="$2"
    local raw=""
    if ! raw="$(clickhouse_scalar "
SELECT count()
FROM ${CLICKHOUSE_DATABASE}.logs_structured
WHERE username = '${CONTINUOUS_USERNAME}'
  AND position(raw_log, '${CONTINUOUS_MARKER}') > 0
  AND timestamp >= '${start_time}'
  AND timestamp < '${end_time}'
")"; then
        return 1
    fi
    printf '%s\n' "${raw}"
}

cleanup_current_round_data() {
    local state=""
    local start_time=""
    local end_time=""
    local before_validation="0"
    local before_logs="0"
    local after_validation="0"
    local after_logs="0"

    state="$(get_window_state)"
    start_time="$(get_window_start)"
    end_time="$(get_window_end)"

    if [ "${state}" != "${WINDOW_STATE_CLOSED}" ] || [ -z "${start_time}" ] || [ -z "${end_time}" ]; then
        echo "错误：精确 cleanup 只接受 CLOSED 窗口；窗口状态文件缺失时拒绝执行。"
        print_window_state
        return 1
    fi
    if ! confirm_token "DELETE" "请输入 DELETE 确认精确 cleanup："; then
        return 1
    fi

    before_validation_raw="$(validation_result_count_for_window "${start_time}" "${end_time}")" || {
        echo "错误：cleanup 前 validation 计数查询失败，拒绝执行 DELETE。"
        return 1
    }
    before_logs_raw="$(continuous_log_count_for_window "${start_time}" "${end_time}")" || {
        echo "错误：cleanup 前日志计数查询失败，拒绝执行 DELETE。"
        return 1
    }
    if ! require_nonnegative_integer "${before_validation_raw}" "cleanup_before_validation_count"; then
        return 1
    fi
    if ! require_nonnegative_integer "${before_logs_raw}" "cleanup_before_log_count"; then
        return 1
    fi
    before_validation="${before_validation_raw}"
    before_logs="${before_logs_raw}"
    echo "cleanup 前计数：validation_results=${before_validation} continuous_logs=${before_logs}"

    if ! clickhouse_query "
ALTER TABLE ${CLICKHOUSE_DATABASE}.ueba_validation_results
DELETE WHERE username = '${CONTINUOUS_USERNAME}'
  AND startsWith(validation_run_id, 'menu_')
  AND baseline_model_version = '${CONTINUOUS_MODEL_VERSION}'
  AND log_type = '${CONTINUOUS_LOG_TYPE}'
  AND timestamp >= '${start_time}'
  AND timestamp < '${end_time}'
SETTINGS mutations_sync = 1
" > /dev/null 2>&1; then
        echo "错误：validation results DELETE 执行失败，cleanup 中止。"
        return 1
    fi

    if ! clickhouse_query "
ALTER TABLE ${CLICKHOUSE_DATABASE}.logs_structured
DELETE WHERE username = '${CONTINUOUS_USERNAME}'
  AND position(raw_log, '${CONTINUOUS_MARKER}') > 0
  AND timestamp >= '${start_time}'
  AND timestamp < '${end_time}'
SETTINGS mutations_sync = 1
" > /dev/null 2>&1; then
        echo "错误：logs_structured DELETE 执行失败，cleanup 中止。"
        return 1
    fi

    after_validation_raw="$(validation_result_count_for_window "${start_time}" "${end_time}")" || {
        echo "错误：cleanup 后 validation 计数查询失败，判定 cleanup 失败。"
        return 1
    }
    after_logs_raw="$(continuous_log_count_for_window "${start_time}" "${end_time}")" || {
        echo "错误：cleanup 后日志计数查询失败，判定 cleanup 失败。"
        return 1
    }
    if ! require_nonnegative_integer "${after_validation_raw}" "cleanup_after_validation_count"; then
        return 1
    fi
    if ! require_nonnegative_integer "${after_logs_raw}" "cleanup_after_log_count"; then
        return 1
    fi
    after_validation="${after_validation_raw}"
    after_logs="${after_logs_raw}"
    echo "cleanup 后计数：validation_results=${after_validation} continuous_logs=${after_logs}"

    if [ "${after_validation}" -ne 0 ] || [ "${after_logs}" -ne 0 ]; then
        echo "警告：cleanup 后仍存在残留数据。"
        return 1
    fi

    clear_window_files
    : > "${VALIDATION_HISTORY_FILE}"
    record_summary "精确 cleanup 成功 window=${start_time}..${end_time}"
    echo "本轮持续流量验收数据已精确清理完成。"
    return 0
}

show_file_if_exists() {
    local label="$1"
    local file_path="$2"
    echo "${label}：${file_path}"
    if [ -f "${file_path}" ]; then
        pretty_print_json_file "${file_path}"
    else
        echo "尚未生成。"
    fi
}

show_overall_status() {
    print_divider
    echo "整体状态"
    echo "项目根目录：${PROJECT_ROOT}"
    echo "状态目录：${STATE_ROOT}"
    echo
    print_window_state
    echo
    echo "基础准线产物："
    show_file_if_exists "run_state" "${BASELINE_OUTPUT_DIR}/run_state.json"
    show_file_if_exists "build_result" "${BASELINE_OUTPUT_DIR}/build_result.json"
    show_file_if_exists "validation_report" "${BASELINE_OUTPUT_DIR}/validation_report.json"
    echo
    echo "训练表更新产物："
    show_file_if_exists "manual_training_update_report" "${TRAINING_OUTPUT_DIR}/manual_training_update_report.json"
    echo
    echo "持续流量状态："
    show_server_status
    echo
    echo "持续流量 Validation 产物："
    show_file_if_exists "last_validation_result" "${LAST_VALIDATION_RESULT_FILE}"
    show_file_if_exists "continuous_validation_acceptance_report" "${CONTINUOUS_ACCEPTANCE_REPORT}"
    if [ -f "${VALIDATION_HISTORY_FILE}" ] && [ -s "${VALIDATION_HISTORY_FILE}" ]; then
        echo "本轮 Validation run 历史："
        cat "${VALIDATION_HISTORY_FILE}"
    else
        echo "本轮 Validation run 历史：暂无"
    fi
    print_divider
}

show_continuous_logs_and_status() {
    print_divider
    print_window_state
    show_server_status
    echo
    echo "HTTP Server 日志（最近 40 行）："
    if [ -f "${SERVER_LOG_FILE}" ]; then
        tail -n 40 "${SERVER_LOG_FILE}"
    else
        echo "暂无日志。"
    fi
    echo
    echo "最近一次 Validation CLI 结果："
    if [ -f "${LAST_VALIDATION_RESULT_FILE}" ]; then
        pretty_print_json_file "${LAST_VALIDATION_RESULT_FILE}"
    else
        echo "暂无结果。"
    fi
    echo
    echo "最近一次持续流量一键验收结果："
    if [ -f "${CONTINUOUS_ACCEPTANCE_REPORT}" ]; then
        pretty_print_json_file "${CONTINUOUS_ACCEPTANCE_REPORT}"
    else
        echo "暂无结果。"
    fi
    print_divider
}

environment_check() {
    print_divider
    echo "环境检查"
    echo "项目根目录：${PROJECT_ROOT}"
    echo
    echo "当前 Git 分支："
    (cd "${PROJECT_ROOT}" && git branch --show-current)
    echo
    echo "git status --short --branch --untracked-files=all："
    (cd "${PROJECT_ROOT}" && git status --short --branch --untracked-files=all)
    echo
    echo ".venv/bin/python：${PYTHON_BIN}"
    if [ -x "${PYTHON_BIN}" ]; then
        "${PYTHON_BIN}" --version
    else
        echo "不存在或不可执行"
    fi
    echo
    print_clickhouse_config
    echo
    echo "ClickHouse ping："
    if clickhouse_curl "$(clickhouse_base_url)/ping"; then
        echo
    else
        echo "ping 失败"
    fi
    echo
    echo "clickhouse-server 容器："
    if command -v docker >/dev/null 2>&1; then
        docker ps -a --format '{{.Names}}' | grep -x 'clickhouse-server' || echo "未发现 clickhouse-server 容器"
    else
        echo "未安装 docker 命令"
    fi
    echo
    echo "8765 端口状态：$(port_state_text)"
    echo
    echo "基础表存在性："
    clickhouse_query "
SELECT name
FROM system.tables
WHERE database = '${CLICKHOUSE_DATABASE}'
  AND name IN (
    'logs_structured',
    'user_behavior_baselines',
    'ueba_baseline_training_logs',
    'ueba_validation_results'
  )
ORDER BY name
" || true
    echo
    echo "历史污染数据检查："
    echo "- ueba_stage14_perf_seed 行数："
    clickhouse_query "
SELECT count()
FROM ${CLICKHOUSE_DATABASE}.logs_structured
WHERE position(raw_log, 'ueba_stage14_perf_seed') > 0
" || true
    echo "- ueba_continuous_fixture 行数："
    clickhouse_query "
SELECT count()
FROM ${CLICKHOUSE_DATABASE}.logs_structured
WHERE position(raw_log, '${CONTINUOUS_MARKER}') > 0
" || true
    echo
    echo "当前菜单 Server PID 文件：${SERVER_PID_FILE}"
    if server_is_running; then
        echo "PID 有效：$(read_server_pid)"
    else
        echo "PID 无效或未运行"
    fi
    echo
    print_window_state
    echo
    echo "当前菜单 Server 状态："
    show_server_status
    print_divider
}

baseline_menu() {
    local choice=""
    while true; do
        print_divider
        echo "基础准线流程"
        echo "注意：以下写库操作直接写入共享 ClickHouse 表"
        echo "  - logs_structured（模拟数据）"
        echo "  - user_behavior_baselines（基线结果）"
        echo "  不会自动 cleanup，操作前请确认当前数据库现场。"
        echo
        echo "1. 生成理论准线文件（只读）"
        echo "2. 生成模拟数据并写入 ClickHouse logs_structured（大写 YES 确认）"
        echo "3. 执行正式 baseline 构建并写入 user_behavior_baselines（大写 YES 确认）"
        echo "4. 对比理论准线与实际 baseline（只读）"
        echo "5. 返回上一级"
        echo "0. 返回上一级"
        if ! read_input choice "请选择操作：" "检测到输入结束，返回主菜单。"; then
            return 0
        fi
        case "${choice}" in
            1) run_baseline_generate_expected ;;
            2)
                echo "此操作将生成 fixture 模拟数据并写入共享 ClickHouse 表 logs_structured。"
                echo "数据不会被自动 cleanup。"
                if ! confirm_token "YES" "请输入 YES 确认写入："; then
                    continue
                fi
                run_baseline_load_fixture
                ;;
            3)
                echo "此操作将执行正式 baseline 构建并写入共享 ClickHouse 表 user_behavior_baselines。"
                echo "数据不会被自动 cleanup。"
                if ! confirm_token "YES" "请输入 YES 确认构建："; then
                    continue
                fi
                run_baseline_build_action
                ;;
            4) run_baseline_validate ;;
            5|0) return 0 ;;
            *) echo "无效输入，请重新选择。" ;;
        esac
    done
}

continuous_mode_menu() {
    local choice=""
    while true; do
        print_divider
        echo "持续流量模式切换"
        echo "1. normal"
        echo "2. new_ip"
        echo "3. new_country"
        echo "4. new_city"
        echo "5. failed_login"
        echo "6. off_hours"
        echo "7. combo_anomaly"
        echo "8. mixed"
        echo "9. 返回上一级"
        echo "0. 返回上一级"
        if ! read_input choice "请选择模式：" "检测到输入结束，返回持续流量菜单。"; then
            return 0
        fi
        case "${choice}" in
            1) set_continuous_mode "normal" ;;
            2) set_continuous_mode "new_ip" ;;
            3) set_continuous_mode "new_country" ;;
            4) set_continuous_mode "new_city" ;;
            5) set_continuous_mode "failed_login" ;;
            6) set_continuous_mode "off_hours" ;;
            7) set_continuous_mode "combo_anomaly" ;;
            8) set_continuous_mode "mixed" ;;
            9|0) return 0 ;;
            *) echo "无效输入，请重新选择。" ;;
        esac
    done
}

continuous_menu() {
    local choice=""
    local custom_rate=""
    while true; do
        print_divider
        echo "持续流量与 Validation"
        echo "1. 启动持续流量 HTTP Server"
        echo "2. 停止持续流量 HTTP Server"
        echo "3. 暂停持续流量"
        echo "4. 恢复持续流量"
        echo "5. 调速到 20/秒"
        echo "6. 调速到 50/秒"
        echo "7. 自定义调速"
        echo "8. 切换持续流量模式"
        echo "9. 使用正式 Validation CLI 评分当前持续流量"
        echo "10. 查看持续流量状态、日志与结果"
        echo "11. 精确清理本轮持续流量验收数据"
        echo "12. 暂未开放：需单独完成 Python 一键联动验收 cleanup 加固"
        echo "13. 返回上一级"
        echo "0. 返回上一级"
        if ! read_input choice "请选择操作：" "检测到输入结束，返回主菜单。"; then
            return 0
        fi
        case "${choice}" in
            1) start_continuous_server ;;
            2) stop_continuous_server ;;
            3) pause_continuous_server ;;
            4) resume_continuous_server ;;
            5) set_continuous_rate "20" ;;
            6) set_continuous_rate "50" ;;
            7)
                if ! read_input custom_rate "请输入 0..1000 的日志速率：" "检测到输入结束，已取消调速。"; then
                    continue
                fi
                set_continuous_rate "${custom_rate}"
                ;;
            8) continuous_mode_menu ;;
            9) run_manual_validation_cli ;;
            10) show_continuous_logs_and_status ;;
            11) cleanup_current_round_data ;;
            12)
                echo "一键持续流量联动验收暂未开放：Python cleanup 精确用户名语义待独立加固。"
                ;;
            13|0) return 0 ;;
            *) echo "无效输入，请重新选择。" ;;
        esac
    done
}

main_menu() {
    local choice=""
    while true; do
        print_divider
        echo "UEBA 全流程演示工具"
        echo
        echo "1. 环境检查"
        echo "2. 基础准线流程"
        echo "3. 训练表更新流程（暂未开放：下游 Runner timeout 待独立加固）"
        echo "4. 持续流量与 Validation"
        echo "5. 查看整体状态"
        echo "6. 一键执行基础准线完整流程（已禁用：交互 Runner pipe 注入已废弃，共享表现场未恢复）"
        echo "7. 一键持续流量联动验收（暂未开放：Python cleanup 精确用户名语义待独立加固）"
        echo "8. 退出"
        echo
        if ! read_input choice "请选择操作：" "检测到输入结束，主菜单安全退出。"; then
            echo "未自动停止 Server，未自动清理数据库。"
            return 0
        fi
        case "${choice}" in
            1) environment_check ;;
            2) baseline_menu ;;
            3)
                echo "训练表更新流程暂未开放：下游 Runner timeout 待独立加固。"
                ;;
            4) continuous_menu ;;
            5) show_overall_status ;;
            6)
                echo "基础准线一键流程已禁用：交互 Runner pipe 注入已废弃，共享表现场未恢复。"
                echo "请使用菜单 2 基础准线流程中的分步入口。"
                ;;
            7)
                echo "一键持续流量联动验收暂未开放：Python cleanup 精确用户名语义待独立加固。"
                ;;
            8|0)
                echo "安全退出。"
                return 0
                ;;
            *) echo "无效输入，请重新显示菜单。" ;;
        esac
    done
}

main() {
    ensure_state_dirs
    if ! require_venv_python; then
        return 1
    fi
    touch "${VALIDATION_HISTORY_FILE}" "${SUMMARY_LOG_FILE}"
    main_menu
}

if [[ "${BASH_SOURCE[0]}" == "$0" ]]; then
    main "$@"
fi
