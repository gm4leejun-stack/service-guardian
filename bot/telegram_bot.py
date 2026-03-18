"""
bot/telegram_bot.py — Telegram bot for AI Supervisor (v2/v3 architecture).

Routes all messages through brain.py, which runs claude --print as a subprocess.
The bot instance and event loop are injected into notify_tools
so the Agent can send real-time progress updates.
"""
from __future__ import annotations

import asyncio
import logging
import sys
import time
from pathlib import Path

HEARTBEAT_FILE = Path(__file__).parent.parent / "logs" / "bot_heartbeat.txt"

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
import agent.brain as brain
from tools.system_tools import _system_status_impl
from tools.nanoclaw_tools import nanoclaw_manage_mount, nanoclaw_register_group
from tools.service_tools import restart_service_tool, check_service

logger = logging.getLogger(__name__)

MAX_MESSAGE_LEN = 4096


def format_token_stats(usage: dict) -> str:
    """格式化 token 统计行，追加到消息末尾"""
    if not usage:
        return ""
    input_total = (
        usage.get("input_tokens", 0)
        + usage.get("cache_creation_input_tokens", 0)
        + usage.get("cache_read_input_tokens", 0)
    )
    output = usage.get("output_tokens", 0)

    def fmt(n: int) -> str:
        return f"{n/1000:.1f}K" if n >= 1000 else str(n)

    return f"\n\n[⬆️ {fmt(input_total)}  ⬇️ {fmt(output)}]"


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
        last_err = None
        for attempt in range(3):
            try:
                await update.message.reply_text(chunk)
                last_err = None
                break
            except Exception as e:
                last_err = e
                if attempt < 2:
                    await asyncio.sleep(2 ** attempt)  # 1s, 2s
        if last_err:
            raise last_err


async def _run_agent_and_reply(update: Update, task: str) -> None:
    """Run the Agent with the given task and send the final response."""
    chat_id = update.effective_chat.id

    # Show thinking indicator — best-effort, don't abort if Telegram times out
    try:
        await update.message.reply_text("⏳ 思考中...")
    except Exception as e:
        logger.warning("Could not send thinking indicator: %s", e)

    try:
        # run_agent is async; asyncio.wait_for is a belt-and-suspenders guard
        # (brain.py has its own 600s subprocess timeout via asyncio.to_thread).
        result_text, usage = await asyncio.wait_for(
            run_agent(task, chat_id=chat_id, thread_id=str(chat_id)),
            timeout=720,  # 12 min > brain's 10 min subprocess timeout
        )
        if result_text:
            stats = format_token_stats(usage)
            await send_reply(update, result_text + stats)
    except asyncio.TimeoutError:
        logger.error("Executor timeout (720s) for chat %s task: %s", chat_id, task[:60])
        try:
            await update.message.reply_text("❌ 任务超时，请重试")
        except Exception:
            pass
    except Exception as e:
        logger.exception("Agent error for task: %s", task)
        try:
            await update.message.reply_text(f"❌ 执行出错: {e}")
        except Exception:
            pass


# ---------------------------------------------------------------------------
# Command Handlers
# ---------------------------------------------------------------------------

async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _check_allowed(update):
        return
    await update.message.reply_text(
        "👋 SuperDevOps 已上线！\n\n"
        "我是你的超级本地 DevOps Agent，可以帮你：\n"
        "• 检查 OpenClaw / NanoClaw 服务状态\n"
        "• 诊断并修复服务问题\n"
        "• 查看日志和搜索错误\n"
        "• 执行系统命令\n"
        "• 管理 NanoClaw 群组和挂载点\n"
        "• 查看系统资源（/sysinfo）\n"
        "• 克隆项目并安装依赖\n\n"
        "直接用中文告诉我你需要什么，或者用 /help 查看快捷命令。"
    )


async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _check_allowed(update):
        return
    help_text = (
        "🤖 *SuperDevOps 命令*\n\n"
        "*系统监控*\n"
        "/sysinfo — 系统资源 + 服务状态（零延迟）\n"
        "/status — 服务状态（通过 Agent）\n\n"
        "*服务管理*\n"
        "/fix openclaw — 重启 OpenClaw\n"
        "/fix nanoclaw — 重启 NanoClaw\n"
        "/fix all — 重启所有服务\n\n"
        "*日志*\n"
        "/logs — 查看最近日志\n"
        "/logs errors — 查看错误日志\n"
        "/logs tmp — 查看 /tmp 日志\n"
        "/logs summary — 日志文件概览\n\n"
        "*执行*\n"
        "/run `<命令>` — 执行 Shell 命令\n"
        "/claude `<任务>` — 调用 Claude Code\n"
        "/scaffold `<路径>` `<repo_url>` — 克隆项目并安装依赖\n\n"
        "*上下文 & Token*\n"
        "/new — 清空当前对话上下文，开始新任务\n"
        "/input — 查看上次调用的 Token 用量分析\n\n"
        "*NanoClaw 管理*\n"
        "/nano groups — 列出所有群组（零延迟）\n"
        "/nano mount add `<路径>` `<jid>` [container_path] — 添加挂载\n"
        "/nano mount remove `<路径>` `<jid>` — 移除挂载\n"
        "/nano register `<jid>` `<name>` `<folder>` — 注册新群组\n\n"
        "💡 也可以直接用自然语言描述，例如：\n"
        "- OpenClaw 没回复了，帮我检查一下\n"
        "- 把 ~/ai-supervisor 挂载到 telegram\\_nanoclaw 群组\n"
        "- 注册新群组 tg:-100xxx MyGroup mygroup"
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


async def cmd_sysinfo(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Zero-LLM fast path: psutil + service status, no Haiku call."""
    if not _check_allowed(update):
        return
    loop = asyncio.get_running_loop()
    result = await loop.run_in_executor(None, _system_status_impl)
    await send_reply(update, result)


def _query_nanoclaw_groups() -> str:
    """Direct sqlite3 query — zero LLM tokens."""
    from pathlib import Path
    import sqlite3
    db = Path.home() / "nanoclaw/store/messages.db"
    if not db.exists():
        return "❌ NanoClaw DB 不存在"
    try:
        conn = sqlite3.connect(str(db))
        rows = conn.execute(
            "SELECT jid, name, folder, requires_trigger, is_main FROM registered_groups ORDER BY is_main DESC, added_at"
        ).fetchall()
        conn.close()
    except Exception as e:
        return f"❌ 查询失败: {e}"
    if not rows:
        return "暂无注册群组"
    lines = ["*NanoClaw 已注册群组*\n"]
    for jid, name, folder, req_trig, is_main in rows:
        tag = " 🏠主" if is_main else ""
        trig = " 🔑触发词" if req_trig else ""
        lines.append(f"• {name}{tag}{trig}\n  JID: `{jid}`\n  目录: `{folder}`")
    return "\n".join(lines)


async def _nano_mount_direct(
    update: Update, op: str, path: str, group: str, container_path: str
) -> None:
    """Zero-LLM direct execution path for mount operations.
    Pattern: call tool → restart → verify → reply. No Agent involved.
    """
    loop = asyncio.get_running_loop()

    mount_result = await loop.run_in_executor(
        None, lambda: nanoclaw_manage_mount.invoke({
            "operation": op, "path": path, "group": group,
            "container_path": container_path, "readonly": False,
        })
    )
    if "❌" in mount_result:
        await send_reply(update, mount_result)
        return

    restart_result = await loop.run_in_executor(
        None, lambda: restart_service_tool.invoke({"service": "nanoclaw"})
    )
    verify_result = await loop.run_in_executor(
        None, lambda: check_service.invoke({"service": "nanoclaw"})
    )
    await send_reply(update, f"{mount_result}\n\n{restart_result}\n{verify_result}")


async def _nano_register_direct(
    update: Update, jid: str, name: str, folder: str
) -> None:
    """Zero-LLM direct execution path for group registration.
    Pattern: call tool → restart → verify → reply. No Agent involved.
    """
    loop = asyncio.get_running_loop()

    reg_result = await loop.run_in_executor(
        None, lambda: nanoclaw_register_group.invoke({
            "jid": jid, "name": name, "folder": folder,
        })
    )
    if "❌" in reg_result:
        await send_reply(update, reg_result)
        return

    restart_result = await loop.run_in_executor(
        None, lambda: restart_service_tool.invoke({"service": "nanoclaw"})
    )
    verify_result = await loop.run_in_executor(
        None, lambda: check_service.invoke({"service": "nanoclaw"})
    )
    await send_reply(update, f"{reg_result}\n\n{restart_result}\n{verify_result}")


async def cmd_nano(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """NanoClaw management commands."""
    if not _check_allowed(update):
        return
    args = context.args
    if not args:
        await update.message.reply_text(
            "用法:\n"
            "/nano groups — 列出所有群组\n"
            "/nano mount add <路径> <jid> [container_path]\n"
            "/nano mount remove <路径> <jid>\n"
            "/nano register <jid> <name> <folder>"
        )
        return

    sub = args[0].lower()

    if sub == "groups":
        # Zero-LLM fast path
        result = _query_nanoclaw_groups()
        await update.message.reply_text(result, parse_mode="Markdown")
        return

    if sub == "mount":
        # Zero-LLM direct execution: mount → restart → verify → reply
        if len(args) < 4:
            await update.message.reply_text(
                "用法: /nano mount add|remove <路径> <群组名或JID> [container_path]\n"
                "群组名示例: 🦠NanoClaw 或 telegram_nanoclaw"
            )
            return
        op = args[1].lower()
        path = args[2]
        group = args[3]
        container_path = args[4] if len(args) > 4 else ""
        await _nano_mount_direct(update, op, path, group, container_path)
        return

    if sub == "register":
        # Zero-LLM direct execution: register → restart → verify → reply
        if len(args) < 4:
            await update.message.reply_text("用法: /nano register <jid> <name> <folder>")
            return
        jid = args[1]
        name = args[2]
        folder = args[3]
        await _nano_register_direct(update, jid, name, folder)
        return

    await update.message.reply_text(f"未知子命令: {sub}。可选: groups, mount, register")


async def cmd_new(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Clear current working memory and start a new task context."""
    if not _check_allowed(update):
        return
    thread_id = str(update.effective_chat.id)
    with brain._memory_lock:
        history = list(brain.working_memory.get(thread_id, []))
    if history:
        summary = await brain.generate_summary_with_haiku(thread_id)
        try:
            brain.save_long_term_memory(thread_id, summary)
        except Exception as e:
            logger.warning("Failed to save long-term memory: %s", e)
            # Continue with clearing working memory even if save fails
        with brain._memory_lock:
            brain.working_memory[thread_id] = []
            brain.last_usage.pop(thread_id, None)
        await update.message.reply_text("✅ 上下文已清除，开始新任务")
    else:
        await update.message.reply_text("📭 当前无活跃上下文。")


async def cmd_input(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Show token usage breakdown for the last agent call in this thread."""
    if not _check_allowed(update):
        return
    thread_id = str(update.effective_chat.id)
    usage_record = brain.last_usage.get(thread_id)
    if not usage_record:
        await update.message.reply_text("暂无记录，请先发送一条消息。")
        return

    input_tokens = usage_record.get("input_tokens", 0)
    cache_create = usage_record.get("cache_creation_input_tokens", 0)
    cache_read = usage_record.get("cache_read_input_tokens", 0)
    output_tokens = usage_record.get("output_tokens", 0)
    input_total = input_tokens + cache_create + cache_read

    def _fmt(n: int) -> str:
        return f"{n/1000:.1f}K" if n >= 1000 else str(n)

    lines = [
        "📊 上次 Token 用量分析",
        "",
        f"输入合计：{_fmt(input_total)}",
        f"  ├ 直接输入：{_fmt(input_tokens)}",
        f"  ├ 缓存创建：{_fmt(cache_create)}",
        f"  └ 缓存命中：{_fmt(cache_read)}",
        f"输出：{_fmt(output_tokens)}",
    ]
    await update.message.reply_text("\n".join(lines))


async def cmd_scaffold(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _check_allowed(update):
        return
    if len(context.args) < 2:
        await update.message.reply_text("用法: /scaffold <目标路径> <repo_url>")
        return
    path = context.args[0]
    repo_url = context.args[1]
    await _run_agent_and_reply(update, f"克隆项目 {repo_url} 到 {path} 并自动安装依赖")


async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Log all errors so they don't silently disappear."""
    logger.error("Telegram error: %s", context.error, exc_info=context.error)


async def msg_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle plain text messages via the Agent."""
    if not _check_allowed(update):
        return
    text = (update.message.text or "").strip()
    if not text:
        return
    if text.startswith("Stop hook feedback:"):
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

    from telegram.request import HTTPXRequest
    app = (
        Application.builder()
        .token(TELEGRAM_BOT_TOKEN)
        # Set explicit read/connect timeouts for getUpdates to prevent
        # TCP-level hangs that freeze the polling loop indefinitely.
        .get_updates_request(HTTPXRequest(read_timeout=30, connect_timeout=15))
        .build()
    )

    # Register handlers
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("help", cmd_help))
    app.add_handler(CommandHandler("status", cmd_status))
    app.add_handler(CommandHandler("fix", cmd_fix))
    app.add_handler(CommandHandler("run", cmd_run))
    app.add_handler(CommandHandler("claude", cmd_claude))
    app.add_handler(CommandHandler("logs", cmd_logs))
    app.add_handler(CommandHandler("sysinfo", cmd_sysinfo))
    app.add_handler(CommandHandler("nano", cmd_nano))
    app.add_handler(CommandHandler("scaffold", cmd_scaffold))
    app.add_handler(CommandHandler("new", cmd_new))
    app.add_handler(CommandHandler("input", cmd_input))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, msg_handler))
    app.add_error_handler(error_handler)

    # Inject bot instance into notify_tools after the app is built
    # We do this via post_init so the bot and loop are both ready
    async def post_init(application: Application) -> None:
        loop = asyncio.get_running_loop()
        notify_tools.setup(application.bot, loop)
        logger.info("Bot instance injected into notify_tools")

        async def _heartbeat_loop() -> None:
            while True:
                try:
                    HEARTBEAT_FILE.write_text(str(time.time()))
                except Exception:
                    pass
                await asyncio.sleep(30)

        asyncio.create_task(_heartbeat_loop())
        logger.info("Bot heartbeat task started → %s", HEARTBEAT_FILE)

        from telegram import BotCommand
        await application.bot.set_my_commands([
            BotCommand("sysinfo",  "系统资源 + 服务状态（零延迟）"),
            BotCommand("status",   "服务状态（通过 Agent）"),
            BotCommand("fix",      "重启服务：fix <openclaw|nanoclaw|all>"),
            BotCommand("logs",     "查看日志：logs [errors|tmp|summary|search]"),
            BotCommand("run",      "执行 Shell 命令"),
            BotCommand("claude",   "调用 Claude Code"),
            BotCommand("scaffold", "克隆项目：scaffold <路径> <repo_url>"),
            BotCommand("nano",     "NanoClaw 管理：nano <groups|mount|register>"),
            BotCommand("new",      "清空当前对话上下文，开始新任务"),
            BotCommand("input",    "查看上次调用的 Token 用量分析"),
            BotCommand("help",     "查看所有命令"),
            BotCommand("start",    "启动 bot"),
        ])
        logger.info("Bot commands registered with Telegram")

    app.post_init = post_init

    logger.info("Bot polling started.")
    app.run_polling(allowed_updates=Update.ALL_TYPES, drop_pending_updates=False)


if __name__ == "__main__":
    main()
