#!/bin/bash
# install.sh — one-command setup for ai-supervisor
#
# Usage: bash install.sh
# What it does:
#   1. Create Python venv + install dependencies
#   2. Interactive .env setup (only asks for 2 tokens)
#   3. Register & start LaunchAgent (auto-start on login)

set -e

PROJECT_DIR="$(cd "$(dirname "$0")" && pwd)"
PLIST_DST="$HOME/Library/LaunchAgents/com.ai-supervisor.plist"
SERVICE="com.ai-supervisor"
VENV_PYTHON="$PROJECT_DIR/venv/bin/python"
ENV_FILE="$PROJECT_DIR/.env"

echo ""
echo "=== ai-supervisor installer ==="
echo ""

# ── Step 1: Python venv ──────────────────────────────────────────────────────

if [ ! -f "$VENV_PYTHON" ]; then
    echo "▶ Creating Python virtual environment..."
    python3 -m venv "$PROJECT_DIR/venv"
    echo "▶ Installing dependencies..."
    "$PROJECT_DIR/venv/bin/pip" install -q -r "$PROJECT_DIR/requirements.txt"
    echo "  ✅ Dependencies installed"
else
    echo "  ✅ venv already exists, skipping"
fi

# ── Step 2: .env setup ───────────────────────────────────────────────────────

if [ ! -f "$ENV_FILE" ]; then
    echo ""
    echo "=== Configuration setup ==="
    echo ""

    # Telegram Bot Token
    echo "1) Telegram Bot Token"
    echo "   → Get it from @BotFather on Telegram"
    read -p "   Token: " BOT_TOKEN
    while [ -z "$BOT_TOKEN" ]; do
        read -p "   Token (required): " BOT_TOKEN
    done

    # Anthropic API Key
    echo ""
    echo "2) Anthropic API Key (for Claude)"
    echo "   → Get it from https://console.anthropic.com"
    read -p "   API Key: " API_KEY
    while [ -z "$API_KEY" ]; do
        read -p "   API Key (required): " API_KEY
    done

    # Optional: machine name
    echo ""
    echo "3) Machine name (shown in agent context, optional)"
    echo "   Example: Mac mini, MacBook Pro"
    read -p "   Name [skip]: " MACHINE_NAME

    # Auto-generate EXEC_BRIDGE_TOKEN
    BRIDGE_TOKEN=$(python3 -c "import secrets; print(secrets.token_hex(16))")

    cat > "$ENV_FILE" <<EOF
TELEGRAM_BOT_TOKEN=$BOT_TOKEN
ANTHROPIC_API_KEY=$API_KEY
ANTHROPIC_BASE_URL=https://api.anthropic.com
HAIKU_MODEL=claude-haiku-4-5-20251001
CLAUDE_MODEL=claude-sonnet-4-6
ADMIN_CHAT_ID=
MACHINE_NAME=$MACHINE_NAME
GITHUB_REPO=
EXEC_BRIDGE_TOKEN=$BRIDGE_TOKEN
EXEC_BRIDGE_PORT=18800
EOF

    echo ""
    echo "  ✅ .env created"
    echo ""
    echo "  ⚠️  ADMIN_CHAT_ID is empty — watchdog alerts won't work yet."
    echo "     After starting, send any message to your bot and run:"
    echo "     /myid"
    echo "     Then fill in ADMIN_CHAT_ID in $ENV_FILE"
else
    echo "  ✅ .env already exists, skipping"
fi

# ── Step 3: LaunchAgent ──────────────────────────────────────────────────────

echo ""
echo "▶ Installing LaunchAgent..."

launchctl stop "$SERVICE" 2>/dev/null || true
launchctl unload "$PLIST_DST" 2>/dev/null || true

mkdir -p "$PROJECT_DIR/logs"

cat > "$PLIST_DST" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>$SERVICE</string>

    <key>ProgramArguments</key>
    <array>
        <string>$VENV_PYTHON</string>
        <string>$PROJECT_DIR/main.py</string>
    </array>

    <key>WorkingDirectory</key>
    <string>$PROJECT_DIR</string>

    <key>RunAtLoad</key>
    <true/>

    <key>KeepAlive</key>
    <true/>

    <key>ThrottleInterval</key>
    <integer>10</integer>

    <key>StandardOutPath</key>
    <string>$PROJECT_DIR/logs/stdout.log</string>

    <key>StandardErrorPath</key>
    <string>$PROJECT_DIR/logs/stderr.log</string>

    <key>EnvironmentVariables</key>
    <dict>
        <key>PATH</key>
        <string>$HOME/.local/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin</string>
        <key>HOME</key>
        <string>$HOME</string>
    </dict>
</dict>
</plist>
EOF

chmod 644 "$PLIST_DST"
launchctl load "$PLIST_DST"
launchctl start "$SERVICE"

# ── Done ─────────────────────────────────────────────────────────────────────

echo ""
echo "================================"
echo "✅ ai-supervisor is running!"
echo ""
echo "  Bot:    check Telegram — your bot should respond now"
echo "  Logs:   tail -f $PROJECT_DIR/logs/stdout.log"
echo "  Stop:   launchctl stop $SERVICE"
echo "  Status: launchctl list $SERVICE"
echo ""
echo "  Next: send /myid to your bot, fill ADMIN_CHAT_ID in .env,"
echo "        then run this script again to restart with alerts enabled."
echo "================================"
echo ""
