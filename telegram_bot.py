"""
telegram_bot.py — Telegram bot for AI Supervisor control.

Commands:
  /start        — welcome message
  /help         — show all commands
  /status       — service status (openclaw + nanoclaw)
  /fix <agent>  — restart openclaw|nanoclaw|all
  /run <cmd>    — run a shell command
  /claude <task>— run a Claude Code task
  /logs         — recent openclaw logs
  /logs errors  — error logs
  /logs tmp     — /tmp/openclaw logs
  /logs summary — log file summary
  /logs search <keyword> — search logs

Powered by python-telegram-bot (v20+ async API).
"""
from __future__ import annotations

import asyncio
import logging
import sys
import os
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent))

from telegram import Update
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    ContextTypes,
    filters,
)

from config.settings import TELEGRAM_BOT_TOKEN, ALLOWED_USERS
from supervisor import run_task

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[
        logging.FileHandler(str(Path(__file__).parent / "logs" / "supervisor.log")),
        logging.StreamHandler(),
    ],
)
logger = logging.getLogger(__name__)

MAX_MESSAGE_LEN = 4096


def _check_allowed(update: Update) -> bool:
    """Return True if the user is allowed to use this bot."""
    if not ALLOWED_USERS:
        return True
    return update.effective_user.id in ALLOWED_USERS


def _split_message(text: str, limit: int = MAX_MESSAGE_LEN) -> list[str]:
    """Split a long message into chunks that fit Telegram's limit."""
    if len(text) <= limit:
        return [text]
    chunks = []
    while text:
        chunk = text[:limit]
        # Try to break at newline
        if len(text) > limit:
            nl = chunk.rfind("\n")
            if nl > limit // 2:
                chunk = chunk[:nl]
        chunks.append(chunk)
        text = text[len(chunk):]
    return chunks


async def send_reply(update: Update, text: str) -> None:
    """Send a reply, splitting if needed."""
    if not text:
        text = "(empty response)"
    for chunk in _split_message(text):
        await update.message.reply_text(chunk)


async def _run_and_reply(update: Update, task: str) -> None:
    """Run a supervisor task and reply with the result."""
    await update.message.reply_text("⏳ Processing...")
    try:
        result = run_task(task)
        # run_task returns a plain string
        if isinstance(result, dict):
            output = result.get("result", "(no output)")
            success = result.get("success", False)
            task_type = result.get("task_type", "?")
            icon = "✅" if success else "❌"
            header = f"{icon} [{task_type}]"
            await send_reply(update, f"{header}\n\n{output}")
        else:
            output = str(result) if result else "(no output)"
            await send_reply(update, output)
    except Exception as e:
        logger.exception("Error running task: %s", task)
        await update.message.reply_text(f"❌ Error: {e}")


# ---------------------------------------------------------------------------
# Handlers
# ---------------------------------------------------------------------------

async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _check_allowed(update):
        return
    await update.message.reply_text(
        "👋 AI Supervisor is online!\n\n"
        "Use /help to see available commands."
    )


async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _check_allowed(update):
        return
    help_text = (
        "🤖 *AI Supervisor Commands*\n\n"
        "/status — service status\n"
        "/fix openclaw — restart openclaw gateway\n"
        "/fix nanoclaw — restart nanoclaw\n"
        "/fix all — restart both services\n"
        "/run `<cmd>` — run shell command\n"
        "/claude `<task>` — run Claude Code task\n"
        "/logs — recent openclaw logs\n"
        "/logs errors — error logs\n"
        "/logs tmp — /tmp/openclaw logs\n"
        "/logs summary — log file sizes\n"
        "/logs search `<keyword>` — search logs"
    )
    await update.message.reply_text(help_text, parse_mode="Markdown")


async def cmd_status(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _check_allowed(update):
        return
    await _run_and_reply(update, "openclaw status")


async def cmd_fix(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _check_allowed(update):
        return
    args = context.args
    if not args:
        await update.message.reply_text("Usage: /fix <openclaw|nanoclaw|all>")
        return
    target = args[0].lower()
    if target not in ("openclaw", "nanoclaw", "all"):
        await update.message.reply_text("Unknown agent. Use: openclaw, nanoclaw, or all")
        return
    await _run_and_reply(update, f"restart {target}")


async def cmd_run(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _check_allowed(update):
        return
    if not context.args:
        await update.message.reply_text("Usage: /run <shell command>")
        return
    cmd = " ".join(context.args)
    await _run_and_reply(update, f"shell: {cmd}")


async def cmd_claude(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _check_allowed(update):
        return
    if not context.args:
        await update.message.reply_text("Usage: /claude <coding task description>")
        return
    task = " ".join(context.args)
    await _run_and_reply(update, f"claude: {task}")


async def cmd_logs(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _check_allowed(update):
        return

    args = context.args

    if not args:
        await _run_and_reply(update, "recent logs")
        return

    sub = args[0].lower()

    if sub == "errors":
        await _run_and_reply(update, "error logs")
    elif sub == "tmp":
        await _run_and_reply(update, "tmp logs")
    elif sub == "summary":
        await _run_and_reply(update, "log summary")
    elif sub == "search":
        if len(args) < 2:
            await update.message.reply_text("Usage: /logs search <keyword>")
            return
        keyword = args[1]
        await _run_and_reply(update, f"search logs for {keyword}")
    else:
        # Treat sub as a quick tail count or pass through
        await _run_and_reply(update, f"recent logs {sub}")


async def msg_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle plain text messages as tasks routed through supervisor."""
    if not _check_allowed(update):
        return
    text = (update.message.text or "").strip()
    if not text:
        return
    logger.info("Received message from %s: %s", update.effective_user.id, text[:80])
    await _run_and_reply(update, text)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    """Start the Telegram bot."""
    if not TELEGRAM_BOT_TOKEN:
        logger.error("TELEGRAM_BOT_TOKEN is not set!")
        sys.exit(1)

    logger.info("Starting AI Supervisor Telegram bot...")

    app = Application.builder().token(TELEGRAM_BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("help", cmd_help))
    app.add_handler(CommandHandler("status", cmd_status))
    app.add_handler(CommandHandler("fix", cmd_fix))
    app.add_handler(CommandHandler("run", cmd_run))
    app.add_handler(CommandHandler("claude", cmd_claude))
    app.add_handler(CommandHandler("logs", cmd_logs))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, msg_handler))

    logger.info("Bot polling started.")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
