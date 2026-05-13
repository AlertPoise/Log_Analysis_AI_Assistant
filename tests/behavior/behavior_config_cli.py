#!/usr/bin/env python3
"""Behavior 模块交互式配置入口。"""

from __future__ import annotations

import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
CONFIG_DIR = PROJECT_ROOT / "config"
CONFIG_PATH = CONFIG_DIR / "behavior.env"
EXAMPLE_PATH = CONFIG_DIR / "behavior.env.example"

DEFAULTS = {
    "BEHAVIOR_TIME_WINDOW_HOURS": "24",
    "ANOMALY_THRESHOLD": "0.7",
    "MIN_SAMPLES_FOR_PROFILE": "10",
}

DESCRIPTIONS = {
    "BEHAVIOR_TIME_WINDOW_HOURS": "默认统计窗口（小时），影响频率类指标的计算口径。",
    "ANOMALY_THRESHOLD": "异常分数达到该值时，结果会标记为正式告警。",
    "MIN_SAMPLES_FOR_PROFILE": "用户历史样本至少达到该值后，基线才视为可靠。",
}


def ensure_config_exists() -> None:
    """确保配置文件存在。"""
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    if CONFIG_PATH.exists():
        return

    if EXAMPLE_PATH.exists():
        CONFIG_PATH.write_text(EXAMPLE_PATH.read_text(encoding="utf-8"), encoding="utf-8")
    else:
        write_config(DEFAULTS)


def read_config() -> dict[str, str]:
    """读取配置文件中的 key=value。"""
    ensure_config_exists()
    values = dict(DEFAULTS)

    for line in CONFIG_PATH.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, value = stripped.split("=", 1)
        values[key.strip()] = value.strip()

    return values


def write_config(values: dict[str, str]) -> None:
    """写回配置文件。"""
    lines = [
        "# Behavior 模块专属配置",
        "# 修改后重新运行测试或脚本即可生效",
        "",
        "# 默认统计窗口（小时）",
        f"BEHAVIOR_TIME_WINDOW_HOURS={values['BEHAVIOR_TIME_WINDOW_HOURS']}",
        "",
        "# 异常分数达到该值时标记为正式告警",
        f"ANOMALY_THRESHOLD={values['ANOMALY_THRESHOLD']}",
        "",
        "# 至少多少条历史样本后，用户画像/基线才视为可靠",
        f"MIN_SAMPLES_FOR_PROFILE={values['MIN_SAMPLES_FOR_PROFILE']}",
        "",
    ]
    CONFIG_PATH.write_text("\n".join(lines), encoding="utf-8")


def print_header() -> None:
    print("=" * 56)
    print(" Behavior 模块交互式配置")
    print("=" * 56)
    print(f"配置文件位置: {CONFIG_PATH}")
    print("")


def show_current_config(values: dict[str, str]) -> None:
    """显示当前配置。"""
    print("当前配置:")
    for key in DEFAULTS:
        print(f"- {key} = {values[key]}")
        print(f"  说明: {DESCRIPTIONS[key]}")
    print("")


def prompt_value(prompt: str, current_value: str) -> str:
    """提示用户输入值，空输入则保留当前值。"""
    user_input = input(f"{prompt} [{current_value}]: ").strip()
    return user_input or current_value


def validate(values: dict[str, str]) -> tuple[bool, str]:
    """校验配置。"""
    try:
        hours = int(values["BEHAVIOR_TIME_WINDOW_HOURS"])
        samples = int(values["MIN_SAMPLES_FOR_PROFILE"])
        threshold = float(values["ANOMALY_THRESHOLD"])
    except ValueError:
        return False, "存在无法解析为数字的配置值。"

    if hours <= 0:
        return False, "BEHAVIOR_TIME_WINDOW_HOURS 必须大于 0。"
    if samples <= 0:
        return False, "MIN_SAMPLES_FOR_PROFILE 必须大于 0。"
    if not 0 <= threshold <= 1:
        return False, "ANOMALY_THRESHOLD 必须在 0 到 1 之间。"
    return True, ""


def interactive_edit() -> None:
    """交互式编辑配置。"""
    values = read_config()
    print_header()
    show_current_config(values)
    print("直接回车可保留当前值。")
    print("")

    updated = dict(values)
    updated["BEHAVIOR_TIME_WINDOW_HOURS"] = prompt_value(
        "请输入默认统计窗口（小时）",
        values["BEHAVIOR_TIME_WINDOW_HOURS"],
    )
    updated["ANOMALY_THRESHOLD"] = prompt_value(
        "请输入异常告警阈值（0-1）",
        values["ANOMALY_THRESHOLD"],
    )
    updated["MIN_SAMPLES_FOR_PROFILE"] = prompt_value(
        "请输入最小可靠样本数",
        values["MIN_SAMPLES_FOR_PROFILE"],
    )

    valid, message = validate(updated)
    if not valid:
        print(f"[ERROR] {message}")
        raise SystemExit(1)

    write_config(updated)
    print("")
    print("[OK] 配置已保存")
    show_current_config(updated)
    print("重新运行 behavior 测试或脚本即可读取新配置。")


def reset_to_defaults() -> None:
    """重置为示例默认值。"""
    write_config(dict(DEFAULTS))
    print("[OK] 已重置为默认 behavior 配置")
    print(f"配置文件位置: {CONFIG_PATH}")


def show_help() -> None:
    """显示帮助。"""
    values = read_config()
    print_header()
    show_current_config(values)
    print("用法:")
    print(f"- {Path(__file__).name}           进入交互式配置")
    print(f"- {Path(__file__).name} --show    仅显示当前配置")
    print(f"- {Path(__file__).name} --reset   重置为默认配置")


def main() -> None:
    ensure_config_exists()

    if len(sys.argv) > 1:
        arg = sys.argv[1]
        if arg == "--show":
            show_help()
            return
        if arg == "--reset":
            reset_to_defaults()
            return
        print(f"未知参数: {arg}")
        raise SystemExit(1)

    interactive_edit()


if __name__ == "__main__":
    main()
