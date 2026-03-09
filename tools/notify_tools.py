"""
notify_tools.py — LangGraph tool for sending real-time progress notifications via Telegram.

notify_user is created fresh per agent-run with chat_id captured in closure,
so concurrent sessions never interfere.
"""
from __future__ import annotations

import asyncio
import logging
from langchain_core.tools import tool

logger = logging.getLogger(__name__)

_bot = None
_loop: asyncio.AbstractEventLoop | None = None


def setup(bot, loop: asyncio.AbstractEventLoop) -> None:
    global _bot, _loop
    _bot = bot
    _loop = loop
    logger.info("[notify_tools] Bot instance injected")


def _send(message: str, chat_id: int) -> str:
    if not _bot or not _loop:
        return "skipped (bot not ready)"
    try:
        future = asyncio.run_coroutine_threadsafe(
            _bot.send_message(chat_id=chat_id, text=message),
            _loop,
        )
        future.result(timeout=5)
        logger.info("[notify_user] -> %s: %s", chat_id, message[:60])
        return "ok"
    except Exception as e:
        logger.error("[notify_user] Failed: %s", e)
        return f"error: {e}"


def make_notify_tool(chat_id: int | None):
    """
    Create a notify_user tool with chat_id captured in closure.
    Call this once per agent invocation so each run has its own chat_id.
    """
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
        if not chat_id:
            logger.warning("[notify_user] No chat_id, skipping: %s", message[:60])
            return "skipped (no chat_id)"
        return _send(message, chat_id)

    return notify_user


def send_notification_sync(message: str, chat_id: int | None = None) -> bool:
    if not chat_id:
        return False
    return _send(message, chat_id) == "ok"


# Default tool instance (chat_id=None) — replaced per-run in brain.py
notify_user = make_notify_tool(None)
