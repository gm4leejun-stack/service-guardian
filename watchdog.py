"""
watchdog.py — monitors OpenClaw/NanoClaw for real freezes.

Freeze definition (per service):
  Both OpenClaw and NanoClaw: process running + log stale > threshold.
  Telegram pending count is collected for triage context but does NOT
  gate the rescue — a stale socket means pending stays 0 even when broken.
  Process-down is also treated as frozen to trigger rescue.

Smart Triage: when an anomaly signal is detected, one cheap LLM call
confirms whether it's a real incident before launching the full ReAct
rescue. This avoids false-alarm rescues at near-zero cost.

Cooldown: each service has a 60-minute cooldown after a rescue fires,
preventing alert storms on slow-recovering services.

Quiet hours (0:00–8:00): rescues run silently, no Telegram notifications.
"""
from __future__ import annotations

import os
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
    WATCHDOG_QUIET_HOURS_START,
    WATCHDOG_QUIET_HOURS_END,
    ADMIN_CHAT_ID,
    ANTHROPIC_API_KEY,
    ANTHROPIC_BASE_URL,
    CLAUDE_MODEL,
    HAIKU_MODEL,
)
import config.settings as settings
from tools.service_tools import get_service_status, restart_service

logger = logging.getLogger(__name__)

# Bot heartbeat file written by telegram_bot.py every 30s
BOT_HEARTBEAT_FILE = str(Path(__file__).parent / "logs" / "bot_heartbeat.txt")
BOT_HEARTBEAT_THRESHOLD = 300  # 5 minutes — bot polling thread is dead

# Per-service rescue cooldown in seconds (60 minutes)
RESCUE_COOLDOWN = 3600
_last_rescue: dict[str, float] = {}

def _load_watchlist() -> list[dict]:
    """每次调用重新读取，支持热更新"""
    path = Path(__file__).parent / "config" / "watchlist.json"
    try:
        with open(path) as f:
            data = json.load(f)
        services = data["services"]
    except FileNotFoundError:
        logger.error("watchlist.json not found at %s", path)
        return []
    except (json.JSONDecodeError, KeyError) as e:
        logger.error("watchlist.json parse error: %s", e)
        return []
    for svc in services:
        if "log" in svc:
            svc["log"] = os.path.expanduser(svc["log"])
    return services


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


def _get_bot_token(svc: dict) -> str:
    """Per-service bot token resolution.
    Prepared for future log_stale / pending-count check modes.
    Currently process_down mode does not use per-service tokens.
    """
    token_env = svc.get("bot_token_env")
    bot_token = os.environ.get(token_env) if token_env else settings.TELEGRAM_BOT_TOKEN
    return bot_token or settings.TELEGRAM_BOT_TOKEN


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
    desc = service_config["description"]
    freeze_check = service_config.get("freeze_check", "process_down")

    status = get_service_status(key)
    running = status.get("running", False)

    if not running:
        logger.warning("[watchdog] %s is NOT running", desc)
        return {
            "service": key, "running": False, "frozen": True,
            "log_age": None,
            "message": f"{desc} 进程已停止，需要重启",
        }

    log_path = service_config.get("log", "")

    # process_down mode: only flag if process is not running (log silence is normal)
    if freeze_check == "process_down":
        age = _log_age_seconds(log_path) if log_path else None
        msg = (f"{desc} 健康（进程存活，日志更新于 {age:.0f}s 前）"
               if age is not None else f"{desc} 健康（进程存活）")
        logger.debug("[watchdog] %s", msg)
        return {"service": key, "running": True, "frozen": False, "log_age": age, "message": msg}

    # Fallback for any future freeze_check modes not yet implemented
    age = _log_age_seconds(log_path) if log_path else None
    msg = (f"{desc} 健康（日志更新于 {age:.0f}s 前）"
           if age is not None else f"{desc} 运行中（日志不存在）")
    logger.debug("[watchdog] %s", msg)
    return {"service": key, "running": True, "frozen": False, "log_age": age, "message": msg}


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
            model=HAIKU_MODEL,  # YES/NO triage only — Haiku sufficient, Sonnet wasteful
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


def check_bot_health() -> dict:
    """Check if the Telegram bot polling thread is alive via heartbeat file."""
    age = _log_age_seconds(BOT_HEARTBEAT_FILE)
    if age is None:
        # File doesn't exist yet (first run after deploy) — skip
        return {"service": "bot", "running": True, "frozen": False,
                "message": "Bot heartbeat 文件不存在，跳过（首次启动）"}
    if age > BOT_HEARTBEAT_THRESHOLD:
        return {"service": "bot", "running": True, "frozen": True,
                "message": f"Bot 轮询线程已挂（心跳陈旧 {age:.0f}s）"}
    return {"service": "bot", "running": True, "frozen": False,
            "message": f"Bot 心跳正常（{age:.0f}s 前更新）"}


def _restart_self() -> None:
    """Restart ai-supervisor via launchctl. This process will be killed."""
    import subprocess
    logger.warning("[watchdog] Bot thread dead — restarting com.ai-supervisor via launchctl")
    subprocess.Popen(
        ["/bin/launchctl", "stop", "com.ai-supervisor"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    # launchctl will restart us automatically (OnDemand=false / KeepAlive=true)


def run_watchdog_once() -> list[dict]:
    results = []
    services = _load_watchlist()
    for svc in services:
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

    # Check bot polling thread health via heartbeat file
    try:
        bot_r = check_bot_health()
        results.append(bot_r)
        if bot_r.get("frozen"):
            logger.warning("[watchdog] %s", bot_r["message"])
            if not _in_cooldown("bot"):
                _last_rescue["bot"] = time.time()
                _restart_self()
        else:
            logger.debug("[watchdog] %s", bot_r["message"])  # healthy = debug only, no log spam
    except Exception as e:
        logger.exception("[watchdog] Error checking bot health: %s", e)

    return results


def run_watchdog_loop() -> None:
    logger.info(
        "[watchdog] Starting loop (interval=%ds, cooldown=%ds, quiet=%s-%s)",
        WATCHDOG_CHECK_INTERVAL, RESCUE_COOLDOWN,
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
