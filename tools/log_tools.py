"""
log_tools.py — LangGraph tools for reading log files.
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

NANOCLAW_LOG = str(Path.home() / "nanoclaw/logs/nanoclaw.log")


def _tail_file(path: str, n: int) -> str:
    p = Path(path)
    if not p.exists():
        return f"(日志文件不存在: {path})"
    try:
        result = subprocess.run(["tail", f"-{n}", path], capture_output=True, text=True, timeout=10)
        return result.stdout.strip() or "(空)"
    except Exception as e:
        return f"读取失败: {e}"


@tool
def read_logs(service: str, lines: int = 50, level: str = "all") -> str:
    """
    读取服务日志。

    Args:
        service: 日志来源，必填，可选值：
                 nanoclaw — NanoClaw 日志 (~/nanoclaw/logs/nanoclaw.log)
                 openclaw — OpenClaw 主日志 (~/.openclaw/logs/gateway.log)
                 openclaw_errors — OpenClaw 错误日志
                 tmp — /tmp/openclaw 临时日志
                 supervisor — ai-supervisor 自身日志
                 summary — 所有日志文件大小摘要
        lines: 读取行数，默认50
        level: 日志级别过滤，可选值：all（默认）、error、warn

    Returns:
        日志内容
    """
    service = service.strip().lower()

    if service == "nanoclaw":
        content = _tail_file(NANOCLAW_LOG, lines)
    elif service == "openclaw":
        content = get_openclaw_logs(lines)
    elif service in ("openclaw_errors", "errors"):
        content = get_openclaw_errors(lines)
    elif service == "tmp":
        content = get_tmp_logs(lines)
    elif service == "supervisor":
        content = get_supervisor_log(lines)
    elif service == "summary":
        content = get_log_summary()
    else:
        return f"❌ 未知 service 值：{service}。可选：nanoclaw、openclaw、openclaw_errors、tmp、supervisor、summary"

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
        service: 搜索范围，可选值：nanoclaw、openclaw、errors、tmp、all（默认）

    Returns:
        匹配结果
    """
    if not keyword:
        return "❌ 请提供搜索关键词"

    service = service.strip().lower()

    # nanoclaw 单独处理
    if service == "nanoclaw":
        p = Path(NANOCLAW_LOG)
        if not p.exists():
            return f"(NanoClaw 日志不存在: {NANOCLAW_LOG})"
        try:
            result = subprocess.run(
                ["grep", "-n", "-i", keyword, str(p)],
                capture_output=True, text=True, timeout=10,
            )
            lines = result.stdout.strip().splitlines()[-20:]
            return "\n".join(lines) if lines else f"未找到关键词 '{keyword}'"
        except Exception as e:
            return f"搜索失败: {e}"

    if service not in ("openclaw", "errors", "tmp", "all"):
        service = "all"

    return search_logs(keyword, log_source=service)
