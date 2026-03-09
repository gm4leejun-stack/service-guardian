"""
shell_tools.py — LangGraph tool for safe shell command execution.
Wraps workers/shell_worker.py logic as a @tool function.
"""
from __future__ import annotations

import logging
from langchain_core.tools import tool
from workers.shell_worker import run_shell

logger = logging.getLogger(__name__)


@tool
def run_shell_command(command: str) -> str:
    """
    执行 Shell 命令（带安全过滤）。

    Args:
        command: 要执行的 Shell 命令

    Returns:
        命令输出结果
    """
    if not command or not command.strip():
        return "❌ 请提供要执行的命令"

    r = run_shell(command=command)
    if r.get("success"):
        return r.get("output", "(no output)")
    else:
        error = r.get("error", "")
        output = r.get("output", "")
        if error and output:
            return f"❌ 命令失败（exit {r.get('returncode', '?')}）:\n{output}\n{error}"
        return f"❌ 命令失败: {error or output or '未知错误'}"
