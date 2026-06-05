#!/usr/bin/env bash
set -Eeuo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
DEMO_SCRIPT="${PROJECT_ROOT}/tests/behavior/ueba_baseline_acceptance/run_ueba_demo.sh"
STATE_ROOT="${UEBA_DEMO_STATE_ROOT:-${PROJECT_ROOT}/.tox/manual/ueba_demo_menu}"
VENV_DIR="${PROJECT_ROOT}/.venv"
PYTHON_BIN="${VENV_DIR}/bin/python"
REQUIREMENTS_FILE="${PROJECT_ROOT}/requirements.txt"

CONTINUOUS_HOST="${UEBA_CONTINUOUS_HOST:-127.0.0.1}"
CONTINUOUS_PORT="${UEBA_CONTINUOUS_PORT:-8765}"

CLICKHOUSE_HOST="${CLICKHOUSE_HOST:-localhost}"
CLICKHOUSE_PORT="${CLICKHOUSE_PORT:-8123}"
CLICKHOUSE_USERNAME="${CLICKHOUSE_USERNAME:-${CLICKHOUSE_USER:-default}}"
CLICKHOUSE_PASSWORD="${CLICKHOUSE_PASSWORD:-}"
CLICKHOUSE_DATABASE="${CLICKHOUSE_DATABASE:-log_analysis}"
CLICKHOUSE_CONTAINER_NAME="${CLICKHOUSE_CONTAINER_NAME:-clickhouse-server}"
CLICKHOUSE_DOCKER_IMAGE="${CLICKHOUSE_DOCKER_IMAGE:-clickhouse/clickhouse-server:latest}"

export CLICKHOUSE_HOST CLICKHOUSE_PORT CLICKHOUSE_USERNAME CLICKHOUSE_PASSWORD CLICKHOUSE_DATABASE
export CLICKHOUSE_USER="${CLICKHOUSE_USERNAME}"
export PYTHONPATH="${PROJECT_ROOT}${PYTHONPATH:+:${PYTHONPATH}}"

log() {
    printf '[ueba-reset] %s\n' "$*"
}

warn() {
    printf '[ueba-reset][warn] %s\n' "$*" >&2
}

die() {
    printf '[ueba-reset][error] %s\n' "$*" >&2
    exit 1
}

is_positive_pid() {
    [[ "${1:-}" =~ ^[1-9][0-9]*$ ]]
}

pid_is_alive() {
    local pid="$1"
    is_positive_pid "${pid}" && kill -0 "${pid}" 2>/dev/null
}

require_command() {
    local name="$1"
    command -v "${name}" >/dev/null 2>&1 || die "Required command not found: ${name}"
}

state_root_is_safe() {
    [ -n "${STATE_ROOT}" ] || return 1
    [ "${STATE_ROOT}" != "/" ] || return 1
    case "${STATE_ROOT}" in
        "${PROJECT_ROOT}"/.tox/*|/tmp/*) return 0 ;;
        *) return 1 ;;
    esac
}

collect_port_pids() {
    local port="$1"

    if command -v lsof >/dev/null 2>&1; then
        lsof -nP -tiTCP:"${port}" -sTCP:LISTEN 2>/dev/null || true
    fi
    if command -v fuser >/dev/null 2>&1; then
        fuser -n tcp "${port}" 2>/dev/null || true
    fi
    if command -v ss >/dev/null 2>&1; then
        ss -H -ltnp "sport = :${port}" 2>/dev/null \
            | sed -n 's/.*pid=\([0-9][0-9]*\).*/\1/p' || true
    fi
}

collect_matching_demo_pids() {
    if command -v pgrep >/dev/null 2>&1; then
        pgrep -f 'tests.behavior.ueba_baseline_acceptance.continuous_login_http_server' 2>/dev/null || true
        pgrep -f 'continuous_login_http_server.py' 2>/dev/null || true
    fi
}

unique_pids() {
    awk '
        /^[0-9]+$/ && $1 != "'"$$"'" && !seen[$1]++ { print $1 }
    '
}

terminate_pids() {
    local label="$1"
    shift
    local pids=("$@")
    local pid=""
    local sent=0

    for pid in "${pids[@]}"; do
        if ! is_positive_pid "${pid}" || [ "${pid}" = "$$" ]; then
            continue
        fi
        if pid_is_alive "${pid}"; then
            log "Stopping ${label} PID ${pid}"
            kill -TERM "${pid}" 2>/dev/null || true
            sent=1
        fi
    done

    if [ "${sent}" -eq 1 ]; then
        sleep 1
    fi

    for pid in "${pids[@]}"; do
        if ! is_positive_pid "${pid}" || [ "${pid}" = "$$" ]; then
            continue
        fi
        if pid_is_alive "${pid}"; then
            warn "${label} PID ${pid} still alive; sending SIGKILL"
            kill -KILL "${pid}" 2>/dev/null || true
        fi
    done
}

clear_demo_port_residue() {
    local pid_file="${STATE_ROOT}/continuous_login_http_server.pid"
    local pid=""
    local pids=()

    log "Clearing demo HTTP port residue on ${CONTINUOUS_HOST}:${CONTINUOUS_PORT}"
    if [ -f "${pid_file}" ]; then
        pid="$(sed -n '1p' "${pid_file}" 2>/dev/null || true)"
        if is_positive_pid "${pid}" && pid_is_alive "${pid}"; then
            terminate_pids "pid-file server" "${pid}"
        fi
        rm -f "${pid_file}"
    fi

    mapfile -t pids < <(
        {
            collect_matching_demo_pids
            collect_port_pids "${CONTINUOUS_PORT}"
        } | tr ' ' '\n' | unique_pids
    )
    if [ "${#pids[@]}" -gt 0 ]; then
        terminate_pids "demo port ${CONTINUOUS_PORT}" "${pids[@]}"
    fi

    if command -v curl >/dev/null 2>&1; then
        if curl -fsS --max-time 1 "http://${CONTINUOUS_HOST}:${CONTINUOUS_PORT}/status" >/dev/null 2>&1; then
            die "Port ${CONTINUOUS_PORT} is still serving after cleanup"
        fi
    fi
}

reset_demo_state() {
    state_root_is_safe || die "Unsafe UEBA_DEMO_STATE_ROOT: ${STATE_ROOT}"
    log "Resetting demo state directory: ${STATE_ROOT}"
    rm -rf "${STATE_ROOT}"
    mkdir -p "${STATE_ROOT}"
}

ensure_shell_dependencies() {
    require_command bash
    require_command curl
    require_command sed
    require_command awk
    require_command timeout
}

ensure_python_env() {
    require_command python3

    if [ ! -x "${PYTHON_BIN}" ]; then
        log "Creating virtualenv: ${VENV_DIR}"
        python3 -m venv "${VENV_DIR}" || die "Failed to create virtualenv. Install python3-venv and retry."
    fi

    if ! "${PYTHON_BIN}" -m pip --version >/dev/null 2>&1; then
        log "Bootstrapping pip in virtualenv"
        "${PYTHON_BIN}" -m ensurepip --upgrade >/dev/null || die "pip is unavailable in ${VENV_DIR}"
    fi

    if "${PYTHON_BIN}" - <<'PYDEP' >/dev/null 2>&1
import clickhouse_connect
PYDEP
    then
        log "Python dependency check passed"
        return 0
    fi

    log "Installing Python dependencies from requirements.txt"
    if [ -f "${REQUIREMENTS_FILE}" ]; then
        if "${PYTHON_BIN}" -m pip install ${UEBA_PIP_INSTALL_ARGS:-} -r "${REQUIREMENTS_FILE}"; then
            return 0
        fi
        warn "Full requirements install failed; falling back to UEBA-required ClickHouse client"
    fi
    "${PYTHON_BIN}" -m pip install ${UEBA_PIP_INSTALL_ARGS:-} 'clickhouse-connect>=0.7.0' \
        || die "Failed to install clickhouse-connect"
}

ensure_behavior_env() {
    local env_file="${PROJECT_ROOT}/config/behavior.env"
    local example_file="${PROJECT_ROOT}/config/behavior.env.example"

    if [ ! -f "${env_file}" ] && [ -f "${example_file}" ]; then
        log "Creating config/behavior.env from example"
        cp "${example_file}" "${env_file}"
    fi
}

clickhouse_base_url() {
    printf 'http://%s:%s' "${CLICKHOUSE_HOST}" "${CLICKHOUSE_PORT}"
}

clickhouse_curl() {
    if [ -n "${CLICKHOUSE_PASSWORD}" ]; then
        curl -fsS --max-time 5 --user "${CLICKHOUSE_USERNAME}:${CLICKHOUSE_PASSWORD}" "$@"
        return
    fi
    if [ "${CLICKHOUSE_USERNAME}" != "default" ]; then
        curl -fsS --max-time 5 --user "${CLICKHOUSE_USERNAME}:" "$@"
        return
    fi
    curl -fsS --max-time 5 "$@"
}

clickhouse_ping() {
    clickhouse_curl "$(clickhouse_base_url)/ping" >/dev/null 2>&1
}

is_local_clickhouse_host() {
    case "${CLICKHOUSE_HOST}" in
        localhost|127.0.0.1|::1) return 0 ;;
        *) return 1 ;;
    esac
}

resolve_docker_command() {
    DOCKER_CMD=()
    if command -v docker >/dev/null 2>&1 && docker info >/dev/null 2>&1; then
        DOCKER_CMD=(docker)
        return 0
    fi
    if command -v sudo >/dev/null 2>&1 && sudo -n docker info >/dev/null 2>&1; then
        DOCKER_CMD=(sudo -n docker)
        return 0
    fi
    return 1
}

docker_container_exists() {
    "${DOCKER_CMD[@]}" ps -a --format '{{.Names}}' \
        | grep -Fx "${CLICKHOUSE_CONTAINER_NAME}" >/dev/null 2>&1
}

docker_container_running() {
    [ "$("${DOCKER_CMD[@]}" inspect -f '{{.State.Running}}' "${CLICKHOUSE_CONTAINER_NAME}" 2>/dev/null || true)" = "true" ]
}

start_clickhouse_docker() {
    resolve_docker_command || die "ClickHouse is not reachable and Docker is not available"

    if docker_container_exists; then
        if docker_container_running; then
            log "ClickHouse container ${CLICKHOUSE_CONTAINER_NAME} is already running"
        else
            log "Starting existing ClickHouse container: ${CLICKHOUSE_CONTAINER_NAME}"
            "${DOCKER_CMD[@]}" start "${CLICKHOUSE_CONTAINER_NAME}" >/dev/null
        fi
        return 0
    fi

    log "Creating ClickHouse container ${CLICKHOUSE_CONTAINER_NAME} from ${CLICKHOUSE_DOCKER_IMAGE}"
    local docker_args=(
        run -d
        --name "${CLICKHOUSE_CONTAINER_NAME}"
        --ulimit nofile=262144:262144
        -p "${CLICKHOUSE_PORT}:8123"
        -e "CLICKHOUSE_DB=${CLICKHOUSE_DATABASE}"
    )
    if [ "${CLICKHOUSE_USERNAME}" != "default" ] || [ -n "${CLICKHOUSE_PASSWORD}" ]; then
        docker_args+=(
            -e "CLICKHOUSE_USER=${CLICKHOUSE_USERNAME}"
            -e "CLICKHOUSE_PASSWORD=${CLICKHOUSE_PASSWORD}"
            -e "CLICKHOUSE_DEFAULT_ACCESS_MANAGEMENT=1"
        )
    fi
    docker_args+=("${CLICKHOUSE_DOCKER_IMAGE}")
    "${DOCKER_CMD[@]}" "${docker_args[@]}" >/dev/null
}

wait_for_clickhouse() {
    local attempt=0
    local max_attempts="${UEBA_CLICKHOUSE_WAIT_SECONDS:-90}"

    while [ "${attempt}" -lt "${max_attempts}" ]; do
        if clickhouse_ping; then
            return 0
        fi
        sleep 1
        attempt=$((attempt + 1))
    done
    return 1
}

ensure_clickhouse_running() {
    if clickhouse_ping; then
        log "ClickHouse is reachable at $(clickhouse_base_url)"
        return 0
    fi

    if ! is_local_clickhouse_host; then
        die "ClickHouse is not reachable at $(clickhouse_base_url); remote hosts are not auto-started"
    fi

    start_clickhouse_docker
    wait_for_clickhouse || die "ClickHouse did not become ready at $(clickhouse_base_url)"
    log "ClickHouse is ready at $(clickhouse_base_url)"
}

ensure_clickhouse_schema() {
    log "Creating ClickHouse database and UEBA tables"
    (
        cd "${PROJECT_ROOT}"
        "${PYTHON_BIN}" - <<'PYSCHEMA'
from __future__ import annotations

import os
import re

import clickhouse_connect

from src.behavior.baseline_store import BaselineStore
from src.behavior.training_log_store import TrainingLogStore
from src.behavior.validation_repository import UebaValidationRepository


def ident(value: str, label: str) -> str:
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", value):
        raise ValueError(f"invalid {label}: {value!r}")
    return value


host = os.environ["CLICKHOUSE_HOST"]
port = int(os.environ["CLICKHOUSE_PORT"])
username = os.environ.get("CLICKHOUSE_USERNAME") or os.environ.get("CLICKHOUSE_USER") or "default"
password = os.environ.get("CLICKHOUSE_PASSWORD", "")
database = ident(os.environ.get("CLICKHOUSE_DATABASE", "log_analysis"), "database")

client = clickhouse_connect.get_client(
    host=host,
    port=port,
    username=username,
    password=password,
    database="default",
)
client.command("SELECT 1")
client.command(f"CREATE DATABASE IF NOT EXISTS {database}")

logs_table = f"{database}.logs_structured"
client.command(
    f"""
    CREATE TABLE IF NOT EXISTS {logs_table}
    (
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
    )
    ENGINE = MergeTree()
    PARTITION BY toYYYYMMDD(timestamp)
    ORDER BY (log_type, timestamp, username)
    SETTINGS index_granularity = 8192
    """
)

logs_columns = [
    ("id", "UInt64"),
    ("timestamp", "DateTime"),
    ("log_type", "String"),
    ("source", "String"),
    ("username", "String"),
    ("user_id", "Nullable(String)"),
    ("dept", "Nullable(String)"),
    ("role", "Nullable(String)"),
    ("action", "String"),
    ("event_type", "Nullable(String)"),
    ("result", "Nullable(String)"),
    ("fail_reason", "Nullable(String)"),
    ("source_ip", "Nullable(String)"),
    ("destination_ip", "Nullable(String)"),
    ("vpn_gateway", "Nullable(String)"),
    ("src_country", "Nullable(String)"),
    ("src_city", "Nullable(String)"),
    ("protocol", "Nullable(String)"),
    ("auth_method", "Nullable(String)"),
    ("client_software", "Nullable(String)"),
    ("user_agent", "Nullable(String)"),
    ("session_id", "Nullable(String)"),
    ("is_off_hours", "Nullable(Bool)"),
    ("is_unusual_ip", "Nullable(Bool)"),
    ("session_duration_sec", "Nullable(UInt32)"),
    ("bytes_sent", "Nullable(UInt64)"),
    ("bytes_recv", "Nullable(UInt64)"),
    ("risk_score", "Nullable(UInt8)"),
    ("risk_tags", "Nullable(String)"),
    ("uri", "Nullable(String)"),
    ("method", "Nullable(String)"),
    ("status_code", "Nullable(UInt16)"),
    ("response_time", "Nullable(Float32)"),
    ("detail", "Nullable(String)"),
    ("severity_level", "Nullable(String)"),
    ("device_info", "Nullable(String)"),
    ("location", "Nullable(String)"),
    ("request_id", "Nullable(String)"),
    ("collected_at", "DateTime"),
    ("parsed_at", "DateTime DEFAULT now()"),
    ("indexed_at", "DateTime DEFAULT now()"),
    ("raw_log", "Nullable(String)"),
    ("parser", "Nullable(String)"),
    ("parse_status", "Nullable(String)"),
]
for name, type_expr in logs_columns:
    client.command(f"ALTER TABLE {logs_table} ADD COLUMN IF NOT EXISTS {name} {type_expr}")

TrainingLogStore(client=client, database=database).ensure_table()
BaselineStore(client=client, database=database).ensure_table()
UebaValidationRepository(client=client, database=database).ensure_table()

client.close()
PYSCHEMA
    )
}

cleanup_demo_database_rows() {
    if [ "${UEBA_RESET_SKIP_DB_CLEANUP:-0}" = "1" ]; then
        log "Skipping database cleanup because UEBA_RESET_SKIP_DB_CLEANUP=1"
        return 0
    fi

    log "Cleaning prior UEBA demo rows"
    (
        cd "${PROJECT_ROOT}"
        "${PYTHON_BIN}" - <<'PYCLEAN'
from __future__ import annotations

import os
import re

import clickhouse_connect

from tests.behavior.ueba_baseline_acceptance.config import AcceptanceConfig


def ident(value: str, label: str) -> str:
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", value):
        raise ValueError(f"invalid {label}: {value!r}")
    return value


def quote(value: str) -> str:
    return "'" + value.replace("\\", "\\\\").replace("'", "\\'") + "'"


host = os.environ["CLICKHOUSE_HOST"]
port = int(os.environ["CLICKHOUSE_PORT"])
username = os.environ.get("CLICKHOUSE_USERNAME") or os.environ.get("CLICKHOUSE_USER") or "default"
password = os.environ.get("CLICKHOUSE_PASSWORD", "")
database = ident(os.environ.get("CLICKHOUSE_DATABASE", "log_analysis"), "database")

client = clickhouse_connect.get_client(
    host=host,
    port=port,
    username=username,
    password=password,
    database="default",
)

config = AcceptanceConfig()
users = sorted(set(config.fixture_usernames + ["fixture_user_validation_nobase"]))
users_sql = ", ".join(quote(u) for u in users)
model_versions = [
    "ueba_baseline_fixture_v2_monthly",
    "ueba_monthly_acceptance_may_init",
    "ueba_monthly_acceptance_june_updated",
]
model_sql = ", ".join(quote(v) for v in model_versions)

commands = [
    f"""
    ALTER TABLE {database}.logs_structured
    DELETE WHERE username IN ({users_sql})
       OR position(ifNull(raw_log, ''), 'ueba_continuous_fixture') > 0
       OR position(ifNull(raw_log, ''), 'ueba_stage14_perf_seed') > 0
    SETTINGS mutations_sync = 1
    """,
    f"""
    ALTER TABLE {database}.user_behavior_baselines
    DELETE WHERE username IN ({users_sql})
       AND model_version IN ({model_sql})
    SETTINGS mutations_sync = 1
    """,
    f"""
    ALTER TABLE {database}.ueba_baseline_training_logs
    DELETE WHERE dataset_id = 'ueba_training_monthly_acceptance'
    SETTINGS mutations_sync = 1
    """,
    f"""
    ALTER TABLE {database}.ueba_validation_results
    DELETE WHERE username IN ({users_sql})
       AND (
            baseline_model_version IN ({model_sql})
            OR startsWith(validation_run_id, 'continuous_normal_')
            OR startsWith(validation_run_id, 'continuous_combo_')
            OR startsWith(validation_run_id, 'continuous_idempotent_')
       )
    SETTINGS mutations_sync = 1
    """,
]
for sql in commands:
    client.command(sql)

client.close()
PYCLEAN
    )
}

launch_demo_menu() {
    [ -x "${DEMO_SCRIPT}" ] || die "Demo script is missing or not executable: ${DEMO_SCRIPT}"
    log "Preparation complete. Entering UEBA demo menu."
    exec bash "${DEMO_SCRIPT}"
}

main() {
    cd "${PROJECT_ROOT}"
    ensure_shell_dependencies
    state_root_is_safe || die "Unsafe UEBA_DEMO_STATE_ROOT: ${STATE_ROOT}"
    clear_demo_port_residue
    reset_demo_state
    ensure_python_env
    ensure_behavior_env
    ensure_clickhouse_running
    ensure_clickhouse_schema
    cleanup_demo_database_rows
    launch_demo_menu
}

main "$@"
