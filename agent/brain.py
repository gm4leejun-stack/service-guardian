"""
brain.py — LangGraph ReAct Agent core using Claude Haiku.

This module provides the main Agent that can:
1. Understand natural language requests
2. Use tools to diagnose and fix services
3. Send real-time progress notifications via notify_user tool
4. Maintain conversation memory via SQLite checkpointer
"""
from __future__ import annotations

import asyncio
import logging
import threading
from pathlib import Path
from typing import Any

from langchain_anthropic import ChatAnthropic
from langgraph.prebuilt import create_react_agent

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
from tools.notify_tools import notify_user, set_chat_id

logger = logging.getLogger(__name__)

# All tools available to the Agent
AGENT_TOOLS = [
    check_service,
    restart_service_tool,
    read_logs,
    search_logs_tool,
    run_shell_command,
    fix_with_claude,
    notify_user,
]

# Thread-local lock to prevent concurrent agent runs from the same chat
_run_lock = threading.Lock()

# Lazy-initialized agent and memory
_agent = None
_memory = None
_agent_lock = threading.Lock()


def _get_agent():
    """Lazily initialize and return the agent (thread-safe)."""
    global _agent, _memory
    if _agent is not None:
        return _agent

    with _agent_lock:
        if _agent is not None:
            return _agent

        from langgraph.checkpoint.memory import MemorySaver
        checkpointer = MemorySaver()
        logger.info("[brain] Using in-memory checkpointer")

        llm = ChatAnthropic(
            model=HAIKU_MODEL,
            api_key=ANTHROPIC_API_KEY,
            base_url=ANTHROPIC_BASE_URL,
            temperature=0,
            max_tokens=4096,
        )

        _agent = create_react_agent(
            llm,
            AGENT_TOOLS,
            checkpointer=checkpointer,
            prompt=SYSTEM_PROMPT,
        )
        logger.info("[brain] Agent initialized with model: %s", HAIKU_MODEL)
        return _agent


def run_agent(task: str, chat_id: int | None = None, thread_id: str = "default") -> str:
    """
    Run the Agent with the given task.

    Args:
        task: Natural language task description
        chat_id: Telegram chat ID for notifications
        thread_id: Conversation thread ID for memory continuity

    Returns:
        Agent's final response text
    """
    # Set active chat ID for notify_user tool
    if chat_id is not None:
        set_chat_id(chat_id)

    agent = _get_agent()

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

        # Extract the last assistant message
        messages = result.get("messages", [])
        for msg in reversed(messages):
            role = getattr(msg, "type", None) or getattr(msg, "role", None)
            if role in ("ai", "assistant"):
                content = msg.content
                if isinstance(content, list):
                    # Handle mixed content (text + tool_use blocks)
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
    """
    Run agent from a non-async context (e.g., watchdog thread).
    If quiet=True, notifications are suppressed (quiet hours).
    """
    if quiet:
        # In quiet mode, don't set chat_id so notify_user is a no-op
        return run_agent(task, chat_id=None, thread_id=thread_id)
    return run_agent(task, chat_id=chat_id, thread_id=thread_id)
