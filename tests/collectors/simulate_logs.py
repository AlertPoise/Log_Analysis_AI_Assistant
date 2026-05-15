#!/usr/bin/env python3
"""
模拟日志生成器 - 仅生成有效日志（包含所有必需字段）
输出格式：JSON，每行一条
输出路径：sample_logs/app.log
"""

import json
import time
import random
from datetime import datetime
from pathlib import Path

# 输出文件路径
LOG_FILE = Path(__file__).parent / "sample_logs" / "app.log"

# 确保目录存在
LOG_FILE.parent.mkdir(exist_ok=True)

# 可选的日志类型和来源
LOG_TYPES = ["vpn", "api", "db", "system"]
SOURCES = [
    "/var/log/vpn.log",
    "/var/log/api.log",
    "/var/log/db.log",
    "/var/log/system.log"
]
MESSAGES = [
    "User 'admin' logged in from 192.168.1.100",
    "Request to /api/v1/users took 45ms",
    "Connection pool exhausted, retrying",
    "Disk usage reached 85%",
    "Firewall rule updated"
]

def generate_valid_log():
    """生成一条完全有效的日志（包含所有必需字段）"""
    return {
        "timestamp": datetime.now().isoformat(),
        "log_type": random.choice(LOG_TYPES),
        "source": random.choice(SOURCES),
        "message": random.choice(MESSAGES)
    }

def main():
    print(f"有效日志生成器启动，输出文件: {LOG_FILE}")
    # 清空旧日志，避免干扰
    if LOG_FILE.exists():
        LOG_FILE.unlink()
        print("已清空旧日志文件")

    count = 0
    try:
        while True:
            log_entry = generate_valid_log()
            with open(LOG_FILE, "a") as f:
                f.write(json.dumps(log_entry) + "\n")
            count += 1
            if count % 10 == 0:
                print(f"已生成 {count} 条有效日志")
            time.sleep(random.uniform(0.5, 2))  # 随机间隔 0.5~2 秒
    except KeyboardInterrupt:
        print(f"\n停止生成，共生成 {count} 条有效日志")

if __name__ == "__main__":
    main()