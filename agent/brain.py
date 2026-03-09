"""
agent/brain.py — singleton LangGraph ReAct Agent.

Design:
- One agent instance, built once, reused forever
- chat_id passed via config["configurable"]["chat_id"] — no closures, no rebuilds
- Conversation history trimmed to last 20 messages, always starting from a
  HumanMessage to avoid orphaned tool_result blocks (Anthropic 400 error)
- Self-healing:
    * History corruption (400 tool_use_id) → clear thread + retry once (sync)
    * Any code-level error → spawn background thread that calls Claude Code
      to fix the ai-supervisor codebase, then restarts the service
    * Transient errors (network/API/timeout) → no self-heal, just report
    * 30-minute cooldown prevents self-heal loops
- notify_user reads chat_id from RunnableConfig automatically
"""
from __future__ import annotations

import subprocess
import time
import logging
import threading
from pathlib import Path
import sqlite3
from langchain_anthropic import ChatAnthropic
from langchain_core.messages import HumanMessage
from langgraph.prebuilt import create_react_agent
from langgraph.checkpoint.sqlite import SqliteSaver

from config.settings import ANTHROPIC_API_KEY, ANTHROPIC_BASE_URL, HAIKU_MODEL, LANGGRAPH_RECURSION_LIMIT
from agent.prompts import SYSTEM_PROMPT
from tools.service_tools import check_service, restart_service_tool
from tools.log_tools import read_logs, search_logs_tool
from tools.shell_tools import run_shell_command
from tools.claude_tools import fix_with_claude
from tools.notify_tools import notify_user

logger = logging.getLogger(__name__)

_agent = None
_lock  = threading.Lock()

AGENT_TOOLS = [
    check_service,
    restart_service_tool,
    read_logs,
    search_logs_tool,
    run_shell_command,
    fix_with_claude,
    notify_user,
]


def _trim_state(state: dict) -> list:
    """Keep last 20 messages, ensuring no orphaned tool_result blocks.

    Naive tail-slicing can leave orphaned tool_result blocks at the start
    (when the corresponding tool_use was cut off), which causes Anthropic to
    return a 400 error.

    Strategy:
    1. Trim to last 20 messages.
    2. Collect all tool_use_ids present in the trimmed window.
    3. Walk forward, skipping any ToolMessage whose tool_call_id is not in
       the collected set (these are orphaned results from cut-off tool calls).
    4. Additionally, ensure the slice starts from a HumanMessage.
    """
    from langchain_core.messages import ToolMessage, AIMessage

    messages = state.get("messages", [])
    if len(messages) <= 20:
        return messages
    trimmed = messages[-20:]

    # Collect all tool_use ids that are present in this window
    present_ids: set[str] = set()
    for msg in trimmed:
        if isinstance(msg, AIMessage):
            for block in (msg.tool_calls or []):
                if block.get("id"):
                    present_ids.add(block["id"])

    # Remove orphaned ToolMessages (tool_result without matching tool_use)
    cleaned = [
        msg for msg in trimmed
        if not (isinstance(msg, ToolMessage) and msg.tool_call_id not in present_ids)
    ]

    # Find first HumanMessage to avoid starting mid-conversation
    for i, msg in enumerate(cleaned):
        if isinstance(msg, HumanMessage):
            return cleaned[i:]
    return cleaned  # fallback: no HumanMessage found


_DB_PATH = str(Path(__file__).parent / "memory.db")


def get_agent():
    global _agent
    if _agent:
        return _agent
    with _lock:
        if _agent:
            return _agent
        llm = ChatAnthropic(
            model=HAIKU_MODEL,
            api_key=ANTHROPIC_API_KEY,
            base_url=ANTHROPIC_BASE_URL,
            temperature=0,
            max_tokens=4096,
        )
        conn = sqlite3.connect(_DB_PATH, check_same_thread=False)
        checkpointer = SqliteSaver(conn)
        _agent = create_react_agent(
            llm,
            AGENT_TOOLS,
            checkpointer=checkpointer,
            prompt=_trim_state,
        )
        logger.info("[brain] agent ready: %s  (memory: %s)", HAIKU_MODEL, _DB_PATH)
        return _agent


_SUPERVISOR_DIR = str(Path(__file__).parent.parent)
_CLAUDE_BIN = str(Path.home() / ".local/bin/claude")

_self_heal_lock = threading.Lock()
_last_self_heal: float = 0
SELF_HEAL_COOLDOWN = 1800  # 30 minutes — prevents fix loops


def _is_transient_error(err: Exception) -> bool:
    """Network/API errors that Claude Code can't fix by editing source code."""
    s = str(err).lower()
    return any(k in s for k in [
        "timeout", "timed out", "connection", "network",
        "rate limit", "quota", "overloaded",
        "502", "503", "504",
    ])


def _notify(message: str, chat_id: int | None) -> None:
    if not chat_id:
        return
    try:
        from tools.notify_tools import send_sync
        send_sync(message, chat_id)
    except Exception:
        pass


def _run_self_heal(error: Exception, task_context: str, chat_id: int | None) -> None:
    """Background thread: call Claude Code to fix ai-supervisor, then restart."""
    global _last_self_heal

    with _self_heal_lock:
        now = time.time()
        remaining = SELF_HEAL_COOLDOWN - (now - _last_self_heal)
        if remaining > 0:
            logger.info("[brain] Self-heal cooldown active (%ds remaining)", int(remaining))
            return
        _last_self_heal = now

    error_str = str(error)
    logger.warning("[brain] Self-heal triggered: %s", error_str[:200])

    _notify(
        f"🔧 检测到系统错误，正在调用 Claude Code 自动修复...\n"
        f"错误：{error_str[:150]}",
        chat_id,
    )

    heal_task = (
        f"AI Supervisor 系统在执行任务时遇到以下错误，请分析原因并修复代码：\n\n"
        f"错误信息：{error_str}\n\n"
        f"任务上下文：{task_context}\n\n"
        f"项目目录：{_SUPERVISOR_DIR}\n\n"
        f"请：1) 找出导致此错误的源码位置  2) 修复 bug  3) 简要说明改了什么"
    )

    try:
        r = subprocess.run(
            [_CLAUDE_BIN, "--print", "--dangerously-skip-permissions"],
            input=heal_task, capture_output=True, text=True,
            timeout=300, cwd=_SUPERVISOR_DIR,
        )
        fix_result = (r.stdout.strip() or r.stderr.strip() or "(无输出)")[:400]
        success = r.returncode == 0
        logger.info("[brain] Self-heal result (rc=%d): %s", r.returncode, fix_result[:200])

        _notify(
            f"{'✅' if success else '⚠️'} Claude Code 修复{'完成' if success else '尝试结束'}，正在重启服务...\n"
            f"{fix_result}",
            chat_id,
        )
    except subprocess.TimeoutExpired:
        logger.error("[brain] Self-heal timed out")
        _notify("⚠️ Claude Code 修复超时，正在重启服务...", chat_id)
    except Exception as e:
        logger.exception("[brain] Self-heal Claude Code call failed: %s", e)
        _notify(f"⚠️ 自动修复失败（{e}），正在重启服务...", chat_id)

    # Restart service to pick up any code changes
    try:
        subprocess.run(["launchctl", "stop", "com.ai-supervisor"], capture_output=True, timeout=5)
        time.sleep(1)
        subprocess.run(["launchctl", "start", "com.ai-supervisor"], capture_output=True, timeout=5)
        logger.info("[brain] Service restarted after self-heal")
    except Exception as e:
        logger.error("[brain] Service restart after self-heal failed: %s", e)


def _extract_last_ai_message(result: dict) -> str:
    for msg in reversed(result.get("messages", [])):
        role = getattr(msg, "type", None) or getattr(msg, "role", None)
        if role in ("ai", "assistant"):
            content = msg.content
            if isinstance(content, list):
                parts = [c["text"] for c in content
                         if isinstance(c, dict) and c.get("type") == "text"]
                return "\n".join(parts) if parts else ""
            return str(content)
    return ""


def _is_history_corruption(err: Exception) -> bool:
    """True if the error is an orphaned tool_result / tool_use mismatch."""
    s = str(err)
    return "tool_use_id" in s or ("tool_result" in s and "400" in s)


def run_agent(task: str, chat_id: int | None = None, thread_id: str = "default") -> str:
    agent = get_agent()

    def _make_config(tid: str) -> dict:
        return {
            "configurable": {"thread_id": tid, "chat_id": chat_id},
            "recursion_limit": LANGGRAPH_RECURSION_LIMIT,
        }

    logger.info("[brain] task (chat=%s, thread=%s): %s", chat_id, thread_id, task[:80])
    try:
        result = agent.invoke(
            {"messages": [{"role": "user", "content": task}]},
            config=_make_config(str(thread_id)),
        )
        return _extract_last_ai_message(result)

    except Exception as e:
        if _is_history_corruption(e):
            # Tier-1 self-heal: history corruption → retry with fresh thread (sync)
            fresh_tid = f"{thread_id}_fresh_{int(time.time())}"
            logger.warning("[brain] History corruption in thread %s → fresh thread %s",
                           thread_id, fresh_tid)
            _notify("⚠️ 检测到对话历史异常，正在自动修复并重试...", chat_id)
            try:
                result = agent.invoke(
                    {"messages": [{"role": "user", "content": task}]},
                    config=_make_config(fresh_tid),
                )
                return _extract_last_ai_message(result)
            except Exception as e2:
                # Fall through to Tier-2 self-heal
                e = e2

        if not _is_transient_error(e):
            # Tier-2 self-heal: code bug → Claude Code fixes the codebase, then restart
            threading.Thread(
                target=_run_self_heal,
                args=(e, task[:300], chat_id),
                daemon=True,
                name="self-heal",
            ).start()
            return f"❌ 执行出错，已启动 Claude Code 自动修复，稍后服务将重启：{e}"

        logger.exception("[brain] transient error: %s", e)
        return f"❌ Agent 执行错误: {e}"


def run_agent_sync(task: str, chat_id: int | None = None,
                   thread_id: str = "watchdog", quiet: bool = False) -> str:
    return run_agent(task, chat_id=None if quiet else chat_id, thread_id=thread_id)
