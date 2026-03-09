"""
openclaw_worker.py — monitors and controls OpenClaw / NanoClaw via launchctl.
"""
import subprocess
import logging
import time
from pathlib import Path
from config.settings import (
    OPENCLAW_SERVICE,
    NANOCLAW_SERVICE,
    OPENCLAW_LOG,
    OPENCLAW_ERR_LOG,
)

logger = logging.getLogger(__name__)

SERVICES = {
    "openclaw": OPENCLAW_SERVICE,
    "nanoclaw": NANOCLAW_SERVICE,
}


def _launchctl(args: list[str]) -> dict:
    """Run a launchctl command, return dict with success/output/error."""
    cmd = ["launchctl"] + args
    try:
        result = subprocess.run(
            cmd, capture_output=True, text=True, timeout=15
        )
        return {
            "success": result.returncode == 0,
            "output": result.stdout.strip(),
            "error": result.stderr.strip(),
        }
    except subprocess.TimeoutExpired:
        return {"success": False, "output": "", "error": "launchctl timeout"}
    except Exception as e:
        return {"success": False, "output": "", "error": str(e)}


def get_service_status(service_key: str = "openclaw") -> dict:
    """
    Return status dict for the given service key (openclaw|nanoclaw).
    Parses `launchctl list <service>` output.
    """
    service = SERVICES.get(service_key)
    if not service:
        return {"success": False, "output": "", "error": f"Unknown service: {service_key}"}

    result = _launchctl(["list", service])
    if not result["success"]:
        # Service may not be loaded
        return {
            "success": True,
            "running": False,
            "pid": None,
            "status": "not loaded",
            "raw": result["error"],
        }

    raw = result["output"]
    pid = None
    last_exit = None

    for line in raw.splitlines():
        line = line.strip()
        if '"PID"' in line or "PID" in line:
            try:
                pid = int(line.split("=")[-1].strip().rstrip(";"))
            except ValueError:
                pass
        if "LastExitStatus" in line:
            try:
                last_exit = int(line.split("=")[-1].strip().rstrip(";"))
            except ValueError:
                pass

    running = pid is not None
    return {
        "success": True,
        "running": running,
        "pid": pid,
        "last_exit_status": last_exit,
        "status": "running" if running else "stopped",
        "raw": raw,
    }


def restart_service(service_key: str = "openclaw") -> dict:
    """Stop then start a service. Returns final status."""
    service = SERVICES.get(service_key)
    if not service:
        return {"success": False, "error": f"Unknown service: {service_key}"}

    logger.info("Restarting service: %s (%s)", service_key, service)

    stop = _launchctl(["stop", service])
    logger.debug("Stop result: %s", stop)
    time.sleep(2)

    start = _launchctl(["start", service])
    logger.debug("Start result: %s", start)
    time.sleep(2)

    status = get_service_status(service_key)
    return {
        "success": status.get("running", False),
        "service": service,
        "status": status,
        "stop_result": stop,
        "start_result": start,
    }


def get_all_status() -> str:
    """Return a formatted status string for all services."""
    lines = []
    for key in SERVICES:
        st = get_service_status(key)
        icon = "✅" if st.get("running") else "❌"
        pid_str = f"PID={st.get('pid')}" if st.get("pid") else "not running"
        lines.append(f"{icon} {key}: {st.get('status','?')} ({pid_str})")
    return "\n".join(lines)


def tail_openclaw_log(lines: int = 30) -> str:
    """Return last N lines of openclaw gateway log."""
    log_path = Path(OPENCLAW_LOG)
    if not log_path.exists():
        return f"Log not found: {OPENCLAW_LOG}"
    try:
        result = subprocess.run(
            ["tail", f"-{lines}", str(log_path)],
            capture_output=True,
            text=True,
            timeout=10,
        )
        return result.stdout.strip() or "(empty)"
    except Exception as e:
        return f"Error reading log: {e}"
