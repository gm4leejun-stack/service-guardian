#!/bin/bash
# install.sh — install ai-supervisor as a LaunchAgent
#
# Generates the plist dynamically from the actual project directory,
# so it works correctly on any machine or username.

set -e

PROJECT_DIR="$(cd "$(dirname "$0")" && pwd)"
PLIST_DST="$HOME/Library/LaunchAgents/com.ai-supervisor.plist"
SERVICE="com.ai-supervisor"
VENV_PYTHON="$PROJECT_DIR/venv/bin/python"

echo "Installing ai-supervisor from: $PROJECT_DIR"

# Stop if already running
launchctl stop "$SERVICE" 2>/dev/null || true
launchctl unload "$PLIST_DST" 2>/dev/null || true

# Create logs directory
mkdir -p "$PROJECT_DIR/logs"

# Generate plist with correct paths for this machine
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
        <string>$HOME/.nvm/versions/node/v22.22.0/bin:$HOME/.local/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin</string>
        <key>HOME</key>
        <string>$HOME</string>
    </dict>
</dict>
</plist>
EOF

chmod 644 "$PLIST_DST"

# Load and start
launchctl load "$PLIST_DST"
launchctl start "$SERVICE"

echo "✅ ai-supervisor installed and started"
echo "   Project:  $PROJECT_DIR"
echo "   Service:  $SERVICE"
echo "   Logs:     $PROJECT_DIR/logs/"
echo "   Status:   launchctl list $SERVICE"
