"""
bot/telegram_bot.py — Telegram bot for AI Supervisor.

Routes all messages through the LangGraph ReAct Agent (brain.py).
The bot instance and event loop are injected into notify_tools
so the Agent can send real-time progress updates.
"""
from __future__ import annotations

import asyncio
import logging
import sys
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from telegram import Update
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    ContextTypes,
    filters,
)

from config.settings import TELEGRAM_BOT_TOKEN, ALLOWED_USERS
import tools.notify_tools as notify_tools  # needs setup() called at startup
from agent.brain import run_agent

logger = logging.getLogger(__name__)

MAX_MESSAGE_LEN = 4096


def _check_allowed(update: Update) -> bool:
    if not ALLOWED_USERS:
        return True
    return update.effective_user.id in ALLOWED_USERS


def _split_message(text: str, limit: int = MAX_MESSAGE_LEN) -> list[str]:
    if len(text) <= limit:
        return [text]
    chunks = []
    while text:
        chunk = text[:limit]
        if len(text) > limit:
            nl = chunk.rfind("\n")
            if nl > limit // 2:
                chunk = chunk[:nl]
        chunks.append(chunk)
        text = text[len(chunk):]
    return chunks


async def send_reply(update: Update, text: str) -> None:
    if not text:
        text = "(empty response)"
    for chunk in _split_message(text):
        await update.message.reply_text(chunk)


async def _run_agent_and_reply(update: Update, task: str) -> None:
    """Run the Agent with the given task and send the final response."""
    chat_id = update.effective_chat.id

    # Show thinking indicator
    await update.message.reply_text("⏳ 思考中...")

    try:
        # Run agent in thread pool (it's blocking)
        loop = asyncio.get_event_loop()
        result = await loop.run_in_executor(
            None,
            lambda: run_agent(task, chat_id=chat_id, thread_id=str(chat_id))
        )
        if result:
            await send_reply(update, result)
    except Exception as e:
        logger.exception("Agent error for task: %s", task)
        await update.message.reply_text(f"❌ 执行出错: {e}")


# ---------------------------------------------------------------------------
# Command Handlers
# ---------------------------------------------------------------------------

async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _check_allowed(update):
        return
    await update.message.reply_text(
        "👋 AI Supervisor 已上线！\n\n"
        "我是你的服务守护 Agent，可以帮你：\n"
        "• 检查 OpenClaw / NanoClaw 服务状态\n"
        "• 诊断并修复服务问题\n"
        "• 查看日志和搜索错误\n"
        "• 执行系统命令\n\n"
        "直接用中文告诉我你需要什么，或者用 /help 查看快捷命令。"
    )


async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _check_allowed(update):
        return
    help_text = (
        "🤖 *AI Supervisor 命令*\n\n"
        "/status — 查看服务状态\n"
        "/fix openclaw — 重启 OpenClaw\n"
        "/fix nanoclaw — 重启 NanoClaw\n"
        "/fix all — 重启所有服务\n"
        "/logs — 查看最近日志\n"
        "/logs errors — 查看错误日志\n"
        "/logs tmp — 查看 /tmp 日志\n"
        "/logs summary — 日志文件概览\n"
        "/run `<命令>` — 执行 Shell 命令\n"
        "/claude `<任务>` — 调用 Claude Code\n\n"
        "💡 也可以直接用自然语言描述，例如：\n"
        "- OpenClaw 没回复了，帮我检查一下\n"
        "- 查看最近的错误日志\n"
        "- 重启 nanoclaw 并验证是否正常"
    )
    await update.message.reply_text(help_text, parse_mode="Markdown")


async def cmd_status(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _check_allowed(update):
        return
    await _run_agent_and_reply(update, "检查所有服务状态，包括 openclaw 和 nanoclaw")


async def cmd_fix(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _check_allowed(update):
        return
    args = context.args
    if not args:
        await update.message.reply_text("用法：/fix <openclaw|nanoclaw|all>")
        return
    target = args[0].lower()
    if target not in ("openclaw", "nanoclaw", "all"):
        await update.message.reply_text("未知服务，可选：openclaw、nanoclaw、all")
        return
    await _run_agent_and_reply(update, f"重启 {target} 服务，完成后验证服务是否正常运行")


async def cmd_run(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _check_allowed(update):
        return
    if not context.args:
        await update.message.reply_text("用法：/run <shell 命令>")
        return
    cmd = " ".join(context.args)
    await _run_agent_and_reply(update, f"执行 Shell 命令：{cmd}")


async def cmd_claude(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _check_allowed(update):
        return
    if not context.args:
        await update.message.reply_text("用法：/claude <任务描述>")
        return
    task = " ".join(context.args)
    await _run_agent_and_reply(update, f"使用 Claude Code 完成以下任务：{task}")


async def cmd_logs(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _check_allowed(update):
        return
    args = context.args

    if not args:
        await _run_agent_and_reply(update, "读取 openclaw 最近50行日志")
        return

    sub = args[0].lower()
    if sub == "errors":
        await _run_agent_and_reply(update, "读取 openclaw 错误日志最近30行")
    elif sub == "tmp":
        await _run_agent_and_reply(update, "读取 /tmp/openclaw 最新日志")
    elif sub == "summary":
        await _run_agent_and_reply(update, "查看所有日志文件的大小和修改时间摘要")
    elif sub == "search":
        if len(args) < 2:
            await update.message.reply_text("用法：/logs search <关键词>")
            return
        keyword = args[1]
        await _run_agent_and_reply(update, f"在所有日志中搜索关键词：{keyword}")
    else:
        await _run_agent_and_reply(update, f"读取日志：{sub}")


async def msg_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle plain text messages via the Agent."""
    if not _check_allowed(update):
        return
    text = (update.message.text or "").strip()
    if not text:
        return
    logger.info("Message from %s: %s", update.effective_user.id, text[:80])
    await _run_agent_and_reply(update, text)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    """Start the Telegram bot with bot instance injection."""
    if not TELEGRAM_BOT_TOKEN:
        logger.error("TELEGRAM_BOT_TOKEN is not set!")
        sys.exit(1)

    logger.info("Starting AI Supervisor Telegram bot (Agent mode)...")

    app = Application.builder().token(TELEGRAM_BOT_TOKEN).build()

    # Register handlers
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("help", cmd_help))
    app.add_handler(CommandHandler("status", cmd_status))
    app.add_handler(CommandHandler("fix", cmd_fix))
    app.add_handler(CommandHandler("run", cmd_run))
    app.add_handler(CommandHandler("claude", cmd_claude))
    app.add_handler(CommandHandler("logs", cmd_logs))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, msg_handler))

    # Inject bot instance into notify_tools after the app is built
    # We do this via post_init so the bot and loop are both ready
    async def post_init(application: Application) -> None:
        loop = asyncio.get_event_loop()
        notify_tools.setup(application.bot, loop)
        logger.info("Bot instance injected into notify_tools")

    app.post_init = post_init

    logger.info("Bot polling started.")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
