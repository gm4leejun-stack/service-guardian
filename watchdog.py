"""
watchdog.py — monitors OpenClaw/NanoClaw for freezes and triggers the Agent for auto-rescue.

A service is considered frozen when:
  1. The process is running (launchctl shows PID) BUT
  2. The gateway log has not been updated in WATCHDOG_FREEZE_THRESHOLD seconds

Quiet hours (0:00-8:00): rescues run silently (no Telegram notifications).
"""
from __future__ import annotations

import time
import logging
import sys
from pathlib import Path
from datetime import datetime

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent))

from config.settings import (
    WATCHDOG_CHECK_INTERVAL,
    WATCHDOG_FREEZE_THRESHOLD,
    WATCHDOG_QUIET_HOURS_START,
    WATCHDOG_QUIET_HOURS_END,
    OPENCLAW_LOG,
    ADMIN_CHAT_ID,
)
from workers.openclaw_worker import get_service_status

logger = logging.getLogger(__name__)

SERVICES_TO_WATCH = [
    {
        "key": "openclaw",
        "log": OPENCLAW_LOG,
        "description": "OpenClaw Gateway",
    },
    {
        "key": "nanoclaw",
        "log": str(Path.home() / "nanoclaw/logs/nanoclaw.log"),
        "description": "NanoClaw",
    },
]


def is_quiet_hours() -> bool:
    """Return True if current time is in quiet hours (no Telegram notifications)."""
    h = datetime.now().hour
    start = WATCHDOG_QUIET_HOURS_START
    end = WATCHDOG_QUIET_HOURS_END
    if start < end:
        return start <= h < end
    # wraps midnight (e.g. 22:00-08:00)
    return h >= start or h < end


def _log_age_seconds(log_path: str) -> float | None:
    """Return seconds since log file was last modified. None if file doesn't exist."""
    p = Path(log_path)
    if not p.exists():
        return None
    return time.time() - p.stat().st_mtime


def _trigger_agent_rescue(service_key: str, description: str, log_age: float) -> None:
    """
    Trigger the Agent to perform a full rescue for the given service.
    Uses quiet mode (no Telegram) during quiet hours.
    """
    quiet = is_quiet_hours()
    chat_id = ADMIN_CHAT_ID if not quiet else None

    task = (
        f"系统监控检测到 {description} ({service_key}) 服务疑似冻结！"
        f"日志已 {log_age:.0f} 秒未更新（阈值 {WATCHDOG_FREEZE_THRESHOLD} 秒）。"
        f"请立即执行完整急救流程：检查状态→读取日志→诊断→重启→验证→汇报。"
    )

    logger.warning("[watchdog] Triggering Agent rescue for %s (quiet=%s)", service_key, quiet)

    try:
        from agent.brain import run_agent_sync
        result = run_agent_sync(
            task,
            chat_id=chat_id,
            thread_id=f"watchdog_{service_key}",
            quiet=quiet,
        )
        logger.info("[watchdog] Agent rescue completed for %s: %s", service_key, result[:200])
    except Exception as e:
        logger.exception("[watchdog] Agent rescue failed for %s: %s", service_key, e)
        # Fall back to direct restart if Agent fails
        logger.warning("[watchdog] Falling back to direct restart for %s", service_key)
        try:
            from workers.openclaw_worker import restart_service
            r = restart_service(service_key)
            if r.get("success"):
                logger.info("[watchdog] Fallback restart succeeded for %s", service_key)
                if not quiet and chat_id:
                    from tools.notify_tools import send_notification_sync
                    send_notification_sync(
                        f"⚠️ {description} 检测到冻结，已自动重启（Agent 不可用，使用直接重启）",
                        chat_id=chat_id,
                    )
            else:
                logger.error("[watchdog] Fallback restart also failed for %s", service_key)
        except Exception as e2:
            logger.exception("[watchdog] Fallback restart error: %s", e2)


def check_service_health(service_config: dict) -> dict:
    """
    Check one service's health. Returns status dict.
    Does NOT auto-fix; caller decides whether to trigger rescue.
    """
    key = service_config["key"]
    log_path = service_config["log"]
    desc = service_config["description"]

    status = get_service_status(key)
    running = status.get("running", False)

    if not running:
        logger.warning("[watchdog] %s is NOT running", desc)
        return {
            "service": key,
            "running": False,
            "frozen": False,
            "log_age": None,
            "message": f"{desc} 未运行（可能已被手动停止）",
        }

    age = _log_age_seconds(log_path)

    if age is None:
        logger.info("[watchdog] %s log not found at %s", desc, log_path)
        return {
            "service": key,
            "running": True,
            "frozen": False,
            "log_age": None,
            "message": f"{desc} 运行中，但日志文件不存在",
        }

    frozen = age > WATCHDOG_FREEZE_THRESHOLD

    if frozen:
        logger.warning(
            "[watchdog] %s FROZEN! Log stale for %.0fs (threshold: %ds)",
            desc, age, WATCHDOG_FREEZE_THRESHOLD,
        )
    else:
        logger.debug("[watchdog] %s healthy (log age: %.0fs)", desc, age)

    return {
        "service": key,
        "running": True,
        "frozen": frozen,
        "log_age": age,
        "message": (
            f"{desc} 疑似冻结（日志 {age:.0f}s 未更新）"
            if frozen
            else f"{desc} 健康（日志更新于 {age:.0f}s 前）"
        ),
    }


def run_watchdog_once() -> list[dict]:
    """Run one check cycle. Triggers Agent rescue for frozen services."""
    results = []
    for svc in SERVICES_TO_WATCH:
        try:
            r = check_service_health(svc)
            results.append(r)
            logger.info("[watchdog] %s", r["message"])

            if r.get("frozen"):
                _trigger_agent_rescue(svc["key"], svc["description"], r["log_age"])
        except Exception as e:
            logger.exception("[watchdog] Error checking %s: %s", svc["key"], e)
            results.append({
                "service": svc["key"],
                "running": None,
                "frozen": None,
                "log_age": None,
                "message": f"Watchdog 检查出错: {e}",
            })
    return results


def run_watchdog_loop() -> None:
    """Run the watchdog in an infinite loop."""
    logger.info(
        "[watchdog] Starting loop (interval=%ds, freeze_threshold=%ds, quiet=%s-%s)",
        WATCHDOG_CHECK_INTERVAL,
        WATCHDOG_FREEZE_THRESHOLD,
        f"{WATCHDOG_QUIET_HOURS_START:02d}:00",
        f"{WATCHDOG_QUIET_HOURS_END:02d}:00",
    )
    while True:
        try:
            run_watchdog_once()
        except Exception as e:
            logger.exception("[watchdog] Unexpected error in watchdog loop: %s", e)

        time.sleep(WATCHDOG_CHECK_INTERVAL)


if __name__ == "__main__":
    import argparse

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        handlers=[
            logging.FileHandler(str(Path(__file__).parent / "logs" / "watchdog.log")),
            logging.StreamHandler(),
        ],
    )

    parser = argparse.ArgumentParser(description="AI Supervisor Watchdog")
    parser.add_argument("--once", action="store_true", help="Run one check and exit")
    args = parser.parse_args()

    if args.once:
        results = run_watchdog_once()
        for r in results:
            print(r["message"])
    else:
        run_watchdog_loop()
