"""
log_tools.py — LangGraph tools for reading log files.
Wraps workers/log_worker.py logic as @tool functions.
"""
from __future__ import annotations

import logging
from langchain_core.tools import tool
from workers.log_worker import (
    get_openclaw_logs,
    get_openclaw_errors,
    get_tmp_logs,
    get_supervisor_log,
    search_logs,
    get_log_summary,
)

logger = logging.getLogger(__name__)


@tool
def read_logs(service: str = "openclaw", lines: int = 50, level: str = "all") -> str:
    """
    读取服务日志。

    Args:
        service: 日志来源，可选值：openclaw（默认）、errors、tmp、supervisor、summary
        lines: 读取行数，默认50
        level: 日志级别过滤，可选值：all（默认）、error、warn

    Returns:
        日志内容文字
    """
    service = service.strip().lower()

    if service == "errors":
        content = get_openclaw_errors(lines)
    elif service == "tmp":
        content = get_tmp_logs(lines)
    elif service == "supervisor":
        content = get_supervisor_log(lines)
    elif service == "summary":
        content = get_log_summary()
    else:
        content = get_openclaw_logs(lines)

    if level == "error" and content:
        filtered = [l for l in content.splitlines() if "error" in l.lower() or "err" in l.lower()]
        content = "\n".join(filtered) or "(无 error 级别日志)"
    elif level == "warn" and content:
        filtered = [l for l in content.splitlines() if "warn" in l.lower() or "error" in l.lower()]
        content = "\n".join(filtered) or "(无 warn/error 级别日志)"

    return content or "(日志为空)"


@tool
def search_logs_tool(keyword: str, service: str = "all") -> str:
    """
    在日志中搜索关键词。

    Args:
        keyword: 要搜索的关键词
        service: 搜索范围，可选值：openclaw、errors、tmp、all（默认）

    Returns:
        匹配结果
    """
    if not keyword:
        return "❌ 请提供搜索关键词"

    service = service.strip().lower()
    if service not in ("openclaw", "errors", "tmp", "all"):
        service = "all"

    return search_logs(keyword, log_source=service)
