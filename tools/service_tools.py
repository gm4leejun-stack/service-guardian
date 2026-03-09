"""
service_tools.py — LangGraph tools for checking and restarting services.
"""
from __future__ import annotations

import logging
from langchain_core.tools import tool
from workers.openclaw_worker import get_service_status, restart_service, get_all_status

logger = logging.getLogger(__name__)

_ALIASES = {
    "nano":    "nanoclaw",
    "claw":    "openclaw",
    "gateway": "openclaw",
}

def _resolve(name: str) -> str:
    n = name.strip().lower()
    return _ALIASES.get(n, n)


@tool
def check_service(service: str = "all") -> str:
    """
    检查服务运行状态。

    Args:
        service: 服务名，支持缩写。可选：openclaw（或 gateway/claw）、
                 nanoclaw（或 nano）、all（默认，查所有）

    Returns:
        服务状态描述
    """
    svc = _resolve(service) if service else "all"
    if svc in ("all", ""):
        return get_all_status()
    if svc not in ("openclaw", "nanoclaw"):
        return f"❌ 未知服务：{service}（识别为 '{svc}'）。已知服务：openclaw、nanoclaw"
    st = get_service_status(svc)
    icon = "✅" if st.get("running") else "❌"
    pid_str = f"PID={st.get('pid')}" if st.get("pid") else "未运行"
    return f"{icon} {svc}: {st.get('status', '?')} ({pid_str})"


@tool
def restart_service_tool(service: str) -> str:
    """
    重启指定服务。

    Args:
        service: 服务名，支持缩写。可选：openclaw（或 gateway/claw）、
                 nanoclaw（或 nano）、all（重启所有）

    Returns:
        重启结果
    """
    svc = _resolve(service)
    if svc not in ("openclaw", "nanoclaw", "all"):
        return f"❌ 未知服务：{service}（识别为 '{svc}'）。已知服务：openclaw、nanoclaw"
    if svc == "all":
        results = []
        for s in ("openclaw", "nanoclaw"):
            r = restart_service(s)
            results.append(f"{'✅' if r.get('success') else '❌'} {s}: {'重启成功' if r.get('success') else '重启失败'}")
        return "\n".join(results)
    r = restart_service(svc)
    return f"✅ {svc} 重启成功" if r.get("success") else f"❌ {svc} 重启失败：{r.get('error', '未知错误')}"
