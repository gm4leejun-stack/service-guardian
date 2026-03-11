"""
tools/exec_bridge.py — Mac command execution bridge for NanoClaw containers.

Runs a lightweight HTTP server on localhost:EXEC_BRIDGE_PORT.
NanoClaw's Claude Code calls this via host.docker.internal to execute
Mac commands (launchctl, ps, etc.) that are unavailable inside Docker.

Security:
- Binds to 127.0.0.1 only (Mac-local, not exposed to network)
- Bearer token authentication (EXEC_BRIDGE_TOKEN from .env)
- Same SHELL_BLOCKED_PATTERNS as run_shell_command

API:
  POST /exec
  Authorization: Bearer <token>
  Content-Type: application/json
  {"cmd": "launchctl list com.ai-supervisor", "timeout": 30}

  Response 200: {"stdout": "...", "stderr": "...", "returncode": 0}
  Response 401: {"error": "unauthorized"}
  Response 400: {"error": "missing cmd"}
  Response 500: {"error": "...", "stderr": "..."}
"""
from __future__ import annotations

import json
import logging
import re
import subprocess
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

logger = logging.getLogger(__name__)

_BLOCKED = [
    r"rm\s+-rf\s+/",
    r"mkfs",
    r"dd\s+if=/dev",
    r":(){ :|:& };:",
    r">\s+/dev/sd",
]


def _is_blocked(cmd: str) -> bool:
    return any(re.search(p, cmd) for p in _BLOCKED)


def _make_handler(token: str):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, fmt, *args):
            logger.debug("[exec_bridge] %s", fmt % args)

        def _send_json(self, code: int, data: dict) -> None:
            body = json.dumps(data).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_POST(self):
            if self.path != "/exec":
                self._send_json(404, {"error": "not found"})
                return

            # Auth
            auth = self.headers.get("Authorization", "")
            if auth != f"Bearer {token}":
                logger.warning("[exec_bridge] unauthorized request from %s", self.client_address)
                self._send_json(401, {"error": "unauthorized"})
                return

            # Parse body
            length = int(self.headers.get("Content-Length", 0))
            try:
                body = json.loads(self.rfile.read(length))
            except Exception:
                self._send_json(400, {"error": "invalid json"})
                return

            cmd = body.get("cmd", "").strip()
            if not cmd:
                self._send_json(400, {"error": "missing cmd"})
                return

            timeout = min(int(body.get("timeout", 30)), 120)

            if _is_blocked(cmd):
                logger.warning("[exec_bridge] blocked command: %s", cmd[:80])
                self._send_json(403, {"error": "command blocked by safety filter"})
                return

            logger.info("[exec_bridge] exec: %s", cmd[:120])
            try:
                result = subprocess.run(
                    cmd,
                    shell=True,
                    capture_output=True,
                    text=True,
                    timeout=timeout,
                )
                self._send_json(200, {
                    "stdout": result.stdout,
                    "stderr": result.stderr,
                    "returncode": result.returncode,
                })
            except subprocess.TimeoutExpired:
                self._send_json(200, {
                    "stdout": "",
                    "stderr": f"command timed out after {timeout}s",
                    "returncode": -1,
                })
            except Exception as e:
                self._send_json(500, {"error": str(e), "stderr": ""})

    return Handler


def start_bridge(port: int, token: int) -> None:
    """Start the exec bridge HTTP server in a daemon thread."""
    if not token:
        logger.warning("[exec_bridge] EXEC_BRIDGE_TOKEN not set — bridge disabled")
        return

    handler = _make_handler(token)
    server = HTTPServer(("127.0.0.1", port), handler)

    def _serve():
        logger.info("[exec_bridge] listening on http://127.0.0.1:%d/exec", port)
        server.serve_forever()

    t = threading.Thread(target=_serve, daemon=True, name="exec_bridge")
    t.start()
