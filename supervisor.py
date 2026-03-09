"""
Supervisor: LangGraph StateGraph with LLM-powered intent routing.
Claude classifies every message into a worker, no more keyword matching.
"""

from __future__ import annotations

import logging
import re
from typing import Any, TypedDict

from langgraph.graph import END, StateGraph

from workers.claude_runner import run_claude_task
from workers.log_worker import get_openclaw_logs, get_openclaw_errors, search_logs, get_log_summary, get_supervisor_log
from workers.openclaw_worker import get_service_status, restart_service, get_all_status
from workers.shell_worker import run_shell

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# State
# ---------------------------------------------------------------------------

class SupervisorState(TypedDict):
    task: str
    next_worker: str
    task_type: str
    params: dict[str, Any]
    result: str
    error: str | None


# ---------------------------------------------------------------------------
# LLM-based intent classifier
# ---------------------------------------------------------------------------

_OPENCLAW_KW = re.compile(
    r"openclaw|nanoclaw|gateway|重启|restart|没回复|冻结|freeze|服务|service", re.I
)
_LOG_KW = re.compile(
    r"\blog\b|logs|日志|错误|error|tail|搜索日志|search.*log", re.I
)
_SHELL_KW = re.compile(
    r"^(shell:|run:|\$\s)|\bls\b|\bps\b|\bkill\b|\bdf\b|\bdu\b|执行命令|run command", re.I
)


def classify_intent(task: str) -> dict[str, Any]:
    """Keyword-based intent classifier (no API needed)."""
    t = task.strip()
    if _OPENCLAW_KW.search(t):
        # Detect restart intent
        action = "restart" if re.search(r"重启|restart|fix|修复", t, re.I) else "status"
        service = "nanoclaw" if "nanoclaw" in t.lower() else "openclaw"
        params: dict[str, Any] = {"action": action, "service": service}
        worker = "openclaw"
    elif _LOG_KW.search(t):
        if re.search(r"错误|error", t, re.I):
            action = "errors"
        elif re.search(r"搜索|search", t, re.I):
            action = "search"
            m = re.search(r"(?:搜索|search)\s+(\S+)", t, re.I)
            params = {"action": action, "keyword": m.group(1) if m else None}
            worker = "log"
            logger.info("classify_intent: worker=%s params=%s", worker, params)
            return {"worker": worker, "params": params}
        else:
            action = "openclaw"
        params = {"action": action, "keyword": None}
        worker = "log"
    elif _SHELL_KW.search(t):
        cmd = re.sub(r"^(shell:|run:|\$\s*)", "", t, flags=re.I).strip()
        params = {"command": cmd}
        worker = "shell"
    else:
        params = {"task": t}
        worker = "claude"
    logger.info("classify_intent: worker=%s params=%s", worker, params)
    return {"worker": worker, "params": params}


# ---------------------------------------------------------------------------
# Nodes
# ---------------------------------------------------------------------------

def supervisor_node(state: SupervisorState) -> SupervisorState:
    """Route to the appropriate worker using LLM intent classification."""
    task = state.get("task", "").strip()
    if not task:
        return {**state, "next_worker": "unknown", "task_type": "unknown", "params": {}}

    # Handle slash commands directly (no LLM needed)
    task_lower = task.lower()

    if task_lower in ("/start", "/help"):
        return {**state, "next_worker": "unknown", "task_type": "unknown", "params": {}}

    if task_lower.startswith("/fix "):
        service = task_lower.split("/fix ", 1)[1].strip()
        return {**state, "next_worker": "openclaw", "task_type": "openclaw",
                "params": {"action": "restart", "service": service}}

    if task_lower in ("/logs", "/logs errors", "/logs supervisor"):
        if "supervisor" in task_lower:
            action = "supervisor"
        elif "errors" in task_lower:
            action = "errors"
        else:
            action = "openclaw"
        return {**state, "next_worker": "log", "task_type": "log",
                "params": {"action": action, "keyword": None}}

    if task_lower.startswith("/run "):
        cmd = task[5:].strip()
        return {**state, "next_worker": "shell", "task_type": "shell",
                "params": {"command": cmd}}

    if task_lower.startswith("/claude "):
        claude_task = task[8:].strip()
        return {**state, "next_worker": "claude", "task_type": "claude",
                "params": {"task": claude_task}}

    # LLM-based routing for all other messages
    intent = classify_intent(task)
    worker = intent["worker"]
    params = intent["params"]

    logger.info("Intent classified: worker=%s params=%s", worker, params)
    return {**state, "next_worker": worker, "task_type": worker, "params": params}


def claude_node(state: SupervisorState) -> SupervisorState:
    params = state.get("params", {})
    task = params.get("task", state.get("task", ""))
    r = run_claude_task(task)
    if r.get("success"):
        return {**state, "result": r["output"], "error": None}
    return {**state, "result": r.get("output", ""), "error": r.get("error", "unknown error")}


def openclaw_node(state: SupervisorState) -> SupervisorState:
    params = state.get("params", {})
    action = params.get("action", "status")
    service = params.get("service", "openclaw")
    try:
        if action == "restart":
            r = restart_service(service)
        else:
            r = get_all_status()
        output = r.get("message", str(r)) if isinstance(r, dict) else str(r)
        return {**state, "result": output, "error": None}
    except Exception as e:
        logger.error("openclaw_node error: %s", e)
        return {**state, "result": "", "error": str(e)}


def log_node(state: SupervisorState) -> SupervisorState:
    params = state.get("params", {})
    action = params.get("action", "openclaw")
    keyword = params.get("keyword")
    try:
        if action == "errors":
            r = get_openclaw_errors()
        elif action == "summary":
            r = get_log_summary()
        elif action == "supervisor":
            r = get_supervisor_log()
        elif action == "search" and keyword:
            r = search_logs(keyword)
        else:
            r = get_openclaw_logs()
        output = r.get("output", str(r)) if isinstance(r, dict) else str(r)
        return {**state, "result": output, "error": None}
    except Exception as e:
        logger.error("log_node error: %s", e)
        return {**state, "result": "", "error": str(e)}


def shell_node(state: SupervisorState) -> SupervisorState:
    params = state.get("params", {})
    command = params.get("command", "")
    if not command:
        return {**state, "result": "❌ No command provided.", "error": None}
    try:
        r = run_shell(command=command)
        if isinstance(r, dict):
            output = r.get("output", r.get("result", ""))
            if not r.get("success", True):
                return {**state, "result": f"❌ {r.get('error', 'Command failed')}\n{output}".strip(), "error": None}
        else:
            output = str(r)
        return {**state, "result": output, "error": None}
    except Exception as e:
        logger.error("shell_node error: %s", e)
        return {**state, "result": "", "error": str(e)}


def unknown_node(state: SupervisorState) -> SupervisorState:
    help_text = (
        "👋 你好！我是 AI Supervisor，支持自然语言指令。\n\n"
        "你可以直接用中文或英文告诉我你要做什么，例如：\n"
        "- 帮我写一个 Python 脚本\n"
        "- openclaw 为什么没回复\n"
        "- 查看最近的错误日志\n"
        "- 执行 ls -la\n\n"
        "也支持快捷命令：\n"
        "- /fix openclaw — 重启 openclaw\n"
        "- /run <命令> — 执行 shell 命令\n"
        "- /claude <任务> — 直接调用 Claude\n"
        "- /logs — 查看日志"
    )
    return {**state, "result": help_text, "error": None}


# ---------------------------------------------------------------------------
# Route function
# ---------------------------------------------------------------------------

def route_to_worker(state: SupervisorState) -> str:
    return state.get("next_worker", "unknown")


# ---------------------------------------------------------------------------
# Build graph
# ---------------------------------------------------------------------------

def build_graph() -> Any:
    graph = StateGraph(SupervisorState)

    graph.add_node("supervisor", supervisor_node)
    graph.add_node("claude", claude_node)
    graph.add_node("openclaw", openclaw_node)
    graph.add_node("log", log_node)
    graph.add_node("shell", shell_node)
    graph.add_node("unknown", unknown_node)

    graph.set_entry_point("supervisor")

    graph.add_conditional_edges(
        "supervisor",
        route_to_worker,
        {
            "claude": "claude",
            "openclaw": "openclaw",
            "log": "log",
            "shell": "shell",
            "unknown": "unknown",
        },
    )

    graph.add_edge("claude", END)
    graph.add_edge("openclaw", END)
    graph.add_edge("log", END)
    graph.add_edge("shell", END)
    graph.add_edge("unknown", END)

    return graph.compile()


app = build_graph()


def run_task(task: str) -> str:
    return process_task(task)


def process_task(task: str) -> str:
    """Main entry point called by telegram_bot."""
    initial_state: SupervisorState = {
        "task": task,
        "next_worker": "",
        "task_type": "",
        "params": {},
        "result": "",
        "error": None,
    }
    final_state = app.invoke(initial_state)
    if final_state.get("error"):
        return f"❌ Error: {final_state['error']}"
    return final_state.get("result", "（无结果）")
