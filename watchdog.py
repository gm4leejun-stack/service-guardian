"""
watchdog.py — monitors OpenClaw/NanoClaw for real freezes.

Freeze definition (per service):
  OpenClaw: process running + log stale > threshold + Telegram has pending
            updates (messages queued but not processed)
  NanoClaw: process NOT running (crash/stop) — log staleness alone is not
            a reliable signal since NanoClaw may be idle

Smart Triage: when an anomaly signal is detected, one cheap LLM call
confirms whether it's a real incident before launching the full ReAct
rescue. This avoids false-alarm rescues at near-zero cost.

Cooldown: each service has a 60-minute cooldown after a rescue fires,
preventing alert storms on slow-recovering services.

Quiet hours (0:00–8:00): rescues run silently, no Telegram notifications.
"""
from __future__ import annotations

import time
import json
import logging
import urllib.request
import sys
from pathlib import Path
from datetime import datetime

sys.path.insert(0, str(Path(__file__).parent))

from config.settings import (
    WATCHDOG_CHECK_INTERVAL,
    WATCHDOG_FREEZE_THRESHOLD,
    WATCHDOG_QUIET_HOURS_START,
    WATCHDOG_QUIET_HOURS_END,
    OPENCLAW_LOG,
    ADMIN_CHAT_ID,
    ANTHROPIC_API_KEY,
    ANTHROPIC_BASE_URL,
    HAIKU_MODEL,
)
from tools.service_tools import get_service_status, restart_service

logger = logging.getLogger(__name__)

# OpenClaw's own Telegram bot token (for pending update check)
OPENCLAW_BOT_TOKEN = "8397885859:AAHwmhMbyUu8cRcG_vdIkb-PG7TUGMt21xU"

# Per-service rescue cooldown in seconds (60 minutes)
RESCUE_COOLDOWN = 3600
_last_rescue: dict[str, float] = {}

SERVICES_TO_WATCH = [
    {
        "key": "openclaw",
        "log": OPENCLAW_LOG,
        "description": "OpenClaw Gateway",
        "freeze_check": "telegram_pending",
    },
    {
        "key": "nanoclaw",
        "log": str(Path.home() / "nanoclaw/logs/nanoclaw.log"),
        "description": "NanoClaw",
        "freeze_check": "process_down",
    },
]


def is_quiet_hours() -> bool:
    h = datetime.now().hour
    start, end = WATCHDOG_QUIET_HOURS_START, WATCHDOG_QUIET_HOURS_END
    if start < end:
        return start <= h < end
    return h >= start or h < end


def _in_cooldown(service_key: str) -> bool:
    last = _last_rescue.get(service_key)
    if last is None:
        return False
    elapsed = time.time() - last
    if elapsed < RESCUE_COOLDOWN:
        logger.info("[watchdog] %s in cooldown (%.0f / %ds remaining)", service_key,
                    elapsed, RESCUE_COOLDOWN - elapsed)
        return True
    return False


def _log_age_seconds(log_path: str) -> float | None:
    p = Path(log_path)
    if not p.exists():
        return None
    return time.time() - p.stat().st_mtime


def _get_telegram_pending(bot_token: str) -> int | None:
    try:
        url = f"https://api.telegram.org/bot{bot_token}/getWebhookInfo"
        with urllib.request.urlopen(url, timeout=5) as resp:
            data = json.loads(resp.read())
            return data.get("result", {}).get("pending_update_count", 0)
    except Exception as e:
        logger.debug("[watchdog] Telegram pending check failed: %s", e)
        return None


def check_service_health(service_config: dict) -> dict:
    key = service_config["key"]
    log_path = service_config["log"]
    desc = service_config["description"]
    freeze_check = service_config.get("freeze_check", "process_down")

    status = get_service_status(key)
    running = status.get("running", False)

    # --- NanoClaw: only care if it crashes ---
    if freeze_check == "process_down":
        if not running:
            logger.warning("[watchdog] %s is DOWN", desc)
            return {
                "service": key, "running": False, "frozen": True,
                "log_age": None,
                "message": f"{desc} 进程已停止，需要重启",
            }
        logger.debug("[watchdog] %s running (PID=%s)", desc, status.get("pid"))
        return {
            "service": key, "running": True, "frozen": False,
            "log_age": _log_age_seconds(log_path),
            "message": f"{desc} 运行正常（PID={status.get('pid')}）",
        }

    # --- OpenClaw: frozen = running + log stale + Telegram has pending msgs ---
    if not running:
        logger.warning("[watchdog] %s is NOT running", desc)
        return {
            "service": key, "running": False, "frozen": False,
            "log_age": None,
            "message": f"{desc} 未运行（可能已被手动停止）",
        }

    age = _log_age_seconds(log_path)
    if age is None or age <= WATCHDOG_FREEZE_THRESHOLD:
        msg = (f"{desc} 健康（日志更新于 {age:.0f}s 前）"
               if age is not None else f"{desc} 运行中（日志不存在）")
        logger.debug("[watchdog] %s", msg)
        return {"service": key, "running": True, "frozen": False, "log_age": age, "message": msg}

    # Log is stale — check if Telegram actually has queued messages
    pending = _get_telegram_pending(OPENCLAW_BOT_TOKEN)
    if pending is None:
        logger.info("[watchdog] %s log stale (%.0fs) but Telegram check failed — skipping", desc, age)
        return {
            "service": key, "running": True, "frozen": False, "log_age": age,
            "message": f"{desc} 日志陈旧 {age:.0f}s，无法确认冻结（Telegram API 不可达）",
        }

    if pending == 0:
        logger.info("[watchdog] %s log stale (%.0fs) but no pending msgs — idle", desc, age)
        return {
            "service": key, "running": True, "frozen": False, "log_age": age,
            "message": f"{desc} 空闲（日志 {age:.0f}s 未更新，Telegram 无积压消息）",
        }

    logger.warning("[watchdog] %s FROZEN: log stale %.0fs + %d pending Telegram msgs",
                   desc, age, pending)
    return {
        "service": key, "running": True, "frozen": True, "log_age": age,
        "message": f"{desc} 冻结（日志 {age:.0f}s 未更新，Telegram 积压 {pending} 条消息）",
    }


def _smart_triage(service_key: str, description: str, anomaly_summary: str) -> bool:
    """
    Single LLM call to confirm whether this is a real incident.
    Returns True if it's confirmed real, False if likely false alarm.
    """
    try:
        import anthropic
        client = anthropic.Anthropic(
            api_key=ANTHROPIC_API_KEY,
            base_url=ANTHROPIC_BASE_URL,
        )
        prompt = (
            f"服务监控检测到以下异常信号：{anomaly_summary}\n\n"
            f"服务：{description} ({service_key})\n\n"
            "请判断：这是一个需要立即处理的真实故障，还是可能的误报？\n"
            "只回答 YES（真实故障，需要立即介入）或 NO（可能误报，暂时观察），"
            "后面可以加一句简短理由（不超过20字）。"
        )
        msg = client.messages.create(
            model=HAIKU_MODEL,
            max_tokens=50,
            messages=[{"role": "user", "content": prompt}],
        )
        answer = msg.content[0].text.strip().upper()
        is_real = answer.startswith("YES")
        logger.info("[watchdog] Smart Triage for %s: %s → confirmed=%s", service_key, answer, is_real)
        return is_real
    except Exception as e:
        logger.error("[watchdog] Smart Triage failed for %s: %s — defaulting to YES", service_key, e)
        return True  # fail-safe: treat as real


def _trigger_agent_rescue(service_key: str, description: str, health: dict) -> None:
    if _in_cooldown(service_key):
        return

    anomaly_summary = health.get("message", "服务异常")

    # Smart Triage: single LLM call to confirm
    if not _smart_triage(service_key, description, anomaly_summary):
        logger.info("[watchdog] Smart Triage says false alarm for %s — skipping rescue", service_key)
        return

    # Mark cooldown before rescue starts (prevents overlapping rescues)
    _last_rescue[service_key] = time.time()

    quiet = is_quiet_hours()
    chat_id = ADMIN_CHAT_ID if not quiet else None

    task = (
        f"系统监控确认 {description} ({service_key}) 服务异常：{anomaly_summary}。"
        f"请执行急救流程：检查状态→读取日志→诊断→修复/重启→验证→汇报。"
    )

    logger.warning("[watchdog] Triggering Agent rescue for %s (quiet=%s)", service_key, quiet)
    try:
        from agent.brain import run_agent_sync
        result_text = run_agent_sync(task, chat_id=chat_id,
                                     thread_id=f"watchdog_{service_key}", quiet=quiet)
        logger.info("[watchdog] Rescue completed for %s: %s", service_key, result_text[:200])
    except Exception as e:
        logger.exception("[watchdog] Agent rescue failed for %s: %s", service_key, e)
        # Fallback: direct restart
        try:
            r = restart_service(service_key)
            ok = r.get("success", False)
            logger.info("[watchdog] Fallback restart for %s: %s", service_key, "OK" if ok else "FAILED")
            if not quiet and chat_id and ok:
                from tools.notify_tools import send_sync
                send_sync(f"⚠️ {description} 异常，已直接重启（Agent 不可用）", chat_id)
        except Exception as e2:
            logger.exception("[watchdog] Fallback restart error: %s", e2)


def run_watchdog_once() -> list[dict]:
    results = []
    for svc in SERVICES_TO_WATCH:
        try:
            r = check_service_health(svc)
            results.append(r)
            logger.info("[watchdog] %s", r["message"])
            if r.get("frozen"):
                _trigger_agent_rescue(svc["key"], svc["description"], r)
        except Exception as e:
            logger.exception("[watchdog] Error checking %s: %s", svc["key"], e)
            results.append({
                "service": svc["key"], "running": None, "frozen": None,
                "log_age": None, "message": f"检查出错: {e}",
            })
    return results


def run_watchdog_loop() -> None:
    logger.info(
        "[watchdog] Starting loop (interval=%ds, freeze_threshold=%ds, cooldown=%ds, quiet=%s-%s)",
        WATCHDOG_CHECK_INTERVAL, WATCHDOG_FREEZE_THRESHOLD, RESCUE_COOLDOWN,
        f"{WATCHDOG_QUIET_HOURS_START:02d}:00", f"{WATCHDOG_QUIET_HOURS_END:02d}:00",
    )
    while True:
        try:
            run_watchdog_once()
        except Exception as e:
            logger.exception("[watchdog] Unexpected error: %s", e)
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
    parser = argparse.ArgumentParser()
    parser.add_argument("--once", action="store_true")
    args = parser.parse_args()
    if args.once:
        for r in run_watchdog_once():
            print(r["message"])
    else:
        run_watchdog_loop()
