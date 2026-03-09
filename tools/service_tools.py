"""
service_tools.py — check and restart launchctl services.
"""
from __future__ import annotations

import subprocess
import time
import logging
from langchain_core.tools import tool

logger = logging.getLogger(__name__)

SERVICES = {
    "openclaw": "ai.openclaw.gateway",
    "nanoclaw":  "com.nanoclaw",
}

_ALIASES = {
    "nano":    "nanoclaw",
    "claw":    "openclaw",
    "gateway": "openclaw",
}


def _resolve(name: str) -> str:
    n = name.strip().lower()
    return _ALIASES.get(n, n)


def _launchctl(args: list[str]) -> dict:
    try:
        r = subprocess.run(["launchctl"] + args, capture_output=True, text=True, timeout=15)
        return {"success": r.returncode == 0, "output": r.stdout.strip(), "error": r.stderr.strip()}
    except subprocess.TimeoutExpired:
        return {"success": False, "output": "", "error": "launchctl timeout"}
    except Exception as e:
        return {"success": False, "output": "", "error": str(e)}


def _get_status(service_key: str) -> dict:
    label = SERVICES.get(service_key)
    if not label:
        return {"running": False, "pid": None, "status": "unknown"}
    r = _launchctl(["list", label])
    if not r["success"]:
        return {"running": False, "pid": None, "status": "not loaded"}
    pid = None
    for line in r["output"].splitlines():
        if "PID" in line:
            try:
                pid = int(line.split("=")[-1].strip().rstrip(";"))
            except ValueError:
                pass
    return {"running": pid is not None, "pid": pid,
            "status": "running" if pid else "stopped", "raw": r["output"]}


def _restart(service_key: str) -> dict:
    label = SERVICES.get(service_key)
    if not label:
        return {"success": False, "error": f"unknown service: {service_key}"}
    _launchctl(["stop", label])
    time.sleep(2)
    _launchctl(["start", label])
    time.sleep(2)
    st = _get_status(service_key)
    return {"success": st["running"], "service": label, "status": st}


@tool
def check_service(service: str = "all") -> str:
    """
    检查服务运行状态。

    Args:
        service: openclaw（或 gateway/claw）、nanoclaw（或 nano）、all（默认）
    """
    svc = _resolve(service) if service else "all"
    if svc == "all":
        lines = []
        for key in SERVICES:
            st = _get_status(key)
            icon = "✅" if st["running"] else "❌"
            pid = f"PID={st['pid']}" if st["pid"] else "未运行"
            lines.append(f"{icon} {key}: {st['status']} ({pid})")
        return "\n".join(lines)
    if svc not in SERVICES:
        return f"❌ 未知服务：{service}。可选：openclaw、nanoclaw、all"
    st = _get_status(svc)
    icon = "✅" if st["running"] else "❌"
    pid = f"PID={st['pid']}" if st["pid"] else "未运行"
    return f"{icon} {svc}: {st['status']} ({pid})"


@tool
def restart_service_tool(service: str) -> str:
    """
    重启服务（launchctl stop + start）。重启是最后手段，仅用于进程级故障。

    Args:
        service: openclaw（或 gateway/claw）、nanoclaw（或 nano）、all
    """
    svc = _resolve(service)
    if svc not in SERVICES and svc != "all":
        return f"❌ 未知服务：{service}。可选：openclaw、nanoclaw、all"
    targets = list(SERVICES.keys()) if svc == "all" else [svc]
    results = []
    for t in targets:
        r = _restart(t)
        results.append(f"{'✅' if r['success'] else '❌'} {t}: {'重启成功' if r['success'] else '重启失败 ' + r.get('error','')}")
    return "\n".join(results)


# Internal helper for watchdog (no LangChain tool wrapper)
def get_service_status(service_key: str) -> dict:
    return _get_status(service_key)


def restart_service(service_key: str) -> dict:
    return _restart(service_key)
