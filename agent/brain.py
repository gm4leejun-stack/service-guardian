"""
agent/brain.py — Claude Code subprocess runner (v2).

Replaces LangGraph ReAct Agent. Architecture:
    Telegram message → claude --print subprocess → result

Benefits over v1 (LangGraph):
- No recursion limits (no GraphRecursionError, no step-limit self-healing)
- No history corruption (no _trim_state, no orphaned tool_result)
- Claude Code uses Bash natively — no custom tool wrappers needed
- Session persistence via --resume flag (sessions.json per thread_id)
- Simpler codebase: no Tier-0/1/2 self-healing, no SqliteSaver

notify_user: Claude Code calls notify_cli.py via Bash:
    python3 /path/to/notify_cli.py "message" <chat_id>
"""
from __future__ import annotations

import json
import logging
import os
import subprocess
import threading
from pathlib import Path

from config.settings import ANTHROPIC_API_KEY, ANTHROPIC_BASE_URL

logger = logging.getLogger(__name__)

_SUPERVISOR_DIR = str(Path(__file__).parent.parent)
_CLAUDE_BIN = str(Path.home() / ".local/bin/claude")
_SESSIONS_FILE = str(Path(__file__).parent / "sessions.json")
_sessions_lock = threading.Lock()


def _load_sessions() -> dict:
    try:
        p = Path(_SESSIONS_FILE)
        if p.exists():
            return json.loads(p.read_text())
    except Exception:
        pass
    return {}


def _save_sessions(sessions: dict) -> None:
    try:
        Path(_SESSIONS_FILE).write_text(json.dumps(sessions, indent=2))
    except Exception as e:
        logger.warning("[brain] Failed to save sessions: %s", e)


def run_agent(task: str, chat_id: int | None = None, thread_id: str = "default") -> str:
    # Inject chat_id so Claude Code can call notify_cli.py for progress updates
    notify_hint = ""
    if chat_id:
        notify_hint = (
            f"\n\n[进度通知命令: python3 {_SUPERVISOR_DIR}/tools/notify_cli.py '消息内容' {chat_id}]"
        )
    full_task = task + notify_hint

    # Session continuity: resume previous conversation for this thread
    with _sessions_lock:
        sessions = _load_sessions()
        session_id = sessions.get(thread_id)

    env = dict(os.environ)
    env["ANTHROPIC_API_KEY"] = ANTHROPIC_API_KEY
    env["ANTHROPIC_BASE_URL"] = ANTHROPIC_BASE_URL

    cmd = [_CLAUDE_BIN, "--print", "--dangerously-skip-permissions",
           "--output-format", "json"]
    if session_id:
        cmd += ["--resume", session_id]

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

        # Parse JSON output to extract result text and session_id
        if stdout:
            try:
                data = json.loads(stdout)
                # Save session_id for next call
                new_session = data.get("session_id")
                if new_session:
                    with _sessions_lock:
                        sessions = _load_sessions()
                        sessions[thread_id] = new_session
                        _save_sessions(sessions)
                reply = data.get("result") or data.get("content") or ""
                if reply:
                    return str(reply)
            except json.JSONDecodeError:
                # Fallback: raw text output
                return stdout

        if result.returncode != 0:
            err = stderr[:500] if stderr else "(no error output)"
            logger.error("[brain] claude error rc=%d: %s", result.returncode, err)
            # Retry without session if session may be stale
            if session_id and "session" in err.lower():
                logger.info("[brain] Clearing stale session %s for thread %s", session_id, thread_id)
                with _sessions_lock:
                    sessions = _load_sessions()
                    sessions.pop(thread_id, None)
                    _save_sessions(sessions)
            return f"❌ 执行出错: {err}"

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
