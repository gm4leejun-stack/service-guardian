#!/bin/bash
# install.sh — install ai-supervisor as a LaunchAgent

set -e

PLIST_SRC="$(cd "$(dirname "$0")" && pwd)/com.ai-supervisor.plist"
PLIST_DST="$HOME/Library/LaunchAgents/com.ai-supervisor.plist"

echo "Installing ai-supervisor LaunchAgent..."

# Stop if already loaded
launchctl stop com.ai-supervisor 2>/dev/null || true
launchctl unload "$PLIST_DST" 2>/dev/null || true

# Copy plist
cp "$PLIST_SRC" "$PLIST_DST"
chmod 644 "$PLIST_DST"

# Load
launchctl load "$PLIST_DST"
launchctl start com.ai-supervisor

echo "✅ ai-supervisor installed and started"
echo "   Logs: ~/ai-supervisor/logs/"
echo "   Status: launchctl list com.ai-supervisor"
