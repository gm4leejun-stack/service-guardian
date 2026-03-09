"""
log_worker.py — reads log files from OpenClaw and system.
"""
import os
import glob
import logging
import subprocess
from pathlib import Path
from datetime import datetime, date
from config.settings import OPENCLAW_LOG, OPENCLAW_ERR_LOG, OPENCLAW_TMP_LOG_DIR

logger = logging.getLogger(__name__)

MAX_CHARS = 3500


def _tail_file(path: str, n: int = 30) -> str:
    """Return last n lines of a file, or error message."""
    p = Path(path)
    if not p.exists():
        return f"(file not found: {path})"
    try:
        result = subprocess.run(
            ["tail", f"-{n}", str(p)], capture_output=True, text=True, timeout=10
        )
        return result.stdout.strip() or "(empty)"
    except Exception as e:
        return f"Error: {e}"


def get_openclaw_logs(lines: int = 40) -> str:
    """Return recent lines from openclaw gateway log."""
    return _tail_file(OPENCLAW_LOG, lines)


def get_openclaw_errors(lines: int = 30) -> str:
    """Return recent lines from openclaw error log."""
    return _tail_file(OPENCLAW_ERR_LOG, lines)


def get_tmp_logs(lines: int = 30) -> str:
    """
    Return latest log from /tmp/openclaw/ directory.
    Files are named openclaw-YYYY-MM-DD.log
    """
    tmp_dir = Path(OPENCLAW_TMP_LOG_DIR)
    if not tmp_dir.exists():
        return f"(directory not found: {OPENCLAW_TMP_LOG_DIR})"

    log_files = sorted(tmp_dir.glob("openclaw-*.log"), reverse=True)
    if not log_files:
        return "(no log files found in /tmp/openclaw/)"

    latest = log_files[0]
    content = _tail_file(str(latest), lines)
    return f"[{latest.name}]\n{content}"


def get_supervisor_log(lines: int = 30) -> str:
    """Return recent lines from this supervisor's own log."""
    log_dir = Path(__file__).parent.parent / "logs"
    log_file = log_dir / "supervisor.log"
    return _tail_file(str(log_file), lines)


def search_logs(keyword: str, log_source: str = "openclaw") -> str:
    """
    Search for a keyword in the specified log source.
    log_source: openclaw | errors | tmp | all
    """
    if not keyword:
        return "No keyword provided"

    sources = []
    if log_source in ("openclaw", "all"):
        sources.append(OPENCLAW_LOG)
    if log_source in ("errors", "all"):
        sources.append(OPENCLAW_ERR_LOG)
    if log_source in ("tmp", "all"):
        tmp_dir = Path(OPENCLAW_TMP_LOG_DIR)
        if tmp_dir.exists():
            log_files = sorted(tmp_dir.glob("openclaw-*.log"), reverse=True)
            sources.extend([str(f) for f in log_files[:3]])

    results = []
    for src in sources:
        p = Path(src)
        if not p.exists():
            continue
        try:
            result = subprocess.run(
                ["grep", "-n", "-i", keyword, str(p)],
                capture_output=True,
                text=True,
                timeout=10,
            )
            if result.stdout.strip():
                lines = result.stdout.strip().splitlines()[-20:]  # last 20 matches
                results.append(f"[{p.name}]\n" + "\n".join(lines))
        except Exception as e:
            results.append(f"[{p.name}] Error: {e}")

    if not results:
        return f"No matches for '{keyword}'"

    output = "\n\n".join(results)
    if len(output) > MAX_CHARS:
        output = output[:MAX_CHARS] + "\n...[truncated]"
    return output


def get_log_summary() -> str:
    """Return a summary of available logs and their sizes."""
    lines = ["Log Summary:"]

    paths = [
        ("openclaw gateway", OPENCLAW_LOG),
        ("openclaw errors", OPENCLAW_ERR_LOG),
    ]

    for label, path in paths:
        p = Path(path)
        if p.exists():
            size = p.stat().st_size
            mtime = datetime.fromtimestamp(p.stat().st_mtime).strftime("%Y-%m-%d %H:%M:%S")
            lines.append(f"  {label}: {size:,} bytes, modified {mtime}")
        else:
            lines.append(f"  {label}: not found")

    # tmp logs
    tmp_dir = Path(OPENCLAW_TMP_LOG_DIR)
    if tmp_dir.exists():
        log_files = sorted(tmp_dir.glob("openclaw-*.log"), reverse=True)
        if log_files:
            lines.append(f"  /tmp/openclaw/: {len(log_files)} files, latest: {log_files[0].name}")
        else:
            lines.append("  /tmp/openclaw/: empty")
    else:
        lines.append("  /tmp/openclaw/: not found")

    return "\n".join(lines)
