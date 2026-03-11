"""
agent/brain.py — Claude Code subprocess runner (v2).

Replaces LangGraph ReAct Agent. Architecture:
    Telegram message → claude --print subprocess → result

Session design:
- No --resume: avoids growing Claude Code session history (tools + results)
  which causes API slowdown after long usage.
- Lightweight history: last HISTORY_TURNS (user_msg, assistant_response) pairs
  stored in memory, injected as plain text context. Gives conversational
  continuity without accumulating intermediate tool-call overhead.
- History is per thread_id, in-memory only (cleared on service restart).
  Watchdog tasks (thread_id starts with "watchdog_") skip history entirely.

notify_user: Claude Code calls notify_cli.py via Bash:
    python3 /path/to/notify_cli.py "message" <chat_id>
"""
from __future__ import annotations

import logging
import os
import subprocess
import threading
from collections import deque
from pathlib import Path

from config.settings import ANTHROPIC_API_KEY, ANTHROPIC_BASE_URL

logger = logging.getLogger(__name__)

_SUPERVISOR_DIR = str(Path(__file__).parent.parent)
_CLAUDE_BIN = str(Path.home() / ".local/bin/claude")

# Conversational history: keep last N user/response pairs per thread
HISTORY_TURNS = 5
_history: dict[str, deque] = {}   # thread_id → deque of (user_msg, assistant_resp)
_history_lock = threading.Lock()


def _get_history_context(thread_id: str) -> str:
    with _history_lock:
        turns = list(_history.get(thread_id, []))
    if not turns:
        return ""
    lines = ["[最近对话记录（供参考）]"]
    for user_msg, assistant_resp in turns:
        lines.append(f"用户: {user_msg[:300]}")
        lines.append(f"助手: {assistant_resp[:600]}")
        lines.append("")
    return "\n".join(lines) + "\n---\n\n"


def _save_history(thread_id: str, user_msg: str, assistant_resp: str) -> None:
    with _history_lock:
        if thread_id not in _history:
            _history[thread_id] = deque(maxlen=HISTORY_TURNS)
        _history[thread_id].append((user_msg, assistant_resp))


def run_agent(task: str, chat_id: int | None = None, thread_id: str = "default") -> str:
    is_watchdog = thread_id.startswith("watchdog")

    # Inject conversational history (skip for watchdog autonomous tasks)
    history_ctx = "" if is_watchdog else _get_history_context(thread_id)

    # Inject chat_id so Claude Code can call notify_cli.py for progress updates
    notify_hint = ""
    if chat_id:
        notify_hint = (
            f"\n\n[进度通知命令: python3 {_SUPERVISOR_DIR}/tools/notify_cli.py '消息内容' {chat_id}]"
            f"\n[规则：进度通知命令只用于执行中的中间步骤通知。最终结果必须且只能通过 stdout 输出，不得再用进度通知命令发送最终汇总，否则用户会收到重复消息。]"
        )

    full_task = history_ctx + task + notify_hint

    env = dict(os.environ)
    env["ANTHROPIC_API_KEY"] = ANTHROPIC_API_KEY
    env["ANTHROPIC_BASE_URL"] = ANTHROPIC_BASE_URL

    cmd = [
        _CLAUDE_BIN, "--print", "--dangerously-skip-permissions",
        "--no-session-persistence",   # skip disk session IO
        "--max-budget-usd", "0.30",   # cap ~30 tool calls, returns result instead of hanging
    ]

    logger.info("[brain] task (chat=%s, thread=%s): %s", chat_id, thread_id, task[:80])
    try:
        result = subprocess.run(
            cmd,
            input=full_task,
            capture_output=True,
            text=True,
            timeout=600,
            cwd=_SUPERVISOR_DIR,
            env=env,
        )

        stdout = (result.stdout or "").strip()
        stderr = (result.stderr or "").strip()

        logger.info("[brain] claude rc=%d stdout_len=%d", result.returncode, len(stdout))

        if result.returncode != 0:
            err = stderr[:500] if stderr else stdout[:500] or "(no output)"
            logger.error("[brain] claude error rc=%d: %s", result.returncode, err)
            return f"❌ 执行出错: {err}"

        # Save to history on success
        if not is_watchdog and stdout:
            _save_history(thread_id, task, stdout)

        return stdout or "(empty response)"

    except subprocess.TimeoutExpired:
        logger.error("[brain] timeout after 600s for thread %s", thread_id)
        return "❌ 执行超时（10分钟），请稍后重试"
    except Exception as e:
        logger.exception("[brain] unexpected error: %s", e)
        return f"❌ 执行出错: {e}"


def run_agent_sync(task: str, chat_id: int | None = None,
                   thread_id: str = "watchdog", quiet: bool = False) -> str:
    return run_agent(task, chat_id=None if quiet else chat_id, thread_id=thread_id)
