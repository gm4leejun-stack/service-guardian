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


# ---------------------------------------------------------------------------
# Keyword search
# ---------------------------------------------------------------------------

_STOPWORDS = {
    '的', '了', '在', '是', '我', '你', '他', '她', '它', '和', '有', '这', '那', '也',
    '后', '时', '被', '从', '到', '于', '把', '让', '对', '与', '但',
    'the', 'a', 'an', 'is', 'in', 'on', 'at', 'to', 'for', 'of', 'and', 'or',
}


def extract_keywords(query: str) -> list[str]:
    """Extract meaningful tokens from a query (Chinese words + English alphanumeric)."""
    import re
    tokens = re.findall(r'[\u4e00-\u9fff]{2,}|[a-zA-Z][a-zA-Z0-9._-]{1,}', query)
    return [t for t in tokens if t.lower() not in _STOPWORDS]


def search_knowledge(query: str, top_k: int = 3) -> list[dict]:
    """Return top-k entries matching query keywords. Returns [] if none match."""
    keywords = extract_keywords(query)
    if not keywords:
        return []

    entries = load_knowledge_cache()
    scored: list[tuple[int, dict]] = []

    for entry in entries:
        search_text = " ".join([
            *entry.get("tags", []),
            *entry.get("symptoms", []),
            entry.get("root_cause", ""),
            entry.get("solution", ""),
        ]).lower()
        score = sum(1 for kw in keywords if kw.lower() in search_text)
        if score > 0:
            scored.append((score, entry))

    scored.sort(key=lambda x: x[0], reverse=True)
    return [e for _, e in scored[:top_k]]


def format_search_result_for_prompt(results: list[dict]) -> str:
    """Format search results for injection into claude subprocess prompt."""
    if not results:
        return ""
    lines = ["[经验库参考]"]
    for i, e in enumerate(results, 1):
        lines.append(f"{i}. 症状：{'、'.join(e.get('symptoms', []))}")
        lines.append(f"   根因：{e.get('root_cause', '')}")
        lines.append(f"   解法：{e.get('solution', '')}")
        if e.get("affected_versions"):
            lines.append(f"   版本：{', '.join(e['affected_versions'])}")
    lines.append("")
    return "\n".join(lines)


def format_search_summary_for_user(results: list[dict]) -> str:
    """One-line summary for display to Telegram user."""
    if not results:
        return ""
    top = results[0]
    summary = top.get("root_cause", "")[:40]
    return f"📚 参考了 {len(results)} 条历史经验（{summary}）\n"
