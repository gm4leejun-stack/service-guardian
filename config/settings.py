"""Central configuration for ai-supervisor."""
import os
from pathlib import Path
from dotenv import load_dotenv

# Load .env from project root
_project_root = Path(__file__).parent.parent
load_dotenv(_project_root / ".env")

# Paths
HOME = Path.home()
SUPERVISOR_DIR = _project_root
LOG_DIR = SUPERVISOR_DIR / "logs"
LOG_DIR.mkdir(exist_ok=True)

# Claude CLI
CLAUDE_BIN = str(HOME / ".local/bin/claude")

# Anthropic API
ANTHROPIC_BASE_URL = os.environ.get("ANTHROPIC_BASE_URL", "https://llmapi.lovbrowser.com")
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
CLAUDE_MODEL = os.environ.get("CLAUDE_MODEL", "claude-sonnet-4-6")
HAIKU_MODEL = os.environ.get("HAIKU_MODEL", "claude-haiku-4-5-20251001")

# Telegram
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
ADMIN_CHAT_ID_STR = os.environ.get("ADMIN_CHAT_ID", "")
ADMIN_CHAT_ID: int | None = int(ADMIN_CHAT_ID_STR) if ADMIN_CHAT_ID_STR.isdigit() else None
# Allowed Telegram user IDs (empty = allow all)
ALLOWED_USERS: list[int] = []

# OpenClaw
OPENCLAW_SERVICE = "ai.openclaw.gateway"
NANOCLAW_SERVICE = "com.nanoclaw"
OPENCLAW_LOG = str(HOME / ".openclaw/logs/gateway.log")
OPENCLAW_ERR_LOG = str(HOME / ".openclaw/logs/gateway.err.log")
OPENCLAW_TMP_LOG_DIR = "/tmp/openclaw"

# Shell safety: commands blocked in shell_worker
SHELL_BLOCKED_PATTERNS = [
    r"rm\s+-rf\s+/",
    r"mkfs",
    r"dd\s+if=/dev",
    r":(){ :|:& };:",  # fork bomb
    r">\s+/dev/sd",
]

# Watchdog
WATCHDOG_CHECK_INTERVAL = 60  # seconds
WATCHDOG_FREEZE_THRESHOLD = 120  # seconds of no log activity = frozen
WATCHDOG_QUIET_HOURS_START = 0   # 0:00
WATCHDOG_QUIET_HOURS_END = 8     # 8:00

# LangGraph
LANGGRAPH_RECURSION_LIMIT = 20
