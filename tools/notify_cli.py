"""
notify_cli.py — CLI wrapper for Claude Code to send Telegram notifications.

Usage (called via Bash by Claude Code):
    python3 /Users/lijunsheng/ai-supervisor/tools/notify_cli.py "消息内容" <chat_id>
"""
from __future__ import annotations

import sys
import os
import urllib.request
import urllib.parse
import json
from pathlib import Path


def _load_env() -> None:
    env_file = Path(__file__).parent.parent / ".env"
    if not env_file.exists():
        return
    for line in env_file.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, val = line.partition("=")
        key = key.strip()
        val = val.strip().strip('"').strip("'")
        if key not in os.environ:
            os.environ[key] = val


def main() -> None:
    _load_env()
    if len(sys.argv) < 3:
        print("Usage: notify_cli.py <message> <chat_id>", file=sys.stderr)
        sys.exit(1)

    message = sys.argv[1]
    chat_id = sys.argv[2]
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "")
    if not token:
        print("No TELEGRAM_BOT_TOKEN", file=sys.stderr)
        sys.exit(1)

    payload = json.dumps({"chat_id": chat_id, "text": message}).encode()
    req = urllib.request.Request(
        f"https://api.telegram.org/bot{token}/sendMessage",
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            resp.read()
        print("OK")
    except Exception as e:
        print(f"Failed: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
