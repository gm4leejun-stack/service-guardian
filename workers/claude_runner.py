"""
claude_runner.py — runs claude CLI for coding tasks.
Receives a task description, spawns claude CLI with --print flag,
returns the output.
"""
import subprocess
import shlex
import logging
from pathlib import Path
from config.settings import CLAUDE_BIN

logger = logging.getLogger(__name__)

MAX_OUTPUT_CHARS = 4000  # Telegram message limit safety


def run_claude_task(task: str, cwd: str | None = None) -> dict:
    """
    Run a Claude Code CLI task.

    Args:
        task: Natural language task description
        cwd: Working directory (defaults to home)

    Returns:
        dict with keys: success, output, error
    """
    if not task or not task.strip():
        return {"success": False, "output": "", "error": "Empty task"}

    claude_path = Path(CLAUDE_BIN)
    if not claude_path.exists():
        return {
            "success": False,
            "output": "",
            "error": f"Claude CLI not found at {CLAUDE_BIN}",
        }

    working_dir = cwd or str(Path.home())

    cmd = [
        str(claude_path),
        "--print",
        "--dangerously-skip-permissions",
    ]

    logger.info("Running claude task: %s (cwd=%s)", task[:80], working_dir)

    try:
        result = subprocess.run(
            cmd,
            input=task.strip(),
            capture_output=True,
            text=True,
            timeout=300,  # 5 minute timeout
            cwd=working_dir,
        )

        stdout = result.stdout.strip()
        stderr = result.stderr.strip()

        if result.returncode == 0:
            output = stdout or "(no output)"
            # Truncate if too long
            if len(output) > MAX_OUTPUT_CHARS:
                output = output[:MAX_OUTPUT_CHARS] + "\n...[truncated]"
            return {"success": True, "output": output, "error": ""}
        else:
            error_msg = stderr or stdout or f"Exit code {result.returncode}"
            if len(error_msg) > MAX_OUTPUT_CHARS:
                error_msg = error_msg[:MAX_OUTPUT_CHARS] + "\n...[truncated]"
            return {"success": False, "output": stdout, "error": error_msg}

    except subprocess.TimeoutExpired:
        logger.warning("Claude task timed out: %s", task[:80])
        return {"success": False, "output": "", "error": "Task timed out after 5 minutes"}
    except Exception as e:
        logger.exception("Claude runner error")
        return {"success": False, "output": "", "error": str(e)}
