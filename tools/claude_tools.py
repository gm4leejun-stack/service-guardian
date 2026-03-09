"""
claude_tools.py — LangGraph tool for running Claude Code CLI tasks.
Wraps workers/claude_runner.py logic as a @tool function.
"""
from __future__ import annotations

import logging
from pathlib import Path
from langchain_core.tools import tool
from workers.claude_runner import run_claude_task

logger = logging.getLogger(__name__)


@tool
def fix_with_claude(task: str, working_dir: str = "") -> str:
    """
    使用 Claude Code CLI 分析和修复代码问题。

    Args:
        task: 任务描述（自然语言）
        working_dir: 工作目录，默认为用户主目录

    Returns:
        Claude Code 的执行结果
    """
    if not task or not task.strip():
        return "❌ 请提供任务描述"

    cwd = working_dir.strip() if working_dir else str(Path.home())

    r = run_claude_task(task, cwd=cwd)
    if r.get("success"):
        return r.get("output", "(no output)")
    else:
        return f"❌ Claude Code 执行失败: {r.get('error', '未知错误')}"
