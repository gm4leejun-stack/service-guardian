"""
shell_worker.py — executes shell commands safely with a blocklist.
"""
import re
import subprocess
import logging
import shlex
from pathlib import Path
from config.settings import SHELL_BLOCKED_PATTERNS

logger = logging.getLogger(__name__)

MAX_OUTPUT_CHARS = 3500
COMMAND_TIMEOUT = 60  # seconds


def _is_blocked(command: str) -> str | None:
    """Return the matched pattern if the command is blocked, else None."""
    for pattern in SHELL_BLOCKED_PATTERNS:
        if re.search(pattern, command, re.IGNORECASE):
            return pattern
    return None


def run_shell(command: str, cwd: str | None = None) -> dict:
    """
    Execute a shell command safely.

    Args:
        command: Shell command string
        cwd: Working directory (defaults to home)

    Returns:
        dict with: success, output, error, returncode
    """
    if not command or not command.strip():
        return {"success": False, "output": "", "error": "Empty command", "returncode": -1}

    blocked = _is_blocked(command)
    if blocked:
        logger.warning("Blocked command: %s (matched pattern: %s)", command, blocked)
        return {
            "success": False,
            "output": "",
            "error": f"Command blocked by safety filter (pattern: {blocked})",
            "returncode": -1,
        }

    working_dir = cwd or str(Path.home())
    logger.info("Running shell command: %s (cwd=%s)", command, working_dir)

    try:
        result = subprocess.run(
            command,
            shell=True,
            capture_output=True,
            text=True,
            timeout=COMMAND_TIMEOUT,
            cwd=working_dir,
        )

        stdout = result.stdout.strip()
        stderr = result.stderr.strip()

        # Combine output
        output = stdout
        if stderr and result.returncode != 0:
            output = (stdout + "\n" + stderr).strip() if stdout else stderr

        if len(output) > MAX_OUTPUT_CHARS:
            output = output[:MAX_OUTPUT_CHARS] + "\n...[truncated]"

        return {
            "success": result.returncode == 0,
            "output": output or "(no output)",
            "error": stderr if result.returncode != 0 else "",
            "returncode": result.returncode,
        }

    except subprocess.TimeoutExpired:
        logger.warning("Shell command timed out: %s", command)
        return {
            "success": False,
            "output": "",
            "error": f"Command timed out after {COMMAND_TIMEOUT}s",
            "returncode": -1,
        }
    except Exception as e:
        logger.exception("Shell worker error")
        return {"success": False, "output": "", "error": str(e), "returncode": -1}


def check_process(name: str) -> dict:
    """Check if a process with the given name is running."""
    result = run_shell(f"pgrep -fl {shlex.quote(name)}")
    running = result["success"] and bool(result["output"].strip())
    return {
        "running": running,
        "processes": result["output"] if running else "",
    }
