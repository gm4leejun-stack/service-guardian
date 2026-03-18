"""
agent/brain.py — Claude Code subprocess runner (v3 Step 1).

Replaces LangGraph ReAct Agent. Architecture:
    Telegram message → claude --print subprocess → result

Session design (v3):
- Working memory: full task history, MAX_TASK_TURNS=20 safety cap per thread.
  Cleared when user signals task completion (keyword-based detection).
- Long-term memory: agent/memory.json, last 50 records, restart-persistent.
  10 most recent records for the thread injected at task start.
- Watchdog tasks (thread_id starts with "watchdog_") skip all memory entirely.

notify_user: Claude Code calls notify_cli.py via Bash:
    python3 /path/to/notify_cli.py "message" <chat_id>
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import signal
import subprocess
import threading
from collections import defaultdict
from datetime import datetime, timezone, timedelta
from pathlib import Path

import anthropic

from config import settings

logger = logging.getLogger(__name__)

_SUPERVISOR_DIR = str(Path(__file__).parent.parent)
_CLAUDE_BIN = str(Path.home() / ".local/bin/claude")

# ---------------------------------------------------------------------------
# Working memory (task-scoped, in-memory only)
# ---------------------------------------------------------------------------

MAX_TASK_TURNS = 20

# key = thread_id (str(chat_id))
# value = list of (user_msg, assistant_response) pairs
working_memory: dict[str, list[tuple[str, str]]] = defaultdict(list)

# Token usage records per thread
last_usage: dict[str, dict] = {}

# Lock for working_memory and last_usage (accessed from threads and async context)
_memory_lock = threading.Lock()

# ---------------------------------------------------------------------------
# Long-term memory (agent/memory.json, restart-persistent)
# ---------------------------------------------------------------------------

MEMORY_PATH = Path(__file__).parent / "memory.json"
MAX_LONG_TERM = 50


def load_long_term_memory(thread_id: str) -> list[dict]:
    """Return the 10 most recent long-term memory records for this thread."""
    if not MEMORY_PATH.exists():
        return []
    try:
        records = json.loads(MEMORY_PATH.read_text())
    except (json.JSONDecodeError, OSError):
        return []
    matches = [r for r in records if r.get("thread_id") == thread_id]
    return matches[-10:]


def save_long_term_memory(thread_id: str, summary: str, service: str = "") -> None:
    """Append a summary record to long-term memory, keeping at most MAX_LONG_TERM."""
    try:
        records = json.loads(MEMORY_PATH.read_text()) if MEMORY_PATH.exists() else []
    except (json.JSONDecodeError, OSError):
        records = []
    tz = timezone(timedelta(hours=8))
    records.append({
        "time": datetime.now(tz).isoformat(),
        "thread_id": thread_id,
        "service": service,
        "summary": summary,
    })
    records = records[-MAX_LONG_TERM:]
    MEMORY_PATH.write_text(json.dumps(records, ensure_ascii=False, indent=2))


# ---------------------------------------------------------------------------
# Clear-trigger detection
# ---------------------------------------------------------------------------

RESET_EXACT = {"好了", "解决了", "没问题了", "换个话题", "/new"}
RESET_CONTAINS = ["问题解决了", "已经解决了", "好的谢谢", "完成了"]


def should_clear_working_memory(user_msg: str) -> bool:
    """Return True if the user message signals end of current task."""
    msg = user_msg.strip()
    if msg in RESET_EXACT:
        return True
    return any(kw in msg for kw in RESET_CONTAINS)


# ---------------------------------------------------------------------------
# Usage parsing
# ---------------------------------------------------------------------------

def parse_usage_from_stream(output: str) -> dict | None:
    """从 claude --output-format stream-json 输出中提取最后一个 usage 字段。"""
    usage = None
    for line in output.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
            if "usage" in obj:
                usage = obj["usage"]
        except json.JSONDecodeError:
            continue
    return usage


# ---------------------------------------------------------------------------
# Context building helpers
# ---------------------------------------------------------------------------

def _build_history_context(thread_id: str) -> str:
    """Build a text block with long-term + working memory for prompt injection."""
    long_term = load_long_term_memory(thread_id)
    with _memory_lock:
        working = list(working_memory.get(thread_id, []))

    lines = []

    if long_term:
        lines.append("[长期记忆（历史任务摘要）]")
        for rec in long_term:
            ts = rec.get("time", "")[:16]
            summary = rec.get("summary", "")
            lines.append(f"  [{ts}] {summary}")
        lines.append("")

    if working:
        lines.append("[当前任务对话记录]")
        for user_msg, assistant_resp in working:
            lines.append(f"用户: {user_msg[:300]}")
            lines.append(f"助手: {assistant_resp[:600]}")
            lines.append("")

    if not lines:
        return ""
    return "\n".join(lines) + "---\n\n"


# ---------------------------------------------------------------------------
# Haiku summary
# ---------------------------------------------------------------------------

async def generate_summary_with_haiku(thread_id: str) -> str:
    """使用 Haiku 为当前工作记忆生成摘要（1~3句）。"""
    with _memory_lock:
        history = list(working_memory.get(thread_id, []))
    if not history:
        return ""

    lines = []
    for user_msg, assistant_resp in history[-5:]:
        lines.append(f"用户: {user_msg[:200]}")
        lines.append(f"助手: {assistant_resp[:300]}")
    conversation = "\n".join(lines)

    client = anthropic.AsyncAnthropic(
        api_key=settings.ANTHROPIC_API_KEY,
        base_url=settings.ANTHROPIC_BASE_URL,
    )
    try:
        msg = await client.messages.create(
            model=settings.HAIKU_MODEL,
            max_tokens=200,
            messages=[{
                "role": "user",
                "content": f"请用1~3句话总结以下对话的核心内容（中文，简洁）：\n\n{conversation}",
            }],
        )
        return msg.content[0].text.strip()
    except Exception:
        return f"对话共{len(history)}轮"


# ---------------------------------------------------------------------------
# Subprocess runner (sync, called via asyncio.to_thread)
# ---------------------------------------------------------------------------

def _run_subprocess(full_task: str, thread_id: str) -> tuple[str, str, int]:
    """Run claude --print subprocess and return (stdout_data, stderr_data, returncode)."""
    env = dict(os.environ)
    # Only override if explicitly configured; otherwise Claude Code uses its own auth
    if settings.ANTHROPIC_API_KEY:
        env["ANTHROPIC_API_KEY"] = settings.ANTHROPIC_API_KEY
    if settings.ANTHROPIC_BASE_URL:
        env["ANTHROPIC_BASE_URL"] = settings.ANTHROPIC_BASE_URL

    _debug_log = str(Path(_SUPERVISOR_DIR) / "logs" / f"claude_debug_{thread_id}.jsonl")
    cmd = [
        _CLAUDE_BIN, "--print", "--dangerously-skip-permissions",
        "--no-session-persistence",
        "--max-budget-usd", "1.00",
        "--output-format", "stream-json",
        "--verbose",
        "--setting-sources", "",  # disable plugins/hooks; keeps CLAUDE.md, avoids MCP+hook overhead
        "--debug-file", _debug_log,
    ]

    proc = subprocess.Popen(
        cmd,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        cwd=_SUPERVISOR_DIR,
        env=env,
        start_new_session=True,
    )
    try:
        stdout_data, stderr_data = proc.communicate(input=full_task, timeout=600)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
        except ProcessLookupError:
            pass
        proc.communicate()
        raise

    return stdout_data, stderr_data, proc.returncode


def _parse_stream_output(stdout_data: str) -> tuple[str, int]:
    """Parse stream-json output into (text, tool_call_count)."""
    stdout = ""
    tool_call_count = 0
    for line in (stdout_data or "").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
            if obj.get("type") == "assistant":
                for block in obj.get("message", {}).get("content", []):
                    if block.get("type") == "text":
                        stdout += block["text"]
                    elif block.get("type") == "tool_use":
                        tool_call_count += 1
            elif obj.get("type") == "result":
                if not stdout:
                    stdout = obj.get("result", "")
        except Exception:
            pass
    return stdout, tool_call_count


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

async def run_agent(
    task: str,
    chat_id: int | None = None,
    thread_id: str = "default",
) -> tuple[str, dict | None]:
    """Run the agent for a user task.

    Returns:
        (response_text, usage_dict) — usage_dict may be None if not available.
    """
    is_watchdog = thread_id.startswith("watchdog")

    # --- Clear check (only for human threads) ---
    if not is_watchdog and should_clear_working_memory(task):
        summary = await generate_summary_with_haiku(thread_id)
        if summary:
            try:
                save_long_term_memory(thread_id, summary)
            except Exception as e:
                logger.warning("Failed to save long-term memory: %s", e)
        with _memory_lock:
            working_memory.pop(thread_id, None)
            last_usage.pop(thread_id, None)
        logger.info("[brain] Working memory cleared for thread %s", thread_id)
        return ("✅ 上下文已清除，开始新任务", None)

    # --- MAX_TASK_TURNS guard ---
    if not is_watchdog:
        with _memory_lock:
            turns = len(working_memory.get(thread_id, []))
        if turns >= MAX_TASK_TURNS:
            task = task + f"\n\n[注意：当前任务已进行 {turns} 轮，即将达到上限 {MAX_TASK_TURNS} 轮，请尽快完成或总结。]"

    # --- Build context ---
    history_ctx = "" if is_watchdog else _build_history_context(thread_id)

    # --- notify_hint ---
    notify_hint = ""
    if chat_id:
        notify_hint = (
            f"\n\n[进度通知命令: python3 {_SUPERVISOR_DIR}/tools/notify_cli.py '消息内容' {chat_id}]"
            f"\n[规则：进度通知命令只用于执行中的中间步骤通知。最终结果必须且只能通过 stdout 输出，不得再用进度通知命令发送最终汇总，否则用户会收到重复消息。]"
        )

    # --- Dynamic environment context (prepended to task) ---
    import socket
    env_lines = [
        f"主机名: {socket.gethostname()}",
        f"项目目录: {_SUPERVISOR_DIR}",
        f"服务名: com.ai-supervisor",
    ]
    if settings.MACHINE_NAME:
        env_lines.insert(0, f"机器: {settings.MACHINE_NAME}")
    if settings.GITHUB_REPO:
        env_lines.append(f"GitHub: {settings.GITHUB_REPO}")
    env_ctx = "[当前环境]\n" + "\n".join(env_lines) + "\n\n"

    full_task = env_ctx + history_ctx + task + notify_hint

    logger.info("[brain] task (chat=%s, thread=%s): %s", chat_id, thread_id, task[:80])

    # --- Run subprocess in thread (blocking) ---
    _t0 = asyncio.get_event_loop().time()
    try:
        stdout_data, stderr_data, returncode = await asyncio.to_thread(
            _run_subprocess, full_task, thread_id
        )
    except subprocess.TimeoutExpired:
        logger.error("[brain] timeout after 600s for thread %s", thread_id)
        return ("❌ 执行超时（10分钟），请稍后重试", None)
    except Exception as e:
        logger.exception("[brain] unexpected error: %s", e)
        return (f"❌ 执行出错: {e}", None)
    _elapsed = asyncio.get_event_loop().time() - _t0

    stderr = (stderr_data or "").strip()
    stdout, tool_call_count = _parse_stream_output(stdout_data)

    # Parse usage from stream
    usage = parse_usage_from_stream(stdout_data)

    log_fn = logger.warning if _elapsed > 30 else logger.info
    log_fn("[brain] claude rc=%d tool_calls=%d stdout_len=%d elapsed=%.1fs",
           returncode, tool_call_count, len(stdout), _elapsed)

    if returncode != 0:
        err = stderr[:500] if stderr else stdout[:500] or "(no output)"
        logger.error("[brain] claude error rc=%d: %s", returncode, err)
        return (f"❌ 执行出错: {err}", None)

    # --- Update working memory (non-watchdog, on success) ---
    if not is_watchdog and stdout:
        with _memory_lock:
            working_memory[thread_id].append((task, stdout))
        if usage:
            # Measure component sizes for /input breakdown display
            try:
                claude_md_chars = sum(
                    len(p.read_text(encoding="utf-8", errors="ignore"))
                    for p in [
                        Path(_SUPERVISOR_DIR) / "CLAUDE.md",
                        Path.home() / "CLAUDE.md",
                    ]
                    if p.exists()
                )
            except Exception:
                claude_md_chars = 0
            tz = timezone(timedelta(hours=8))
            last_usage[thread_id] = {
                **usage,
                "_time": datetime.now(tz).strftime("%Y-%m-%d %H:%M:%S CST"),
                "_model": settings.CLAUDE_MODEL,
                "_history_chars": len(history_ctx),
                "_task_chars": len(task),
                "_notify_chars": len(notify_hint),
                "_claude_md_chars": claude_md_chars,
            }

    return (stdout or "(empty response)", usage)


# ---------------------------------------------------------------------------
# Sync wrapper for watchdog (runs in a background thread, not async context)
# ---------------------------------------------------------------------------

def run_agent_sync(
    task: str,
    chat_id: int | None = None,
    thread_id: str = "watchdog",
    quiet: bool = False,
) -> str:
    """Synchronous wrapper around run_agent() for use in watchdog threads."""
    effective_chat_id = None if quiet else chat_id
    result_text, _usage = asyncio.run(
        run_agent(task, chat_id=effective_chat_id, thread_id=thread_id)
    )
    return result_text
