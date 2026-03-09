"""
notify_tools.py — send Telegram progress notifications.

chat_id is injected via LangGraph RunnableConfig, eliminating the need
for global state or per-run agent rebuilding.
"""
from __future__ import annotations

import asyncio
import logging
from langchain_core.tools import tool
from langchain_core.runnables import RunnableConfig

logger = logging.getLogger(__name__)

_bot = None
_loop: asyncio.AbstractEventLoop | None = None


def setup(bot, loop: asyncio.AbstractEventLoop) -> None:
    global _bot, _loop
    _bot = bot
    _loop = loop
    logger.info("[notify_tools] ready")


def _send(message: str, chat_id: int) -> str:
    if not _bot or not _loop:
        return "skipped (bot not ready)"
    try:
        future = asyncio.run_coroutine_threadsafe(
            _bot.send_message(chat_id=chat_id, text=message), _loop
        )
        future.result(timeout=5)
        logger.info("[notify] -> %s: %s", chat_id, message[:60])
        return "ok"
    except Exception as e:
        logger.error("[notify] failed: %s", e)
        return f"error: {e}"


@tool
def notify_user(message: str, config: RunnableConfig) -> str:
    """
    立即向用户发送进度通知。每个关键步骤前都应调用。

    Args:
        message: 通知内容，简洁中文，带 emoji 进度标识
    """
    chat_id = (config.get("configurable") or {}).get("chat_id")
    if not chat_id:
        logger.warning("[notify] no chat_id in config, skipping")
        return "skipped"
    return _send(message, chat_id)


def send_sync(message: str, chat_id: int) -> bool:
    """For use outside tools (watchdog reporting)."""
    return _send(message, chat_id) == "ok"
