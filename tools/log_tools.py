"""
log_tools.py — LangGraph tools for reading log files.

Design: tools handle execution, LLM handles intent mapping.
- Known services are resolved by alias (flexible input)
- Unknown services: pass log_path directly, or LLM uses run_shell_command
"""
from __future__ import annotations

import logging
import subprocess
from pathlib import Path
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

# Known service → log path mapping
_SERVICE_LOGS = {
    "openclaw": str(Path.home() / ".openclaw/logs/gateway.log"),
    "nanoclaw":  str(Path.home() / "nanoclaw/logs/nanoclaw.log"),
}

# Alias normalization: accepts abbreviations / mixed case
_ALIASES = {
    "nano":      "nanoclaw",
    "claw":      "openclaw",
    "gateway":   "openclaw",
    "openclaw_errors": "openclaw_errors",
    "errors":    "openclaw_errors",
    "tmp":       "tmp",
    "supervisor": "supervisor",
    "summary":   "summary",
}


def _resolve_service(name: str) -> str:
    """Normalize service name. Tries alias table, then exact match."""
    n = name.strip().lower()
    return _ALIASES.get(n, n)


def _tail(path: str, n: int) -> str:
    p = Path(path)
    if not p.exists():
        return f"(日志文件不存在: {path})"
    try:
        r = subprocess.run(["tail", f"-{n}", path], capture_output=True, text=True, timeout=10)
        return r.stdout.strip() or "(空)"
    except Exception as e:
        return f"读取失败: {e}"


@tool
def read_logs(service: str, lines: int = 50, level: str = "all") -> str:
    """
    读取服务日志。

    Args:
        service: 服务名或日志文件绝对路径。
                 已知服务（支持缩写）：nanoclaw（或 nano）、openclaw（或 gateway/claw）、
                 openclaw_errors（或 errors）、tmp、supervisor、summary。
                 未知服务：直接传日志文件的绝对路径，例如 /var/log/myapp/app.log
        lines: 读取行数，默认50
        level: 日志级别过滤：all（默认）、error、warn

    Returns:
        日志内容
    """
    svc = _resolve_service(service)

    # Known named services
    if svc == "nanoclaw":
        content = _tail(_SERVICE_LOGS["nanoclaw"], lines)
    elif svc == "openclaw":
        content = get_openclaw_logs(lines)
    elif svc == "openclaw_errors":
        content = get_openclaw_errors(lines)
    elif svc == "tmp":
        content = get_tmp_logs(lines)
    elif svc == "supervisor":
        content = get_supervisor_log(lines)
    elif svc == "summary":
        content = get_log_summary()
    elif service.startswith("/") or service.startswith("~"):
        # Direct file path
        content = _tail(str(Path(service).expanduser()), lines)
    else:
        return (
            f"未识别的服务名：'{service}'。\n"
            f"已知服务：nanoclaw、openclaw、openclaw_errors、tmp、supervisor、summary。\n"
            f"也可传日志文件绝对路径，或用 run_shell_command 自行查找。"
        )

    if level == "error":
        filtered = [l for l in content.splitlines() if "error" in l.lower() or "err" in l.lower()]
        content = "\n".join(filtered) or "(无 error 级别日志)"
    elif level == "warn":
        filtered = [l for l in content.splitlines() if "warn" in l.lower() or "error" in l.lower()]
        content = "\n".join(filtered) or "(无 warn/error 级别日志)"

    return content or "(日志为空)"


@tool
def search_logs_tool(keyword: str, service: str = "all") -> str:
    """
    在日志中搜索关键词。

    Args:
        keyword: 搜索关键词
        service: 搜索范围。已知服务（支持缩写）：nanoclaw（或 nano）、openclaw、
                 errors、tmp、all（默认搜所有已知日志）。
                 也可传日志文件绝对路径。

    Returns:
        匹配结果
    """
    if not keyword:
        return "❌ 请提供搜索关键词"

    svc = _resolve_service(service)

    # NanoClaw or direct path
    if svc == "nanoclaw" or service.startswith("/") or service.startswith("~"):
        path = _SERVICE_LOGS["nanoclaw"] if svc == "nanoclaw" else str(Path(service).expanduser())
        p = Path(path)
        if not p.exists():
            return f"(日志不存在: {path})"
        try:
            r = subprocess.run(["grep", "-n", "-i", keyword, str(p)],
                               capture_output=True, text=True, timeout=10)
            lines = r.stdout.strip().splitlines()[-20:]
            return "\n".join(lines) if lines else f"未找到关键词 '{keyword}'"
        except Exception as e:
            return f"搜索失败: {e}"

    # openclaw / errors / tmp / all
    if svc not in ("openclaw", "openclaw_errors", "errors", "tmp", "all"):
        svc = "all"
    return search_logs(keyword, log_source=svc)
