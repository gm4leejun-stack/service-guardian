"""
shell_tools.py — safe shell command execution.
"""
from __future__ import annotations

import re
import subprocess
import logging
from pathlib import Path
from langchain_core.tools import tool

logger = logging.getLogger(__name__)

_BLOCKED = [
    r"rm\s+-rf\s+/",
    r"mkfs",
    r"dd\s+if=/dev",
    r":\(\)\s*\{",   # fork bomb
    r">\s*/dev/sd",
]
MAX_OUTPUT = 3500
TIMEOUT = 60


@tool
def run_shell_command(command: str) -> str:
    """
    执行 Shell 命令（带安全过滤）。适合查找日志、检查进程、诊断系统状态等。

    Args:
        command: Shell 命令
    """
    if not command.strip():
        return "❌ 命令为空"
    for pattern in _BLOCKED:
        if re.search(pattern, command, re.IGNORECASE):
            return f"❌ 命令被安全策略拒绝"
    try:
        r = subprocess.run(command, shell=True, capture_output=True, text=True,
                           timeout=TIMEOUT, cwd=str(Path.home()))
        out = r.stdout.strip()
        err = r.stderr.strip()
        output = out or err or "(无输出)"
        if r.returncode != 0 and err:
            output = f"{out}\n{err}".strip() if out else err
        if len(output) > MAX_OUTPUT:
            output = output[-MAX_OUTPUT:] + "\n...[截断]"
        return output
    except subprocess.TimeoutExpired:
        return f"❌ 命令超时（{TIMEOUT}s）"
    except Exception as e:
        return f"❌ 执行失败: {e}"
