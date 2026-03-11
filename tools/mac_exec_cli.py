"""
tools/mac_exec_cli.py — Run Mac commands from inside NanoClaw Docker containers.

Connects to ai-supervisor's exec bridge via host.docker.internal.
Claude Code in NanoClaw calls this script via Bash to execute commands
that require native macOS access (launchctl, ps, etc.).

Usage (from inside NanoClaw container):
    python3 /workspace/extra/ai-supervisor/tools/mac_exec_cli.py "launchctl list com.ai-supervisor"
    python3 /workspace/extra/ai-supervisor/tools/mac_exec_cli.py "launchctl stop com.nanoclaw && sleep 2 && launchctl start com.nanoclaw" --timeout 60

Exit codes:
    0  — command ran successfully (check stdout/stderr for command result)
    1  — bridge unreachable or auth error
"""
from __future__ import annotations

import json
import os
import sys
import urllib.request
import urllib.error
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

    if len(sys.argv) < 2:
        print("Usage: mac_exec_cli.py <command> [--timeout N]", file=sys.stderr)
        sys.exit(1)

    cmd = sys.argv[1]
    timeout = 30
    if "--timeout" in sys.argv:
        idx = sys.argv.index("--timeout")
        if idx + 1 < len(sys.argv):
            try:
                timeout = int(sys.argv[idx + 1])
            except ValueError:
                pass

    token = os.environ.get("EXEC_BRIDGE_TOKEN", "")
    port = os.environ.get("EXEC_BRIDGE_PORT", "18800")

    if not token:
        print("Error: EXEC_BRIDGE_TOKEN not set in .env", file=sys.stderr)
        sys.exit(1)

    payload = json.dumps({"cmd": cmd, "timeout": timeout}).encode()
    req = urllib.request.Request(
        f"http://host.docker.internal:{port}/exec",
        data=payload,
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {token}",
        },
        method="POST",
    )

    try:
        with urllib.request.urlopen(req, timeout=timeout + 5) as resp:
            data = json.loads(resp.read())
    except urllib.error.HTTPError as e:
        body = e.read().decode()
        print(f"Bridge error {e.code}: {body}", file=sys.stderr)
        sys.exit(1)
    except Exception as e:
        print(f"Cannot reach exec bridge: {e}", file=sys.stderr)
        print("Make sure ai-supervisor is running on the Mac host.", file=sys.stderr)
        sys.exit(1)

    if data.get("stdout"):
        print(data["stdout"], end="")
    if data.get("stderr"):
        print(data["stderr"], end="", file=sys.stderr)

    rc = data.get("returncode", 0)
    if rc != 0:
        sys.exit(rc)


if __name__ == "__main__":
    main()
