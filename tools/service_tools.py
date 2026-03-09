"""
service_tools.py — LangGraph tools for checking and restarting services.
Wraps workers/openclaw_worker.py logic as @tool functions.
"""
from __future__ import annotations

import logging
from langchain_core.tools import tool
from workers.openclaw_worker import get_service_status, restart_service, get_all_status

logger = logging.getLogger(__name__)


@tool
def check_service(service: str = "all") -> str:
    """
    检查服务状态。

    Args:
        service: 服务名称，可选值：openclaw、nanoclaw、all（默认）

    Returns:
        服务状态描述文字
    """
    service = service.strip().lower()
    if service == "all" or not service:
        return get_all_status()

    if service not in ("openclaw", "nanoclaw"):
        return f"❌ 未知服务：{service}。可选：openclaw、nanoclaw、all"

    st = get_service_status(service)
    icon = "✅" if st.get("running") else "❌"
    pid_str = f"PID={st.get('pid')}" if st.get("pid") else "未运行"
    status_str = st.get("status", "?")
    return f"{icon} {service}: {status_str} ({pid_str})"


@tool
def restart_service_tool(service: str) -> str:
    """
    重启指定服务。

    Args:
        service: 服务名称，可选值：openclaw、nanoclaw、all

    Returns:
        重启结果描述
    """
    service = service.strip().lower()
    if service not in ("openclaw", "nanoclaw", "all"):
        return f"❌ 未知服务：{service}。可选：openclaw、nanoclaw、all"

    if service == "all":
        results = []
        for svc in ("openclaw", "nanoclaw"):
            r = restart_service(svc)
            icon = "✅" if r.get("success") else "❌"
            results.append(f"{icon} {svc}: {'重启成功' if r.get('success') else '重启失败'}")
        return "\n".join(results)

    r = restart_service(service)
    if r.get("success"):
        return f"✅ {service} 重启成功，服务正在运行"
    else:
        return f"❌ {service} 重启失败：{r.get('error', '未知错误')}"
