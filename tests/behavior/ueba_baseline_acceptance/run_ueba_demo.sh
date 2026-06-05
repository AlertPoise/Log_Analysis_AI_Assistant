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
CLEANUP_DONE=0

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

ensure_clickhouse_ready_for_write_flow() {
    local ping_url=""
    ping_url="$(clickhouse_base_url)/ping"
    if clickhouse_curl "${ping_url}" >/dev/null 2>&1; then
        return 0
    fi
    echo "错误：ClickHouse 不可达（${ping_url} 失败），请先执行菜单 1 环境检查并确认 ClickHouse 已启动。" >&2
    return 1
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
CONTINUOUS_ACCEPTANCE_TIMEOUT=600
TRAINING_UPDATE_TIMEOUT=600

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

run_training_update() {
    echo "即将执行月度训练表更新闭环验收。"
    echo "该流程包含：5月初始化训练表、5月 baseline 构建、6月训练表替换、"
    echo "baseline 不变校验、6月 baseline 重建、差异验证。"
    echo "数据将写入共享 ClickHouse 表，不会自动 cleanup。"
    if ! ensure_clickhouse_ready_for_write_flow; then
        return 1
    fi
    echo "开始执行训练表更新闭环（timeout=${TRAINING_UPDATE_TIMEOUT}s）..."
    (
        cd "${PROJECT_ROOT}" || exit 1
        timeout "${TRAINING_UPDATE_TIMEOUT}" "${PYTHON_BIN}" -m "${TRAINING_RUNNER_MODULE}" \
            --run-all --output-dir "${TRAINING_OUTPUT_DIR}" 2>&1
    )
    local rc=$?
    if [ "${rc}" -eq 0 ]; then
        echo "训练表更新闭环完成。"
        record_summary "训练表更新闭环成功"
    else
        echo "训练表更新闭环失败（退出码=${rc}）。"
        record_summary "训练表更新闭环失败 rc=${rc}"
    fi
    return "${rc}"
}

_fixture_usernames_sql() {
    "${PYTHON_BIN}" -c "
from tests.behavior.ueba_baseline_acceptance.config import AcceptanceConfig
c = AcceptanceConfig()
users = c.fixture_usernames + ['fixture_user_validation_nobase']
print(','.join(repr(u) for u in users))
"
}

_cleanup_model_versions_sql() {
    printf "'ueba_baseline_fixture_v2_monthly','ueba_monthly_acceptance_may_init','ueba_monthly_acceptance_june_updated'"
}

_cleanup_config_time_window() {
    "${PYTHON_BIN}" -c "
from tests.behavior.ueba_baseline_acceptance.config import AcceptanceConfig
c = AcceptanceConfig()
print(c.start_time)
print(c.end_time)
"
}

_read_acceptance_report_window() {
    local report_file="$1"
    if [ ! -f "${report_file}" ]; then
        return 1
    fi
    "${PYTHON_BIN}" -c "
import json, sys
with open('${report_file}') as f:
    r = json.load(f)
windows = []
for key in ('normal_window', 'combo_window', 'idempotency_window'):
    w = r.get(key, {})
    if w.get('start') and w.get('end'):
        windows.append((w['start'], w['end']))
if not windows:
    sys.exit(1)
starts = [w[0] for w in windows]
ends = [w[1] for w in windows]
print(min(starts))
print(max(ends))
"
}

_continuous_cleanup_fallback_window() {
    "${PYTHON_BIN}" -c "
from datetime import datetime, timedelta, timezone
now = datetime.now(timezone.utc).replace(tzinfo=None, microsecond=0)
print((now - timedelta(hours=24)).strftime('%Y-%m-%d %H:%M:%S'))
print((now + timedelta(hours=1)).strftime('%Y-%m-%d %H:%M:%S'))
"
}

_cleanup_baseline_fixture_logs_for_continuous() {
    local users_sql=""
    local config_start=""
    local config_end=""
    local config_window=""
    if ! users_sql="$(_fixture_usernames_sql)"; then
        echo "错误：无法获取 fixture 用户名列表。"
        return 1
    fi
    if ! config_window="$(_cleanup_config_time_window 2>/dev/null)"; then
        echo "错误：无法获取 baseline fixture 时间窗口。"
        return 1
    fi
    config_start="$(printf '%s\n' "${config_window}" | head -n 1)"
    config_end="$(printf '%s\n' "${config_window}" | tail -n 1)"
    if [ -z "${config_start}" ] || [ -z "${config_end}" ]; then
        echo "错误：baseline fixture 时间窗口为空。"
        return 1
    fi
    if ! clickhouse_query "ALTER TABLE ${CLICKHOUSE_DATABASE}.logs_structured DELETE WHERE username IN (${users_sql}) AND log_type='vpn' AND timestamp >= '${config_start}' AND timestamp < '${config_end}' SETTINGS mutations_sync=1" > /dev/null 2>&1; then
        echo "错误：baseline fixture logs_structured 预清理失败。"
        return 1
    fi
    echo "baseline fixture 输入日志已清理，保留 baseline 结果供持续评分使用。"
    return 0
}

_one_click_cleanup() {
    echo "正在清理本轮演示数据（4 张表）..."
    local users_sql=""
    if ! users_sql="$(_fixture_usernames_sql)"; then
        echo "错误：无法获取 fixture 用户名列表。"
        return 1
    fi

    local model_versions=""
    model_versions="$(_cleanup_model_versions_sql)"

    # 尝试从连续验收报告获取本轮精确时间窗口
    local continuous_start=""
    local continuous_end=""
    local report_window=""
    if report_window="$(_read_acceptance_report_window "${CONTINUOUS_ACCEPTANCE_REPORT}" 2>/dev/null)"; then
        continuous_start="$(printf '%s\n' "${report_window}" | head -n 1)"
        continuous_end="$(printf '%s\n' "${report_window}" | tail -n 1)"
    else
        local fallback_window=""
        if fallback_window="$(_continuous_cleanup_fallback_window 2>/dev/null)"; then
            continuous_start="$(printf '%s\n' "${fallback_window}" | head -n 1)"
            continuous_end="$(printf '%s\n' "${fallback_window}" | tail -n 1)"
            echo "  continuous cleanup fallback window: ${continuous_start} .. ${continuous_end}"
        fi
    fi

    # 获取 baseline fixture 配置时间窗口
    local config_start=""
    local config_end=""
    local config_window=""
    if config_window="$(_cleanup_config_time_window 2>/dev/null)"; then
        config_start="$(printf '%s\n' "${config_window}" | head -n 1)"
        config_end="$(printf '%s\n' "${config_window}" | tail -n 1)"
    fi

    # logs_structured: 分离 baseline fixture 和 continuous fixture 的清理
    if [ -n "${config_start}" ] && [ -n "${config_end}" ]; then
        if ! clickhouse_query "ALTER TABLE ${CLICKHOUSE_DATABASE}.logs_structured DELETE WHERE username IN (${users_sql}) AND log_type='vpn' AND timestamp >= '${config_start}' AND timestamp < '${config_end}' SETTINGS mutations_sync=1" > /dev/null 2>&1; then
            echo "错误：logs_structured baseline fixture cleanup 失败。"
            return 1
        fi
        echo "  logs_structured (baseline fixture window): 已清理"
    fi
    if [ -n "${continuous_start}" ] && [ -n "${continuous_end}" ]; then
        if ! clickhouse_query "ALTER TABLE ${CLICKHOUSE_DATABASE}.logs_structured DELETE WHERE username IN (${users_sql}) AND log_type='vpn' AND position(raw_log, 'ueba_continuous_fixture') > 0 AND timestamp >= '${continuous_start}' AND timestamp < '${continuous_end}' SETTINGS mutations_sync=1" > /dev/null 2>&1; then
            echo "错误：logs_structured continuous fixture cleanup 失败。"
            return 1
        fi
        echo "  logs_structured (continuous fixture window): 已清理"
    else
        echo "错误：无法获取 continuous cleanup 时间窗口，拒绝清理 continuous 数据。"
        return 1
    fi

    if ! clickhouse_query "ALTER TABLE ${CLICKHOUSE_DATABASE}.user_behavior_baselines DELETE WHERE username IN (${users_sql}) AND model_version IN (${model_versions}) SETTINGS mutations_sync=1" > /dev/null 2>&1; then
        echo "错误：user_behavior_baselines cleanup 失败。"
        return 1
    fi
    echo "  user_behavior_baselines: 已清理"

    if ! clickhouse_query "ALTER TABLE ${CLICKHOUSE_DATABASE}.ueba_baseline_training_logs DELETE WHERE dataset_id='ueba_training_monthly_acceptance' SETTINGS mutations_sync=1" > /dev/null 2>&1; then
        echo "错误：ueba_baseline_training_logs cleanup 失败。"
        return 1
    fi
    echo "  ueba_baseline_training_logs: 已清理"

    # ueba_validation_results: username + model_version + log_type + run_id 前缀 + 本轮/兜底时间窗口
    if [ -n "${continuous_start}" ] && [ -n "${continuous_end}" ]; then
        if ! clickhouse_query "ALTER TABLE ${CLICKHOUSE_DATABASE}.ueba_validation_results DELETE WHERE username IN (${users_sql}) AND baseline_model_version IN (${model_versions}) AND log_type='vpn' AND (startsWith(validation_run_id, 'continuous_normal_') OR startsWith(validation_run_id, 'continuous_combo_') OR startsWith(validation_run_id, 'continuous_idempotent_')) AND timestamp >= '${continuous_start}' AND timestamp < '${continuous_end}' SETTINGS mutations_sync=1" > /dev/null 2>&1; then
            echo "错误：ueba_validation_results cleanup 失败。"
            return 1
        fi
    else
        echo "错误：无法获取 validation cleanup 时间窗口，拒绝清理 validation_results。"
        return 1
    fi
    echo "  ueba_validation_results: 已清理"

    echo "清理完成。"
    return 0
}

_cleanup_once() {
    if [ "${CLEANUP_DONE}" -eq 1 ]; then
        echo "[cleanup] cleanup 已执行，跳过重复调用。"
        return 0
    fi
    CLEANUP_DONE=1
    echo "[cleanup] 执行一次性 cleanup ..."
    _stop_demo_server 2>/dev/null || true
    # 终止所有子进程，防止 cleanup 与数据写入产生竞态
    pkill -P $$ 2>/dev/null || true
    sleep 1
    _one_click_cleanup
}

run_full_process() {
    local step_rc=0
    local cleanup_rc=0
    CLEANUP_DONE=0

    trap 'echo "收到 SIGINT，正在清理..." 1>&2; _cleanup_once; trap - INT TERM; exit 130' INT
    trap 'echo "收到 SIGTERM，正在清理..." 1>&2; _cleanup_once; trap - INT TERM; exit 143' TERM

    echo "==== UEBA 一键完整演示 ===="
    echo "本流程自动完成环境检查、训练更新、基线构建、持续评分与清理。"
    echo ""

    echo "[1/5] 检查运行环境"
    if ! ensure_clickhouse_ready_for_write_flow; then
        step_rc=1
    fi
    if [ "${step_rc}" -eq 0 ]; then
        echo "环境检查通过。"
    else
        echo "环境检查失败，流程中止。"
    fi
    echo ""

    if [ "${step_rc}" -eq 0 ]; then
        echo "[2/5] 更新训练数据并构建 baseline"
        run_training_update || step_rc=1
        echo ""
        if [ "${step_rc}" -eq 0 ]; then
            run_baseline_full_flow || step_rc=1
        fi
        if [ "${step_rc}" -eq 0 ]; then
            _cleanup_baseline_fixture_logs_for_continuous || step_rc=1
        fi
        echo ""
    fi

    if [ "${step_rc}" -eq 0 ]; then
        echo "[3/5] 生成持续流量并执行 Validation"
        _run_continuous_acceptance || step_rc=1
        echo ""
    fi

    if [ "${step_rc}" -eq 0 ]; then
        echo "[4/5] 校验运行结果"
        echo "训练更新、基线构建、持续评分已依次完成。"
    else
        echo "[4/5] 校验运行结果"
        echo "前置步骤存在失败，跳过结果校验。"
    fi
    echo ""

    echo "[5/5] 清理本轮演示数据"
    if ! _cleanup_once; then
        cleanup_rc=1
        record_summary "清理失败"
    fi
    _stop_demo_server 2>/dev/null || true

    trap - INT TERM
    if [ "${step_rc}" -ne 0 ]; then
        record_summary "UEBA 一键完整演示：FAIL（步骤失败）"
        return 1
    fi
    if [ "${cleanup_rc}" -ne 0 ]; then
        record_summary "UEBA 一键完整演示：FAIL（清理失败）"
        return 1
    fi
    record_summary "UEBA 一键完整演示：PASS"
    return 0
}

_stop_demo_server() {
    local pid_file="${STATE_ROOT}/continuous_login_http_server.pid"
    if [ -f "${pid_file}" ]; then
        local pid
        pid="$(cat "${pid_file}" 2>/dev/null)"
        if [ -n "${pid}" ] && is_valid_positive_pid "${pid}"; then
            kill "${pid}" 2>/dev/null || true
            rm -f "${pid_file}"
        fi
    fi
}

run_baseline_full_flow() {
    echo "将按顺序执行基础准线完整流程："
    echo "  1. 生成理论准线文件"
    echo "  2. 生成 fixture 模拟数据并写入 ClickHouse logs_structured"
    echo "  3. 执行正式 baseline 构建（写入 user_behavior_baselines）"
    echo "  4. 对比理论准线与实际 baseline"
    echo ""
    echo "警告：数据将写入共享 ClickHouse 表，不会自动 cleanup。"

    local step_rc=0
    echo ""
    echo "==== 步骤 1/4：生成理论准线文件 ===="
    run_baseline_generate_expected || { echo "步骤 1 失败，流程中止。"; return 1; }

    echo ""
    echo "==== 步骤 2/4：生成模拟数据并写入 ClickHouse ===="
    run_baseline_load_fixture || { echo "步骤 2 失败，流程中止。"; return 1; }

    echo ""
    echo "==== 步骤 3/4：执行正式 baseline 构建 ===="
    run_baseline_build_action || { echo "步骤 3 失败，流程中止。"; return 1; }

    echo ""
    echo "==== 步骤 4/4：对比理论准线与实际 baseline ===="
    run_baseline_validate || { echo "步骤 4 失败。"; return 1; }

    echo ""
    echo "基础准线一键流程全部完成。"
    record_summary "基础准线一键流程成功完成"
    return 0
}

_run_continuous_acceptance() {
    if ! require_venv_python; then
        return 1
    fi

    echo "启动持续流量联动验收（自动确认写库 + 成功后自动 cleanup）..."
    echo "timeout=${CONTINUOUS_ACCEPTANCE_TIMEOUT}s"
    (
        cd "${PROJECT_ROOT}" || exit 1
        timeout "${CONTINUOUS_ACCEPTANCE_TIMEOUT}" "${PYTHON_BIN}" -m "${CONTINUOUS_ACCEPTANCE_MODULE}" \
            --confirm-write \
            --confirm-cleanup --cleanup-after --keep-data-on-failure \
            --report-path "${CONTINUOUS_ACCEPTANCE_REPORT}" 2>&1
    )
    local rc=$?
    if [ "${rc}" -eq 0 ]; then
        echo "持续流量联动验收完成。"
        if [ -f "${CONTINUOUS_ACCEPTANCE_REPORT}" ]; then
            pretty_print_json_file "${CONTINUOUS_ACCEPTANCE_REPORT}"
        fi
        record_summary "持续流量联动验收成功"
    else
        echo "持续流量联动验收失败（退出码=${rc}）。"
        record_summary "持续流量联动验收失败 rc=${rc}"
    fi
    return "${rc}"
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

main_menu() {
    local choice=""
    while true; do
        print_divider
        echo "UEBA 全流程演示工具"
        echo
        echo "1. 环境检查"
        echo "2. 一键执行 UEBA 完整演示"
        echo "3. 查看最近一次运行结果"
        echo "4. 退出"
        echo
        if ! read_input choice "请选择操作：" "检测到输入结束，主菜单安全退出。"; then
            echo "未自动停止 Server，未自动清理数据库。"
            return 0
        fi
        case "${choice}" in
            1) environment_check ;;
            2) run_full_process ;;
            3) show_results ;;
            4|0)
                echo "安全退出。"
                return 0
                ;;
            *) echo "无效输入，请重新选择。" ;;
        esac
    done
}

show_results() {
    print_divider
    echo "最近一次运行结果"
    echo
    show_overall_status
    echo
    print_divider
    if [ -f "${SUMMARY_LOG_FILE}" ]; then
        echo "执行摘要（最近 20 行）："
        tail -n 20 "${SUMMARY_LOG_FILE}" 2>/dev/null || echo "（摘要日志为空）"
    else
        echo "尚未产生执行摘要。请先执行菜单 2。"
    fi
    echo
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
