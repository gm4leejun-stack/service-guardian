"""
main.py — entry point that runs both the Telegram bot and watchdog in parallel.

Usage:
  python main.py          — run bot + watchdog
  python main.py --bot    — bot only (no watchdog)
  python main.py --watchdog — watchdog only (no bot)
"""
from __future__ import annotations

import argparse
import logging
import sys
import threading
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent))

# Setup logging early
log_dir = Path(__file__).parent / "logs"
log_dir.mkdir(exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[
        logging.FileHandler(str(log_dir / "supervisor.log")),
        logging.StreamHandler(),
    ],
)

logger = logging.getLogger(__name__)


def run_watchdog_background() -> threading.Thread:
    """Run watchdog in a background daemon thread."""
    from watchdog import run_watchdog_loop

    logger.info("Starting watchdog in background thread")
    t = threading.Thread(target=run_watchdog_loop, daemon=True, name="watchdog")
    t.start()
    return t


def run_bot() -> None:
    """Start the Telegram bot (blocking). Bot instance is injected into notify_tools."""
    from bot.telegram_bot import main as bot_main
    bot_main()


def main() -> None:
    parser = argparse.ArgumentParser(description="AI Supervisor")
    parser.add_argument("--bot", action="store_true", help="Bot only (no watchdog)")
    parser.add_argument("--watchdog", action="store_true", help="Watchdog only (no bot)")
    args = parser.parse_args()

    if args.watchdog:
        logger.info("Running watchdog only")
        from watchdog import run_watchdog_loop
        run_watchdog_loop()
        return

    if not args.bot:
        # Default: run both
        run_watchdog_background()

    logger.info("Starting Telegram bot (Agent mode)")
    run_bot()


if __name__ == "__main__":
    main()
