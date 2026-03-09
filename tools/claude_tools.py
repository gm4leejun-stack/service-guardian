"""
claude_tools.py — run Claude Code CLI for code fixes.
"""
from __future__ import annotations

import subprocess
import logging
from pathlib import Path
from langchain_core.tools import tool

logger = logging.getLogger(__name__)

CLAUDE_BIN = str(Path.home() / ".local/bin/claude")
MAX_OUTPUT = 4000


@tool
def fix_with_claude(task: str, working_dir: str = "") -> str:
    """
    使用 Claude Code CLI 分析和修复代码问题。
    仅在确认是代码 Bug 时使用，不要用于服务重启或配置修改。

    Args:
        task: 任务描述（自然语言）
        working_dir: 工作目录，默认主目录
    """
    if not task.strip():
        return "❌ 请提供任务描述"
    if not Path(CLAUDE_BIN).exists():
        return f"❌ Claude CLI 不存在：{CLAUDE_BIN}"

    cwd = working_dir.strip() if working_dir else str(Path.home())
    try:
        r = subprocess.run(
            [CLAUDE_BIN, "--print", "--dangerously-skip-permissions"],
            input=task.strip(), capture_output=True, text=True, timeout=300, cwd=cwd,
        )
        output = r.stdout.strip() or r.stderr.strip() or "(无输出)"
        if len(output) > MAX_OUTPUT:
            output = output[:MAX_OUTPUT] + "\n...[截断]"
        return output if r.returncode == 0 else f"❌ 执行失败:\n{output}"
    except subprocess.TimeoutExpired:
        return "❌ 超时（5分钟）"
    except Exception as e:
        return f"❌ 错误: {e}"
