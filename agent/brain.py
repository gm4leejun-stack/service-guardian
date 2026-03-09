"""
agent/brain.py — singleton LangGraph ReAct Agent.

Design:
- One agent instance, built once, reused forever
- chat_id passed via config["configurable"]["chat_id"] — no closures, no rebuilds
- Conversation history trimmed to last 20 messages to control token cost
- notify_user reads chat_id from RunnableConfig automatically
"""
from __future__ import annotations

import logging
import threading
from langchain_anthropic import ChatAnthropic
from langchain_core.messages import trim_messages
from langgraph.prebuilt import create_react_agent
from langgraph.checkpoint.memory import MemorySaver

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
    """Keep system prompt + last 20 messages to bound token cost."""
    messages = state.get("messages", [])
    if len(messages) <= 20:
        return messages
    return messages[-20:]


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
        _agent = create_react_agent(
            llm,
            AGENT_TOOLS,
            checkpointer=MemorySaver(),
            prompt=_trim_state,
        )
        logger.info("[brain] agent ready: %s", HAIKU_MODEL)
        return _agent


def run_agent(task: str, chat_id: int | None = None, thread_id: str = "default") -> str:
    agent = get_agent()
    config = {
        "configurable": {
            "thread_id": str(thread_id),
            "chat_id": chat_id,
        },
        "recursion_limit": LANGGRAPH_RECURSION_LIMIT,
    }
    try:
        logger.info("[brain] task (chat=%s, thread=%s): %s", chat_id, thread_id, task[:80])
        result = agent.invoke(
            {"messages": [{"role": "user", "content": task}]},
            config=config,
        )
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
    except Exception as e:
        logger.exception("[brain] error: %s", e)
        return f"❌ Agent 执行错误: {e}"


def run_agent_sync(task: str, chat_id: int | None = None,
                   thread_id: str = "watchdog", quiet: bool = False) -> str:
    return run_agent(task, chat_id=None if quiet else chat_id, thread_id=thread_id)
