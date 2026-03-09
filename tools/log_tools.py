"""
log_tools.py — read and search log files.
"""
from __future__ import annotations

import subprocess
import logging
from datetime import datetime, date
from pathlib import Path
from langchain_core.tools import tool

logger = logging.getLogger(__name__)

HOME = Path.home()

_SERVICE_LOGS = {
    "openclaw":        str(HOME / ".openclaw/logs/gateway.log"),
    "openclaw_errors": str(HOME / ".openclaw/logs/gateway.err.log"),
    "nanoclaw":        str(HOME / "nanoclaw/logs/nanoclaw.log"),
}
_TMP_LOG_DIR  = "/tmp/openclaw"
_SUPERVISOR_LOG = str(Path(__file__).parent.parent / "logs" / "supervisor.log")

_ALIASES = {
    "nano":    "nanoclaw",
    "claw":    "openclaw",
    "gateway": "openclaw",
    "errors":  "openclaw_errors",
}

MAX_CHARS = 3500


def _resolve(name: str) -> str:
    n = name.strip().lower()
    return _ALIASES.get(n, n)


def _tail(path: str, n: int) -> str:
    p = Path(path)
    if not p.exists():
        return f"(日志不存在: {path})"
    try:
        r = subprocess.run(["tail", f"-{n}", path], capture_output=True, text=True, timeout=10)
        return r.stdout.strip() or "(空)"
    except Exception as e:
        return f"读取失败: {e}"


def _grep(keyword: str, path: str) -> str:
    p = Path(path)
    if not p.exists():
        return f"(文件不存在: {path})"
    try:
        r = subprocess.run(["grep", "-n", "-i", keyword, path],
                           capture_output=True, text=True, timeout=10)
        lines = r.stdout.strip().splitlines()[-20:]
        return "\n".join(lines) if lines else f"未找到 '{keyword}'"
    except Exception as e:
        return f"搜索失败: {e}"


@tool
def read_logs(service: str, lines: int = 30, level: str = "all") -> str:
    """
    读取服务日志。

    Args:
        service: 服务名（支持缩写）或日志文件绝对路径。
                 已知服务：nanoclaw（nano）、openclaw（claw/gateway）、
                 openclaw_errors（errors）、tmp、supervisor、summary
        lines: 读取行数，默认30
        level: all（默认）、error、warn
    """
    svc = _resolve(service)

    if svc in _SERVICE_LOGS:
        content = _tail(_SERVICE_LOGS[svc], lines)
    elif svc == "tmp":
        tmp = Path(_TMP_LOG_DIR)
        if not tmp.exists():
            return f"(目录不存在: {_TMP_LOG_DIR})"
        files = sorted(tmp.glob("*.log"), reverse=True)
        if not files:
            return "(无日志文件)"
        content = f"[{files[0].name}]\n{_tail(str(files[0]), lines)}"
    elif svc == "supervisor":
        content = _tail(_SUPERVISOR_LOG, lines)
    elif svc == "summary":
        parts = []
        for name, path in _SERVICE_LOGS.items():
            p = Path(path)
            if p.exists():
                size = p.stat().st_size
                mtime = datetime.fromtimestamp(p.stat().st_mtime).strftime("%m-%d %H:%M")
                parts.append(f"  {name}: {size:,}字节，更新于 {mtime}")
            else:
                parts.append(f"  {name}: 不存在")
        return "日志摘要:\n" + "\n".join(parts)
    elif service.startswith("/") or service.startswith("~"):
        content = _tail(str(Path(service).expanduser()), lines)
    else:
        return (f"未识别服务：'{service}'。已知：nanoclaw、openclaw、openclaw_errors、"
                f"tmp、supervisor、summary，或传日志文件绝对路径")

    if level == "error":
        filtered = [l for l in content.splitlines() if "error" in l.lower()]
        content = "\n".join(filtered) or "(无 error 日志)"
    elif level == "warn":
        filtered = [l for l in content.splitlines() if "warn" in l.lower() or "error" in l.lower()]
        content = "\n".join(filtered) or "(无 warn/error 日志)"

    if len(content) > MAX_CHARS:
        content = content[-MAX_CHARS:] + "\n...[截断，仅显示最新部分]"
    return content or "(日志为空)"


@tool
def search_logs_tool(keyword: str, service: str = "all") -> str:
    """
    在日志中搜索关键词。

    Args:
        keyword: 搜索关键词
        service: nanoclaw（nano）、openclaw（claw）、openclaw_errors（errors）、all（默认）
                 或日志文件绝对路径
    """
    if not keyword:
        return "❌ 请提供关键词"

    svc = _resolve(service)

    if svc in _SERVICE_LOGS:
        return _grep(keyword, _SERVICE_LOGS[svc])

    if service.startswith("/") or service.startswith("~"):
        return _grep(keyword, str(Path(service).expanduser()))

    # all: search all known logs
    results = []
    for name, path in _SERVICE_LOGS.items():
        r = _grep(keyword, path)
        if "未找到" not in r and "不存在" not in r:
            results.append(f"[{name}]\n{r}")
    output = "\n\n".join(results) if results else f"所有日志中未找到 '{keyword}'"
    if len(output) > MAX_CHARS:
        output = output[-MAX_CHARS:]
    return output


# Internal helpers for watchdog/triage (no LangChain wrapper)
def tail_log(service_key: str, n: int = 30) -> str:
    path = _SERVICE_LOGS.get(service_key)
    if not path:
        return f"(未知服务: {service_key})"
    return _tail(path, n)
