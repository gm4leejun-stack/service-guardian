# Service Guardian (ai-supervisor)

A self-healing AI Agent that monitors, diagnoses, and automatically repairs OpenClaw and NanoClaw services on your Mac mini.

## Architecture

```
Telegram (@jun_xiao_code_bot)
    ↕ real-time bidirectional
LangGraph ReAct Agent (Claude Haiku)
    ↕ tool calls
Tools:
  ├── check_service      — service status via launchctl
  ├── restart_service    — stop + start via launchctl
  ├── read_logs          — tail log files
  ├── search_logs        — grep log files
  ├── fix_with_claude    — delegate code fixes to Claude Code CLI
  ├── run_shell_command  — safe shell execution
  └── notify_user        — real-time Telegram progress push
Watchdog (background thread)
  → detects log staleness → triggers Agent rescue → quiet hours support
```

## Setup

### 1. Install dependencies

```bash
cd ~/ai-supervisor
python -m venv venv
venv/bin/pip install -r requirements.txt
```

### 2. Configure secrets

```bash
cp .env.example .env
# Edit .env and fill in:
#   TELEGRAM_BOT_TOKEN — your bot token from @BotFather
#   ANTHROPIC_API_KEY  — your Anthropic API key
#   ADMIN_CHAT_ID      — your Telegram user ID (for watchdog alerts)
```

### 3. Run manually

```bash
cd ~/ai-supervisor
venv/bin/python main.py
```

### 4. Install as LaunchAgent (auto-start on login)

```bash
cd ~/ai-supervisor
./install.sh
```

## Configuration

All settings are in `config/settings.py`, loaded from `.env`:

| Variable | Default | Description |
|----------|---------|-------------|
| `TELEGRAM_BOT_TOKEN` | — | Bot token from @BotFather |
| `ANTHROPIC_API_KEY` | — | Anthropic API key |
| `ANTHROPIC_BASE_URL` | `https://llmapi.lovbrowser.com` | API proxy URL |
| `HAIKU_MODEL` | `claude-haiku-4-5-20251001` | LLM model for Agent |
| `ADMIN_CHAT_ID` | — | Telegram user ID for watchdog alerts |
| `WATCHDOG_CHECK_INTERVAL` | 60s | How often watchdog checks services |
| `WATCHDOG_FREEZE_THRESHOLD` | 120s | Log staleness threshold for freeze detection |
| `WATCHDOG_QUIET_HOURS_START` | 0 (midnight) | Quiet hours start (no Telegram alerts) |
| `WATCHDOG_QUIET_HOURS_END` | 8 (8am) | Quiet hours end |

## Usage

### Telegram commands

| Command | Description |
|---------|-------------|
| `/status` | Check all service statuses |
| `/fix openclaw` | Restart OpenClaw gateway |
| `/fix nanoclaw` | Restart NanoClaw |
| `/fix all` | Restart all services |
| `/logs` | Recent OpenClaw logs |
| `/logs errors` | Error logs |
| `/logs tmp` | /tmp/openclaw logs |
| `/logs summary` | Log file sizes |
| `/logs search <kw>` | Search logs for keyword |
| `/run <cmd>` | Execute shell command |
| `/claude <task>` | Run Claude Code task |

### Natural language

You can also speak naturally:
- "OpenClaw 没回复了，帮我检查一下"
- "查看最近的错误日志"
- "重启 nanoclaw 并验证是否正常"

## Auto-rescue flow

When Watchdog detects a frozen service, the Agent automatically:

1. 🔍 Sends initial notification and starts diagnosis
2. 📋 Reads recent logs (last 100 lines)
3. ⚠️ Reports findings (error messages, anomalies)
4. 🔧 Either fixes code (via Claude Code) or restarts service
5. 🔄 Restarts service if needed
6. ⏳ Waits and verifies recovery
7. ✅ Sends final report with resolution summary

## Project structure

```
ai-supervisor/
├── .env                    # secrets (not in git)
├── .env.example            # template
├── main.py                 # entry point
├── watchdog.py             # freeze detection + Agent trigger
├── config/settings.py      # all configuration
├── agent/
│   ├── brain.py            # LangGraph ReAct Agent
│   └── prompts.py          # system prompt
├── tools/
│   ├── service_tools.py    # check_service, restart_service
│   ├── log_tools.py        # read_logs, search_logs
│   ├── shell_tools.py      # run_shell_command
│   ├── claude_tools.py     # fix_with_claude
│   └── notify_tools.py     # notify_user (real-time Telegram push)
├── bot/
│   └── telegram_bot.py     # Telegram bot handlers
└── workers/                # low-level service/log/shell primitives
```
