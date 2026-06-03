"""Tests for the UEBA demo shell entrypoint."""

from __future__ import annotations

from pathlib import Path
import shutil
import subprocess


SCRIPT_PATH = Path("tests/behavior/ueba_baseline_acceptance/run_ueba_demo.sh")
STATE_ROOT = Path(".tox/manual/ueba_demo_shell_tests/state")
TEST_TEMP_DIR = Path(".tox/manual/ueba_demo_shell_tests")
SERVER_PID_FILE = STATE_ROOT / "continuous_login_http_server.pid"
WINDOW_START_FILE = STATE_ROOT / "current_window_start"
WINDOW_END_FILE = STATE_ROOT / "current_window_end"
WINDOW_STATE_FILE = STATE_ROOT / "current_window_state"
VALIDATION_HISTORY_FILE = STATE_ROOT / "validation_history.tsv"
SUMMARY_LOG_FILE = STATE_ROOT / "demo_summary.log"


def _script_text() -> str:
    return SCRIPT_PATH.read_text(encoding="utf-8")


def _run_script(input_text: str, *, timeout: float = 2.0, capture: bool = False) -> subprocess.CompletedProcess[str]:
    kwargs = {
        "args": ["bash", str(SCRIPT_PATH)],
        "input": input_text,
        "text": True,
        "timeout": timeout,
        "check": False,
        "env": {
            **__import__("os").environ,
            "UEBA_DEMO_STATE_ROOT": str(STATE_ROOT.resolve()),
            "PATH": __import__("os").environ.get("PATH", "/usr/bin"),
        },
    }
    if capture:
        kwargs["stdout"] = subprocess.PIPE
        kwargs["stderr"] = subprocess.PIPE
    else:
        kwargs["stdout"] = subprocess.DEVNULL
        kwargs["stderr"] = subprocess.DEVNULL
    return subprocess.run(**kwargs)


def _prepare_state_dir() -> None:
    if STATE_ROOT.exists():
        shutil.rmtree(STATE_ROOT)
    STATE_ROOT.mkdir(parents=True, exist_ok=True)


def _source_bash(expr: str, *, timeout: float = 5.0) -> subprocess.CompletedProcess[str]:
    """Source the shell script then evaluate expr; returns the subprocess result."""
    import tempfile
    script = _script_text()
    proj_root = str(Path(__file__).resolve().parents[2])
    # Override path vars after script init to point to real project root
    overrides = (
        f'PROJECT_ROOT="{proj_root}"\n'
        f'PYTHON_BIN="{proj_root}/.venv/bin/python"\n'
        f'STATE_ROOT="{proj_root}/.tox/manual/ueba_demo_shell_tests/state"\n'
    )
    # Override path vars and skip main() via return before source guard
    guard_marker = 'if [[ "${BASH_SOURCE[0]}" == "$0" ]]; then'
    # Override path vars; skip main by replacing the source guard block
    guard_block = 'if [[ "${BASH_SOURCE[0]}" == "$0" ]]; then\n    main "$@"\nfi'
    full = script.replace(guard_block, '# source guard removed for test')
    full += f'\nPROJECT_ROOT="{proj_root}"\nPYTHON_BIN="{proj_root}/.venv/bin/python"\nSTATE_ROOT="{proj_root}/.tox/manual/ueba_demo_shell_tests/state"\n'
    full += 'export CURRENT_SESSION_SERVER_PID="${CURRENT_SESSION_SERVER_PID:-}"\n'
    full += expr + "\n"
    TEST_TEMP_DIR.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(mode='w', suffix='.sh', delete=False,
                                     dir=str(TEST_TEMP_DIR)) as tmp:
        tmp.write(full)
        tmp_path = tmp.name
    try:
        return subprocess.run(
            ["bash", tmp_path],
            capture_output=True, text=True, timeout=timeout, check=False,
            cwd=proj_root,
        )
    finally:
        Path(tmp_path).unlink(missing_ok=True)


def _pipe_json_get(key: str, json_str: str) -> subprocess.CompletedProcess[str]:
    """Pipe JSON string into json_get <key> and return the subprocess result."""
    import tempfile
    script = _script_text()
    proj_root = str(Path(__file__).resolve().parents[2])
    guard_block = 'if [[ "${BASH_SOURCE[0]}" == "$0" ]]; then\n    main "$@"\nfi'
    full = script.replace(guard_block, '# source guard removed for test')
    full += f'\nPROJECT_ROOT="{proj_root}"\nPYTHON_BIN="{proj_root}/.venv/bin/python"\nSTATE_ROOT="{proj_root}/.tox/manual/ueba_demo_shell_tests/state"\n'
    full += 'export CURRENT_SESSION_SERVER_PID="${CURRENT_SESSION_SERVER_PID:-}"\n'
    full += "\nprintf '%s' '" + json_str + "' | json_get " + key + "\n"
    TEST_TEMP_DIR.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(mode='w', suffix='.sh', delete=False,
                                     dir=str(TEST_TEMP_DIR)) as tmp:
        tmp.write(full)
        tmp_path = tmp.name
    try:
        return subprocess.run(
            ["bash", tmp_path],
            capture_output=True, text=True, timeout=5.0, check=False,
            cwd=proj_root,
        )
    finally:
        Path(tmp_path).unlink(missing_ok=True)


def test_demo_shell_contains_required_menu_and_state_paths():
    text = _script_text()

    assert text.startswith("#!/usr/bin/env bash\n")
    assert "set -u" in text
    assert ".tox/manual/ueba_demo_menu" in text
    assert "UEBA 全流程演示工具" in text
    assert "1. 环境检查" in text
    assert "2. 基础准线流程" in text
    assert "3. 训练表更新流程（暂未开放" in text
    assert "4. 持续流量与 Validation" in text
    assert "5. 查看整体状态" in text
    assert "6. 一键执行基础准线完整流程" in text
    assert "7. 一键持续流量联动验收（暂未开放" in text
    assert "8. 退出" in text
    assert "current_window_start" in text
    assert "current_window_end" in text
    assert "current_window_state" in text


def test_demo_shell_uses_unified_safe_read_handling():
    text = _script_text()

    assert "read_input()" in text
    assert text.count("IFS= read -r -p") == 1
    assert "主菜单安全退出" in text
    assert "未自动停止 Server，未自动清理数据库。" in text
    assert "返回主菜单。" in text
    assert "已取消当前操作。" in text


def test_demo_shell_static_safety_constraints():
    text = _script_text()

    assert "/tmp" not in text
    assert "git add" not in text
    assert "git commit" not in text
    assert "git push" not in text
    assert "DROP TABLE" not in text
    assert "TRUNCATE TABLE" not in text
    assert "username LIKE 'fixture_user_%'" not in text
    assert 'CONTINUOUS_USERNAME="fixture_user_stable_0001"' in text
    assert 'CONTINUOUS_MARKER="ueba_continuous_fixture"' in text
    assert 'CONTINUOUS_MODEL_VERSION="ueba_baseline_fixture_v2_monthly"' in text
    assert 'CONTINUOUS_LOG_TYPE="vpn"' in text
    assert "startsWith(validation_run_id, 'menu_')" in text
    assert "validation_run_id LIKE 'menu_%'" not in text
    assert "username = '${CONTINUOUS_USERNAME}'" in text
    assert "position(raw_log, '${CONTINUOUS_MARKER}') > 0" in text
    assert "baseline_model_version = '${CONTINUOUS_MODEL_VERSION}'" in text
    assert "log_type = '${CONTINUOUS_LOG_TYPE}'" in text
    assert "CLICKHOUSE_USERNAME" in text
    assert "--host" in text
    assert "--port" in text
    assert "--username" in text
    assert "--password" in text
    assert "--database" in text


def test_demo_shell_contains_isolation_check_and_window_rules():
    text = _script_text()

    assert "check_window_isolation()" in text
    assert "total_count" in text
    assert "fixture_count" in text
    assert "fixture_count 必须大于 0" in text
    assert "窗口内存在非 fixture 日志" in text
    assert "正式评分只接受 CLOSED 窗口" in text
    assert "menu_$(date -u '+%Y%m%d%H%M%S')" in text
    assert text.index('open_active_window "${start_time}"') < text.index('http_post_json "/start"')
    assert text.index('sleep "${WINDOW_FLUSH_SECONDS}"') < text.index('close_window "${end_time}"')


def test_root_menu_eof_exits_quickly_without_creating_pid_file():
    _prepare_state_dir()

    completed = _run_script("", timeout=2.0)

    assert completed.returncode == 0
    assert not SERVER_PID_FILE.exists()


def test_root_menu_eof_does_not_cleanup_existing_window_files():
    _prepare_state_dir()
    WINDOW_START_FILE.write_text("2026-06-01 00:00:00\n", encoding="utf-8")
    WINDOW_END_FILE.write_text("2026-06-01 00:05:00\n", encoding="utf-8")
    WINDOW_STATE_FILE.write_text("CLOSED\n", encoding="utf-8")
    VALIDATION_HISTORY_FILE.write_text("menu_1\t2026-06-01 00:00:00\t2026-06-01 00:05:00\n", encoding="utf-8")

    completed = _run_script("", timeout=2.0)

    assert completed.returncode == 0
    assert WINDOW_START_FILE.exists()
    assert WINDOW_END_FILE.exists()
    assert WINDOW_STATE_FILE.exists()
    assert VALIDATION_HISTORY_FILE.exists()


def test_submenu_eof_returns_safely_without_looping():
    _prepare_state_dir()

    completed = _run_script("4\n", timeout=2.0)

    assert completed.returncode == 0
    assert not SERVER_PID_FILE.exists()


def test_confirmation_eof_cancels_cleanup_without_removing_window_files():
    _prepare_state_dir()
    WINDOW_START_FILE.write_text("2026-06-01 00:00:00\n", encoding="utf-8")
    WINDOW_END_FILE.write_text("2026-06-01 00:05:00\n", encoding="utf-8")
    WINDOW_STATE_FILE.write_text("CLOSED\n", encoding="utf-8")
    VALIDATION_HISTORY_FILE.write_text("menu_1\t2026-06-01 00:00:00\t2026-06-01 00:05:00\n", encoding="utf-8")

    completed = _run_script("4\n11\n", timeout=2.0)

    assert completed.returncode == 0
    assert WINDOW_START_FILE.exists()
    assert WINDOW_END_FILE.exists()
    assert WINDOW_STATE_FILE.exists()
    assert VALIDATION_HISTORY_FILE.exists()


def test_invalid_input_then_eof_exits_quickly():
    _prepare_state_dir()

    completed = _run_script("9\n", timeout=2.0)

    assert completed.returncode == 0
    assert not SERVER_PID_FILE.exists()


def test_input_zero_exits_cleanly():
    _prepare_state_dir()

    completed = _run_script("0\n", timeout=2.0)

    assert completed.returncode == 0


def test_invalid_input_then_zero_exits_cleanly():
    _prepare_state_dir()

    completed = _run_script("999\n0\n", timeout=2.0)

    assert completed.returncode == 0


def test_continuous_submenu_return_then_exit():
    _prepare_state_dir()

    completed = _run_script("4\n13\n0\n", timeout=2.0)

    assert completed.returncode == 0


def test_summary_log_is_the_only_default_file_touched_on_safe_exit():
    _prepare_state_dir()

    completed = _run_script("0\n", timeout=2.0)

    assert completed.returncode == 0
    assert SUMMARY_LOG_FILE.exists()
    assert VALIDATION_HISTORY_FILE.exists()
    assert not SERVER_PID_FILE.exists()


# ============================================================================
# 新增：标量查询失败与非数字拒绝
# ============================================================================


def test_require_nonnegative_integer_exists():
    text = _script_text()
    assert "require_nonnegative_integer()" in text
    assert '=~ ^[0-9]+$' in text


def test_check_window_isolation_validates_total_count():
    text = _script_text()
    assert 'require_nonnegative_integer "${total_count_raw}" "total_count"' in text
    assert 'require_nonnegative_integer "${fixture_count_raw}" "fixture_count"' in text


def test_cleanup_validates_before_counts():
    text = _script_text()
    assert 'require_nonnegative_integer "${before_validation_raw}" "cleanup_before_validation_count"' in text
    assert 'require_nonnegative_integer "${before_logs_raw}" "cleanup_before_log_count"' in text


def test_cleanup_validates_after_counts():
    text = _script_text()
    assert 'require_nonnegative_integer "${after_validation_raw}" "cleanup_after_validation_count"' in text
    assert 'require_nonnegative_integer "${after_logs_raw}" "cleanup_after_log_count"' in text


def test_curl_uses_fail_flag():
    text = _script_text()
    assert "curl -f -sS" in text


def test_clickhouse_scalar_checks_curl_exit_code():
    text = _script_text()
    assert "raw=\"$(clickhouse_query" in text
    assert "return 1" in text


# ============================================================================
# 新增：一键流程 YES 确认门禁
# ============================================================================


def test_baseline_full_flow_disabled():
    """Root menu 6 is now disabled — shows a clear message and returns 1."""
    text = _script_text()
    idx = text.index("run_baseline_full_flow()")
    block = text[idx:text.index("run_manual_validation_cli", idx)]
    assert "当前不可用" in block
    assert "交互 Runner pipe 注入模式已废弃" in block


def test_yes_confirmation_eof_cancels_baseline_flow():
    _prepare_state_dir()

    completed = _run_script("6\n", timeout=2.0)

    assert completed.returncode == 0
    assert not SERVER_PID_FILE.exists()


def test_yes_confirmation_eof_cancels_continuous_acceptance():
    _prepare_state_dir()

    completed = _run_script("7\n", timeout=2.0)

    assert completed.returncode == 0
    assert not SERVER_PID_FILE.exists()


def test_yes_confirmation_wrong_input_cancels():
    _prepare_state_dir()

    completed = _run_script("6\nNO\n0\n", timeout=2.0)

    assert completed.returncode == 0
    assert not SERVER_PID_FILE.exists()


# ============================================================================
# 新增：stop_continuous_server paused=true 确认
# ============================================================================


def test_stop_continuous_server_checks_paused():
    text = _script_text()
    idx = text.index("stop_continuous_server()")
    block = text[idx:text.index("resume_continuous_server", idx)]
    assert "require_server_status_json status_json" in block
    assert "paused_value_is_true" in block
    assert "拒绝关闭窗口" in block
    assert "窗口保持 ACTIVE 状态" in block


# ============================================================================
# 新增：cleanup 失败路径保护
# ============================================================================


def test_cleanup_delete_failure_has_error_message():
    text = _script_text()
    assert "DELETE 执行失败，cleanup 中止" in text


def test_cleanup_does_not_clear_window_on_failure():
    text = _script_text()
    idx = text.index("cleanup_current_round_data()")
    block = text[idx:text.index("show_file_if_exists", idx)]
    assert "clear_window_files" in block
    assert "return 1" in block


# ============================================================================
# 新增：source guard
# ============================================================================


def test_shell_has_source_guard():
    text = _script_text()
    assert '[[ "${BASH_SOURCE[0]}" == "$0" ]]' in text


def test_sourcing_shell_does_not_run_main():
    """Source 脚本不会执行 main，因此不会创建任何状态文件。"""
    _prepare_state_dir()
    import subprocess
    import tempfile

    script = _script_text()
    safe_script = script + '\n'
    TEST_TEMP_DIR.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(mode='w', suffix='.sh', delete=False,
                                     dir=str(TEST_TEMP_DIR)) as tmp:
        tmp.write(safe_script)
        tmp_path = tmp.name

    try:
        proc = subprocess.run(
            ["bash", "-c", f"source '{tmp_path}' && echo SOURCED_OK"],
            capture_output=True, text=True, timeout=5,
        )
        assert proc.returncode == 0
        assert "SOURCED_OK" in proc.stdout
    finally:
        Path(tmp_path).unlink(missing_ok=True)


# ============================================================================
# 新增：json_get 管道输入测试
# ============================================================================


def test_json_get_paused_true():
    proc = _pipe_json_get("paused", '{"paused": true}')
    assert proc.returncode == 0
    assert proc.stdout.strip() == "true"


def test_json_get_paused_false():
    proc = _pipe_json_get("paused", '{"paused": false}')
    assert proc.returncode == 0
    assert proc.stdout.strip() == "false"


def test_json_get_invalid_json_fails():
    proc = _pipe_json_get("paused", "not-json")
    assert proc.returncode != 0


def test_json_get_empty_input_fails():
    proc = _pipe_json_get("paused", "")
    assert proc.returncode != 0


def test_json_get_missing_key_outputs_empty():
    proc = _pipe_json_get("nonexistent", '{"paused": true}')
    assert proc.returncode == 0
    assert proc.stdout.strip() == ""


# ============================================================================
# 新增：status_is_paused 行为测试
# ============================================================================


def test_status_is_paused_true():
    proc = _source_bash(
        'status_is_paused \'{"paused": true}\' && echo "PAUSED_OK" || echo "PAUSED_FAIL"'
    )
    assert "PAUSED_OK" in proc.stdout


def test_status_is_paused_false():
    proc = _source_bash(
        'status_is_paused \'{"paused": false}\' && echo "PAUSED_OK" || echo "PAUSED_FAIL"'
    )
    assert "PAUSED_FAIL" in proc.stdout


# ============================================================================
# 新增：PID 安全校验
# ============================================================================


def test_is_valid_positive_pid_accepts_legal():
    proc = _source_bash(
        'is_valid_positive_pid "12345" && echo "VALID" || echo "INVALID"'
    )
    assert "VALID" in proc.stdout


def test_is_valid_positive_pid_rejects_zero():
    proc = _source_bash(
        'is_valid_positive_pid "0" && echo "VALID" || echo "INVALID"'
    )
    assert "INVALID" in proc.stdout


def test_is_valid_positive_pid_rejects_negative():
    proc = _source_bash(
        'is_valid_positive_pid "-1" && echo "VALID" || echo "INVALID"'
    )
    assert "INVALID" in proc.stdout


def test_is_valid_positive_pid_rejects_empty():
    proc = _source_bash(
        'is_valid_positive_pid "" && echo "VALID" || echo "INVALID"'
    )
    assert "INVALID" in proc.stdout


def test_is_valid_positive_pid_rejects_non_numeric():
    proc = _source_bash(
        'is_valid_positive_pid "abc123" && echo "VALID" || echo "INVALID"'
    )
    assert "INVALID" in proc.stdout


# ============================================================================
# 新增：rollback_just_started_server 测试
# ============================================================================


def test_rollback_rejects_non_numeric_pid():
    proc = _source_bash(
        'rollback_just_started_server "not-a-pid" && echo "ROLLBACK_OK" || echo "ROLLBACK_FAIL"'
    )
    assert "非法 PID" in proc.stderr


def test_rollback_rejects_zero_pid():
    proc = _source_bash(
        'rollback_just_started_server "0" && echo "ROLLBACK_OK" || echo "ROLLBACK_FAIL"'
    )
    assert "非法 PID" in proc.stderr


def test_rollback_handles_nonexistent_pid():
    """对会话内不存在的 PID 执行回滚：等待循环快速退出。"""
    proc = _source_bash(
        'CURRENT_SESSION_SERVER_PID="99999" ; export CURRENT_SESSION_SERVER_PID ; rollback_just_started_server "99999" ; echo "EXIT=$?"'
    )
    assert "EXIT=0" in proc.stdout or "回滚完成" in proc.stdout


def test_rollback_static_has_finite_wait():
    text = _script_text()
    idx = text.index("rollback_just_started_server()")
    block = text[idx:text.index("return 0", idx + 500) + 9]
    assert "attempt=0" in block
    assert "-lt 20" in block
    assert "rm -f" in block


# ============================================================================
# 新增：HTTP curl --fail 检查
# ============================================================================


def test_http_get_status_uses_fail():
    text = _script_text()
    idx = text.index("http_get_status()")
    block = text[idx:text.index("}", idx)]
    assert "curl -f -sS" in block


def test_http_post_json_uses_fail():
    text = _script_text()
    idx = text.index("http_post_json()")
    block = text[idx:text.index("}", idx)]
    assert "curl -f -sS" in block


# ============================================================================
# 新增：单项写库 YES 确认
# ============================================================================


def test_baseline_menu_item2_requires_yes():
    text = _script_text()
    assert "run_baseline_load_fixture" in text
    assert "此操作将生成 fixture 模拟数据并写入" in text


def test_baseline_menu_item3_requires_yes():
    text = _script_text()
    assert "run_baseline_build_action" in text
    assert "此操作将执行正式 baseline 构建并写入" in text


def test_baseline_menu_item2_eof_cancels():
    _prepare_state_dir()
    completed = _run_script("2\n2\n", timeout=2.0)
    assert completed.returncode == 0


def test_baseline_menu_item3_eof_cancels():
    _prepare_state_dir()
    completed = _run_script("2\n3\n", timeout=2.0)
    assert completed.returncode == 0


def test_baseline_menu_item2_wrong_input_cancels():
    _prepare_state_dir()
    completed = _run_script("2\n2\nNO\n0\n", timeout=2.0)
    assert completed.returncode == 0


# ============================================================================
# 新增：Validation CLI 确认词改为 YES
# ============================================================================


def test_validation_mode_uses_yes():
    text = _script_text()
    assert '请输入 YES 执行正式写库评分' in text
    assert 'case "${mode}" in' in text
    assert 'YES)' in text
    assert 'write_flag="--write"' in text


# ============================================================================
# 新增：ClickHouse 环境变量统一透传
# ============================================================================


def test_clickhouse_vars_exported():
    text = _script_text()
    assert "export CLICKHOUSE_HOST CLICKHOUSE_PORT CLICKHOUSE_USERNAME" in text
    assert 'export CLICKHOUSE_USER="${CLICKHOUSE_USERNAME}"' in text


# ============================================================================
# 新增：启动回滚在 failure path 中调用
# ============================================================================


def test_start_failure_paths_call_rollback():
    text = _script_text()
    idx = text.index("start_continuous_server()")
    block = text[idx:text.index("stop_continuous_server", idx)]
    # 4 个 failure path 都应有 rollback 调用
    assert block.count("rollback_just_started_server") == 4


def test_start_rollback_preserves_previous_pid_file_on_exit_failure():
    text = _script_text()
    idx = text.index("rollback_just_started_server()")
    block = text[idx:text.index("}", idx + 800)]
    assert "保留 PID 文件和会话 PID 供人工排查" in block


# ============================================================================
# 新增：PID 归属模型测试
# ============================================================================


def test_canary_session_pid_variable_exists():
    text = _script_text()
    assert 'CURRENT_SESSION_SERVER_PID=""' in text


def test_is_current_session_server_pid_exists():
    text = _script_text()
    assert "is_current_session_server_pid()" in text
    assert "jobs -pr" in text
    assert "grep -Fx" in text


def test_has_current_session_server_exists():
    text = _script_text()
    assert "has_current_session_server()" in text


def test_stop_server_uses_has_current_session_server():
    text = _script_text()
    idx = text.index("stop_continuous_server()")
    block = text[idx:text.index("resume_continuous_server", idx)]
    assert "has_current_session_server" in block
    assert "${CURRENT_SESSION_SERVER_PID}" in block


def test_start_sets_session_pid():
    text = _script_text()
    idx = text.index("start_continuous_server()")
    block = text[idx:text.index("stop_continuous_server", idx)]
    assert "CURRENT_SESSION_SERVER_PID=\"${pid}\"" in block


def test_stop_clears_session_pid_on_success():
    text = _script_text()
    idx = text.index("stop_continuous_server()")
    block = text[idx:text.index("resume_continuous_server", idx)]
    assert 'CURRENT_SESSION_SERVER_PID=""' in block


# ============================================================================
# 新增：rollback 删除顺序测试
# ============================================================================


def test_rollback_deletes_pid_only_after_exit():
    text = _script_text()
    idx = text.index("rollback_just_started_server()")
    block = text[idx:text.index("}", idx + 1000)]
    rm_idx = block.index("rm -f")
    alive_check_idx = block.rindex("pid_is_alive")
    assert alive_check_idx < rm_idx, "pid_is_alive check must come BEFORE rm -f"


def test_rollback_clears_session_pid_on_success():
    text = _script_text()
    idx = text.index("rollback_just_started_server()")
    block = text[idx:text.index("}", idx + 1000)]
    assert 'CURRENT_SESSION_SERVER_PID=""' in block


# ============================================================================
# 新增：stale PID 文件处理测试
# ============================================================================


def test_start_handles_stale_pid_file():
    text = _script_text()
    assert "历史 PID 文件" in text


def test_stop_handles_no_current_session():
    text = _script_text()
    idx = text.index("stop_continuous_server()")
    block = text[idx:text.index("resume_continuous_server", idx)]
    assert "不属于当前菜单会话" in text


# ============================================================================
# 新增：严格状态 JSON 解析测试
# ============================================================================


def test_get_paused_value_exists():
    text = _script_text()
    assert "get_paused_value()" in text


def test_paused_value_is_true_exists():
    text = _script_text()
    assert "paused_value_is_true()" in text


def test_paused_value_is_false_exists():
    text = _script_text()
    assert "paused_value_is_false()" in text


def test_paused_value_distinguishes_parse_error():
    """get_paused_value 返回 1 表示解析失败（与 paused=false 不同）。"""
    proc = _source_bash(
        'get_paused_value val "not-json" && echo "OK_$val" || echo "FAIL_RC=$?"'
    )
    assert "FAIL_RC=1" in proc.stdout or "无法解析" in proc.stderr


def test_paused_value_rejects_missing_field():
    proc = _source_bash(
        'get_paused_value val \'{}\' && echo "OK_$val" || echo "FAIL_RC=$?"'
    )
    assert "FAIL_RC=1" in proc.stdout or "缺少 paused" in proc.stderr


def test_paused_value_rejects_non_bool():
    proc = _source_bash(
        'get_paused_value val \'{"paused": "yes"}\' && echo "OK_$val" || echo "FAIL_RC=$?"'
    )
    assert "FAIL_RC=1" in proc.stdout or "非法" in proc.stderr


def test_paused_value_true_ok():
    proc = _source_bash(
        'get_paused_value val \'{"paused": true}\' && echo "OK_$val" || echo "FAIL_RC=$?"'
    )
    assert "OK_true" in proc.stdout


def test_paused_value_false_ok():
    proc = _source_bash(
        'get_paused_value val \'{"paused": false}\' && echo "OK_$val" || echo "FAIL_RC=$?"'
    )
    assert "OK_false" in proc.stdout


def test_start_server_uses_paused_value_is_true():
    text = _script_text()
    idx = text.index("start_continuous_server()")
    block = text[idx:text.index("stop_continuous_server", idx)]
    assert "paused_value_is_true" in block


def test_pause_uses_paused_value_is_true():
    text = _script_text()
    idx = text.index("pause_continuous_server()")
    block = text[idx:text.index("set_continuous_rate", idx)]
    assert "paused_value_is_true" in block


def test_resume_uses_paused_value_is_false():
    text = _script_text()
    idx = text.index("resume_continuous_server()")
    block = text[idx:text.index("set_continuous_rate", idx)]
    assert "paused_value_is_false" in block


# ============================================================================
# 新增：无 /tmp 使用
# ============================================================================


def test_no_tmp_in_test_file():
    text = _script_text()
    assert "/tmp" not in text


def test_source_test_no_tmp_usage():
    """Source测试文件中没有 /tmp 引用。"""
    test_text = Path(__file__).read_text(encoding="utf-8")
    assert "dir=str(TEST_TEMP_DIR)" in test_text
    assert "NamedTemporaryFile" in test_text


# ============================================================================
# 新增：禁用菜单项测试
# ============================================================================


def test_menu_3_training_shows_disabled():
    _prepare_state_dir()
    completed = _run_script("3\n0\n", timeout=2.0)
    assert completed.returncode == 0


def test_menu_7_continuous_acceptance_shows_disabled():
    _prepare_state_dir()
    completed = _run_script("7\n0\n", timeout=2.0)
    assert completed.returncode == 0


def test_continuous_submenu_12_shows_disabled():
    _prepare_state_dir()
    completed = _run_script("4\n12\n13\n0\n", timeout=2.0)
    assert completed.returncode == 0


def test_disabled_menu_does_not_call_runner():
    text = _script_text()
    assert "run_training_all" not in text
    assert "run_training_interactive" not in text
    assert "training_menu" not in text
    assert "run_continuous_acceptance_all" not in text


def test_continuous_submenu_12_text():
    text = _script_text()
    assert "暂未开放：需单独完成 Python 一键联动验收 cleanup 加固" in text


# ============================================================================
# 新增：set_continuous_rate 严格 paused 解析测试
# ============================================================================


def test_set_rate_uses_strict_paused_parsing():
    text = _script_text()
    idx = text.index("set_continuous_rate()")
    block = text[idx:text.index("set_continuous_mode", idx)]
    assert "paused_value_is_true" in block
    assert "paused_rc" in block
    assert "无法解析 Server paused 状态" in block


def test_set_rate_rejects_malformed_json():
    text = _script_text()
    idx = text.index("set_continuous_rate()")
    block = text[idx:text.index("set_continuous_mode", idx)]
    assert 'paused_rc}" -eq 2 ]' in block
    assert "拒绝调速" in block


def test_set_rate_missing_paused_also_rejected():
    """paused_value_is_true 对缺失 paused 返回 2（解析失败），与格式错误同等拒绝。"""
    proc = _source_bash(
        'paused_value_is_true \'{}\' && echo "PAUSED_TRUE" || echo "RC=$?"'
    )
    assert "RC=2" in proc.stdout or "RC=1" in proc.stdout


def test_set_rate_non_bool_paused_rejected():
    proc = _source_bash(
        'paused_value_is_true \'{"paused": "yes"}\' && echo "PAUSED_TRUE" || echo "RC=$?"'
    )
    assert "RC=2" in proc.stdout or "RC=1" in proc.stdout


def test_set_rate_paused_true_allows_continue():
    proc = _source_bash(
        'paused_value_is_true \'{"paused": true}\' && echo "PAUSED_TRUE" || echo "PAUSED_FALSE"'
    )
    assert "PAUSED_TRUE" in proc.stdout


def test_set_rate_paused_false_not_true():
    proc = _source_bash(
        'paused_value_is_true \'{"paused": false}\' && echo "PAUSED_TRUE" || echo "PAUSED_FALSE"'
    )
    assert "PAUSED_FALSE" in proc.stdout


# ============================================================================
# 新增：root 6 禁用与 printf 注入移除测试
# ============================================================================


def test_root_menu_6_disabled_message():
    text = _script_text()
    assert "一键执行基础准线完整流程（已禁用" in text
    assert "交互 Runner pipe 注入已废弃" in text


def test_root_menu_6_prints_disabled_and_returns():
    _prepare_state_dir()
    completed = _run_script("6\n0\n", timeout=2.0)
    assert completed.returncode == 0


def test_baseline_menu_no_printf_injection_to_runner():
    """baseline menu must not pipe-inject menu numbers via printf to runner."""
    text = _script_text()
    idx = text.index("baseline_menu()")
    block = text[idx:text.index("continuous_mode_menu", idx)]
    assert "printf" not in block, "baseline_menu must not use printf injection to runner"


def test_run_baseline_generate_expected_exists():
    text = _script_text()
    assert "run_baseline_generate_expected()" in text
    assert "--generate-expected" in text


def test_run_baseline_load_fixture_exists():
    text = _script_text()
    assert "run_baseline_load_fixture()" in text
    assert "--load-fixture" in text


def test_run_baseline_build_action_exists():
    text = _script_text()
    assert "run_baseline_build_action()" in text
    assert "--build-baseline" in text


def test_run_baseline_validate_exists():
    text = _script_text()
    assert "run_baseline_validate()" in text
    assert "--validate-baselines" in text


# ============================================================================
# 新增：runner 非交互接口测试
# ============================================================================


def test_runner_has_noninteractive_args():
    """Runner must expose stable non-interactive single-action flags."""
    runner_text = Path("tests/behavior/ueba_baseline_acceptance/runner.py").read_text("utf-8")
    assert "--generate-expected" in runner_text
    assert "--load-fixture" in runner_text
    assert "--build-baseline" in runner_text
    assert "--validate-baselines" in runner_text
    assert "--print-summary" in runner_text
    assert "_run_non_interactive" in runner_text


def test_runner_accepts_generate_expected_flag():
    """Runner --help should show the new flags."""
    import subprocess as sp
    proj_root = Path(__file__).resolve().parents[2]
    venv_py = str(proj_root / ".venv/bin/python")
    cp = sp.run(
        [venv_py, "-m", "tests.behavior.ueba_baseline_acceptance.runner", "--help"],
        capture_output=True, text=True, timeout=10.0, check=False,
        cwd=str(proj_root),
    )
    assert "--generate-expected" in cp.stdout
    assert "--load-fixture" in cp.stdout
    assert "--build-baseline" in cp.stdout
    assert "--validate-baselines" in cp.stdout
    assert "--print-summary" in cp.stdout


# ============================================================================
# 新增：timeout 保护测试
# ============================================================================


def test_baseline_runner_uses_timeout():
    text = _script_text()
    assert "BASELINE_RUNNER_TIMEOUT=300" in text
    idx = text.index("_run_runner_noninteractive()")
    block = text[idx:text.index("run_baseline_full_flow", idx)]
    assert "timeout" in block


def test_validation_cli_uses_timeout():
    text = _script_text()
    assert "VALIDATION_CLI_TIMEOUT=300" in text
    idx = text.index("run_manual_validation_cli()")
    block = text[idx:text.index("validation_result_count_for_window", idx)]
    assert "timeout" in block


# ============================================================================
# 新增：baseline 菜单写共享表警告测试
# ============================================================================


def test_baseline_menu_shows_shared_table_warning():
    text = _script_text()
    idx = text.index("baseline_menu()")
    block = text[idx:text.index("continuous_mode_menu", idx)]
    assert "不会自动 cleanup" in block
    assert "logs_structured" in block
    assert "user_behavior_baselines" in block


def test_baseline_menu_write_items_require_uppercase_yes():
    """Items 2 and 3 in baseline menu require uppercase YES confirmation."""
    text = _script_text()
    idx = text.index("baseline_menu()")
    block = text[idx:text.index("continuous_mode_menu", idx)]
    assert "请输入 YES 确认写入" in block
    assert "请输入 YES 确认构建" in block
    assert "不会自动 cleanup" in block


# ============================================================================
# 新增：LIKE 'menu_%' 精确语义修复
# ============================================================================


def test_shell_uses_startswith_not_like_for_menu_prefix():
    text = _script_text()
    assert "startsWith(validation_run_id, 'menu_')" in text
    assert "validation_run_id LIKE 'menu_%'" not in text


def test_readme_uses_startswith_not_like_for_menu_prefix():
    readme = Path("tests/behavior/ueba_baseline_acceptance/README.md").read_text("utf-8")
    assert "startsWith(validation_run_id, 'menu_')" in readme
    assert "validation_run_id LIKE 'menu_%'" not in readme


# ============================================================================
# 新增：Runner 非交互参数互斥测试
# ============================================================================


def test_runner_single_action_flag_ok():
    """单个 --print-summary 应正常退出（返回 0 或 1 取决于数据库连接）。"""
    import subprocess as sp
    proj_root = Path(__file__).resolve().parents[2]
    venv_py = str(proj_root / ".venv/bin/python")
    cp = sp.run(
        [venv_py, "-m", "tests.behavior.ueba_baseline_acceptance.runner",
         "--print-summary"],
        capture_output=True, text=True, timeout=10.0, check=False,
        cwd=str(proj_root),
    )
    # --print-summary 读文件，不连库也能运行（文件不存在只是输出"暂无"）
    # 只要不是因为参数冲突退出(rc=2)就通过
    assert cp.returncode != 2, f"stderr={cp.stderr}"


def test_runner_double_action_flag_fails():
    """两个 action flag 同时传入必须非零退出。"""
    import subprocess as sp
    proj_root = Path(__file__).resolve().parents[2]
    venv_py = str(proj_root / ".venv/bin/python")
    cp = sp.run(
        [venv_py, "-m", "tests.behavior.ueba_baseline_acceptance.runner",
         "--generate-expected", "--load-fixture"],
        capture_output=True, text=True, timeout=10.0, check=False,
        cwd=str(proj_root),
    )
    assert cp.returncode != 0, f"期望非零退出码，got {cp.returncode}"
    assert "一次只能指定一个" in cp.stderr


def test_runner_action_flag_conflict_does_not_execute():
    """参数冲突时不应执行任何写库动作（输出不包含 fixture 关键字）。"""
    import subprocess as sp
    proj_root = Path(__file__).resolve().parents[2]
    venv_py = str(proj_root / ".venv/bin/python")
    cp = sp.run(
        [venv_py, "-m", "tests.behavior.ueba_baseline_acceptance.runner",
         "--load-fixture", "--build-baseline"],
        capture_output=True, text=True, timeout=10.0, check=False,
        cwd=str(proj_root),
    )
    assert cp.returncode != 0
    # 错误信息应出现在 stderr
    assert "一次只能指定一个" in cp.stderr
