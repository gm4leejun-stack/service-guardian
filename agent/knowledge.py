"""
agent/knowledge.py — 经验库核心逻辑。

Local I/O: knowledge_cache.json (gitignored), knowledge_pending.json (gitignored)
GitHub sync: via REST API to 'knowledge' branch in GITHUB_REPO
"""
from __future__ import annotations

import json
import logging
import threading
import uuid
from datetime import datetime, timezone, timedelta
from pathlib import Path

from config import settings

logger = logging.getLogger(__name__)

KNOWLEDGE_CACHE_PATH = Path(__file__).parent / "knowledge_cache.json"
KNOWLEDGE_PENDING_PATH = Path(__file__).parent / "knowledge_pending.json"

_lock = threading.Lock()

TZ = timezone(timedelta(hours=8))


def load_knowledge_cache() -> list[dict]:
    """Load all entries from local cache. Returns [] on missing/corrupt file."""
    if not KNOWLEDGE_CACHE_PATH.exists():
        return []
    try:
        return json.loads(KNOWLEDGE_CACHE_PATH.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return []


def _save_knowledge_cache(entries: list[dict]) -> None:
    """Overwrite local cache. Caller must hold _lock."""
    KNOWLEDGE_CACHE_PATH.write_text(
        json.dumps(entries, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def append_entry(entry: dict) -> None:
    """Append one entry to local cache (thread-safe)."""
    with _lock:
        entries = load_knowledge_cache()
        entries.append(entry)
        _save_knowledge_cache(entries)


def delete_entry(entry_id: str) -> bool:
    """Remove entry by id from local cache. Returns True if found."""
    with _lock:
        entries = load_knowledge_cache()
        new_entries = [e for e in entries if e.get("id") != entry_id]
        if len(new_entries) == len(entries):
            return False
        _save_knowledge_cache(new_entries)
    return True


def make_entry(
    tags: list[str],
    symptoms: list[str],
    root_cause: str,
    solution: str,
    source: str = "auto",
    affected_versions: list[str] | None = None,
) -> dict:
    """Create a new knowledge entry dict with generated id and timestamp."""
    return {
        "id": str(uuid.uuid4())[:8],
        "time": datetime.now(TZ).isoformat(),
        "machine": settings.MACHINE_NAME or "unknown",
        "source": source,
        "tags": tags,
        "affected_versions": affected_versions or [],
        "symptoms": symptoms,
        "root_cause": root_cause,
        "solution": solution,
    }
