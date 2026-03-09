"""
notify_tools.py — LangGraph tool for sending real-time progress notifications via Telegram.

Usage:
  1. At bot startup, call setup(bot, loop) to inject the bot instance and event loop.
  2. When handling a message, call set_chat_id(chat_id) to set the active chat.
  3. The Agent can call notify_user(message) at any step to push a progress update.
"""
from __future__ import annotations

import asyncio
import logging
from langchain_core.tools import tool

logger = logging.getLogger(__name__)

# Global state — injected at runtime
_bot = None
_loop: asyncio.AbstractEventLoop | None = None
_chat_id: int | None = None


def setup(bot, loop: asyncio.AbstractEventLoop) -> None:
    """Inject the Telegram bot instance and its event loop."""
    global _bot, _loop
    _bot = bot
    _loop = loop
    logger.info("[notify_tools] Bot instance injected")


def set_chat_id(chat_id: int) -> None:
    """Set the active chat ID for notifications (called per-message)."""
    global _chat_id
    _chat_id = chat_id


def get_chat_id() -> int | None:
    return _chat_id


@tool
def notify_user(message: str) -> str:
    """
    立即向用户发送进度通知（Telegram 消息）。
    在 Agent 执行过程中随时调用此工具推送状态更新。

    Args:
        message: 要发送的消息内容

    Returns:
        "ok" 表示发送成功，或错误描述
    """
    if not _bot or not _loop or not _chat_id:
        logger.warning("[notify_user] Bot not configured, skipping notification: %s", message[:60])
        return "skipped (bot not configured)"

    try:
        future = asyncio.run_coroutine_threadsafe(
            _bot.send_message(chat_id=_chat_id, text=message),
            _loop,
        )
        future.result(timeout=5)
        logger.info("[notify_user] Sent: %s", message[:60])
        return "ok"
    except Exception as e:
        logger.error("[notify_user] Failed to send: %s", e)
        return f"error: {e}"


def send_notification_sync(message: str, chat_id: int | None = None) -> bool:
    """
    Send a notification synchronously from non-tool code (e.g. watchdog).
    Returns True if sent successfully.
    """
    target_chat_id = chat_id or _chat_id
    if not _bot or not _loop or not target_chat_id:
        logger.warning("[notify_tools] Cannot send: bot=%s loop=%s chat_id=%s",
                       bool(_bot), bool(_loop), target_chat_id)
        return False

    try:
        future = asyncio.run_coroutine_threadsafe(
            _bot.send_message(chat_id=target_chat_id, text=message),
            _loop,
        )
        future.result(timeout=5)
        return True
    except Exception as e:
        logger.error("[notify_tools] send_notification_sync failed: %s", e)
        return False
