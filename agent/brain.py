"""
brain.py — LangGraph ReAct Agent core using Claude Haiku.

Each run_agent call builds a fresh tool list with notify_user captured
to the correct chat_id, so concurrent sessions never interfere.
"""
from __future__ import annotations

import logging
import threading
from typing import Any

from langchain_anthropic import ChatAnthropic
from langgraph.prebuilt import create_react_agent
from langgraph.checkpoint.memory import MemorySaver

from config.settings import (
    ANTHROPIC_API_KEY,
    ANTHROPIC_BASE_URL,
    HAIKU_MODEL,
    LANGGRAPH_RECURSION_LIMIT,
)
from agent.prompts import SYSTEM_PROMPT
from tools.service_tools import check_service, restart_service_tool
from tools.log_tools import read_logs, search_logs_tool
from tools.shell_tools import run_shell_command
from tools.claude_tools import fix_with_claude
from tools.notify_tools import make_notify_tool

logger = logging.getLogger(__name__)

_llm_lock = threading.Lock()
_llm = None
_checkpointer = None


def _get_llm():
    global _llm, _checkpointer
    if _llm is not None:
        return _llm, _checkpointer
    with _llm_lock:
        if _llm is not None:
            return _llm, _checkpointer
        _llm = ChatAnthropic(
            model=HAIKU_MODEL,
            api_key=ANTHROPIC_API_KEY,
            base_url=ANTHROPIC_BASE_URL,
            temperature=0,
            max_tokens=4096,
        )
        _checkpointer = MemorySaver()
        logger.info("[brain] LLM initialized: %s", HAIKU_MODEL)
        return _llm, _checkpointer


def _build_agent(chat_id: int | None):
    """Build a fresh agent with notify_user bound to this chat_id."""
    llm, checkpointer = _get_llm()
    tools = [
        check_service,
        restart_service_tool,
        read_logs,
        search_logs_tool,
        run_shell_command,
        fix_with_claude,
        make_notify_tool(chat_id),   # chat_id captured in closure
    ]
    return create_react_agent(
        llm,
        tools,
        checkpointer=checkpointer,
        prompt=SYSTEM_PROMPT,
    )


def run_agent(task: str, chat_id: int | None = None, thread_id: str = "default") -> str:
    """
    Run the Agent with the given task.

    Args:
        task: Natural language task description
        chat_id: Telegram chat ID for real-time notifications
        thread_id: Conversation thread ID for memory continuity
    """
    agent = _build_agent(chat_id)
    config = {
        "configurable": {"thread_id": str(thread_id)},
        "recursion_limit": LANGGRAPH_RECURSION_LIMIT,
    }

    try:
        logger.info("[brain] Running agent task (chat_id=%s): %s", chat_id, task[:80])
        result = agent.invoke(
            {"messages": [{"role": "user", "content": task}]},
            config=config,
        )

        messages = result.get("messages", [])
        for msg in reversed(messages):
            role = getattr(msg, "type", None) or getattr(msg, "role", None)
            if role in ("ai", "assistant"):
                content = msg.content
                if isinstance(content, list):
                    text_parts = [
                        c["text"] for c in content
                        if isinstance(c, dict) and c.get("type") == "text"
                    ]
                    return "\n".join(text_parts) if text_parts else "(no text response)"
                return str(content)

        return "（Agent 未返回结果）"

    except Exception as e:
        logger.exception("[brain] Agent error: %s", e)
        return f"❌ Agent 执行错误: {e}"


def run_agent_sync(task: str, chat_id: int | None = None,
                   thread_id: str = "watchdog", quiet: bool = False) -> str:
    """Run agent from a non-async context (e.g., watchdog thread)."""
    effective_chat_id = None if quiet else chat_id
    return run_agent(task, chat_id=effective_chat_id, thread_id=thread_id)
