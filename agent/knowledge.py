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


# ---------------------------------------------------------------------------
# Haiku extraction
# ---------------------------------------------------------------------------

_EXTRACT_PROMPT = """你是知识库整理专家。分析以下对话历史，判断是否有值得保存的问题解决经验。

判断标准（满足任一即保存）：
- 根因不显而易见（不是简单"重启"）
- 涉及版本变更、配置变化、特定软件行为
- 用户提供了外部文档或特殊解决方案

只记录最终被用户确认有效的解法，排除所有失败尝试。
去除路径中的用户名、IP地址、凭据，但保留命令、版本号、配置键名。

返回严格的 JSON（无其他文字）：
{{
  "worth_saving": true/false,
  "tags": ["关键词1", "关键词2"],
  "affected_versions": [],
  "symptoms": ["现象1", "现象2"],
  "root_cause": "一句话根本原因",
  "solution": "完整可执行的解决方案，含具体命令"
}}

对话历史：
{conversation}"""


def _parse_haiku_json(raw: str) -> dict | None:
    """Parse Haiku JSON output. Returns None if not worth saving or parse error."""
    import re
    raw = re.sub(r"```(?:json)?\s*|\s*```", "", raw).strip()
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return None
    if not data.get("worth_saving"):
        return None
    required = {"tags", "symptoms", "root_cause", "solution"}
    if not required.issubset(data.keys()):
        return None
    return data


async def extract_knowledge_from_conversation(
    history: list[tuple[str, str]],
) -> dict | None:
    """Use Haiku to extract a knowledge entry from conversation history."""
    import anthropic as _anthropic
    if not history:
        return None

    lines = []
    for user_msg, assistant_resp in history[-10:]:
        lines.append(f"用户: {user_msg[:300]}")
        lines.append(f"助手: {assistant_resp[:500]}")
    conversation = "\n".join(lines)

    prompt = _EXTRACT_PROMPT.format(conversation=conversation)

    client = _anthropic.AsyncAnthropic(
        api_key=settings.ANTHROPIC_API_KEY,
        base_url=settings.ANTHROPIC_BASE_URL,
    )
    try:
        msg = await client.messages.create(
            model=settings.HAIKU_MODEL,
            max_tokens=400,
            messages=[{"role": "user", "content": prompt}],
        )
        raw = msg.content[0].text.strip()
    except Exception as e:
        logger.warning("Haiku knowledge extraction failed: %s", e)
        return None

    parsed = _parse_haiku_json(raw)
    if parsed is None:
        return None

    return make_entry(
        tags=parsed.get("tags", []),
        symptoms=parsed.get("symptoms", []),
        root_cause=parsed["root_cause"],
        solution=parsed["solution"],
        source="auto",
        affected_versions=parsed.get("affected_versions", []),
    )


async def extract_knowledge_from_text(raw_text: str) -> dict | None:
    """Use Haiku to structure user-provided text into a knowledge entry (/remember path)."""
    import anthropic as _anthropic

    prompt = f"""将以下内容整理为结构化知识条目。返回严格 JSON（无其他文字）：
{{
  "worth_saving": true,
  "tags": ["关键词1", "关键词2"],
  "affected_versions": [],
  "symptoms": ["现象描述"],
  "root_cause": "根本原因",
  "solution": "解决方案（含具体命令）"
}}

内容：
{raw_text[:1000]}"""

    client = _anthropic.AsyncAnthropic(
        api_key=settings.ANTHROPIC_API_KEY,
        base_url=settings.ANTHROPIC_BASE_URL,
    )
    try:
        msg = await client.messages.create(
            model=settings.HAIKU_MODEL,
            max_tokens=400,
            messages=[{"role": "user", "content": prompt}],
        )
        raw = msg.content[0].text.strip()
    except Exception as e:
        logger.warning("Haiku manual knowledge extraction failed: %s", e)
        return None

    parsed = _parse_haiku_json(raw)
    if parsed is None:
        return None

    return make_entry(
        tags=parsed.get("tags", []),
        symptoms=parsed.get("symptoms", []),
        root_cause=parsed["root_cause"],
        solution=parsed["solution"],
        source="manual",
        affected_versions=parsed.get("affected_versions", []),
    )


# ---------------------------------------------------------------------------
# GitHub API sync
# ---------------------------------------------------------------------------

_GITHUB_BRANCH = "knowledge"
_GITHUB_FILE = "knowledge_base.json"


def _parse_github_coords() -> tuple[str, str] | None:
    """Extract (owner, repo) from settings.GITHUB_REPO URL. Returns None if not configured."""
    import re
    url = settings.GITHUB_REPO or ""
    m = re.search(r"github\.com[/:]([^/]+)/([^/.\s]+)", url)
    if not m:
        return None
    return m.group(1), m.group(2).removesuffix(".git")


def _github_headers() -> dict:
    return {
        "Authorization": f"token {settings.GITHUB_TOKEN}",
        "Accept": "application/vnd.github.v3+json",
        "Content-Type": "application/json",
    }


def sync_from_github() -> None:
    """Fetch knowledge_base.json from GitHub knowledge branch, merge into local cache."""
    if not settings.GITHUB_TOKEN or not settings.GITHUB_REPO:
        return
    coords = _parse_github_coords()
    if not coords:
        return
    owner, repo = coords

    import base64
    import requests as req
    url = f"https://api.github.com/repos/{owner}/{repo}/contents/{_GITHUB_FILE}"
    try:
        resp = req.get(url, headers=_github_headers(), params={"ref": _GITHUB_BRANCH}, timeout=10)
        if resp.status_code == 404:
            return
        resp.raise_for_status()
        content = base64.b64decode(resp.json()["content"]).decode("utf-8")
        remote_entries: list[dict] = json.loads(content)
    except Exception as e:
        logger.warning("sync_from_github failed: %s", e)
        return

    with _lock:
        local = load_knowledge_cache()
        local_ids = {e.get("id") for e in local}
        new_entries = [e for e in remote_entries if e.get("id") not in local_ids]
        if new_entries:
            _save_knowledge_cache(local + new_entries)
            logger.info("sync_from_github: merged %d new entries", len(new_entries))


def _push_to_github_sync(entries: list[dict]) -> bool:
    """Push full entries list to GitHub knowledge branch. Returns True on success."""
    coords = _parse_github_coords()
    if not coords:
        return False
    owner, repo = coords

    import base64
    import requests as req

    api_url = f"https://api.github.com/repos/{owner}/{repo}/contents/{_GITHUB_FILE}"

    for attempt in range(3):
        try:
            get_resp = req.get(api_url, headers=_github_headers(), params={"ref": _GITHUB_BRANCH}, timeout=10)
            sha = get_resp.json().get("sha") if get_resp.status_code == 200 else None

            content_b64 = base64.b64encode(
                json.dumps(entries, ensure_ascii=False, indent=2).encode("utf-8")
            ).decode("ascii")

            body: dict = {
                "message": "data(knowledge): +1 entry",
                "content": content_b64,
                "branch": _GITHUB_BRANCH,
            }
            if sha:
                body["sha"] = sha

            put_resp = req.put(api_url, headers=_github_headers(), json=body, timeout=15)
            if put_resp.status_code in (200, 201):
                return True
            if put_resp.status_code == 409:
                logger.info("GitHub push 409 conflict, retrying (attempt %d)", attempt + 1)
                continue
            logger.warning("GitHub push failed: %s %s", put_resp.status_code, put_resp.text[:200])
            return False
        except Exception as e:
            logger.warning("GitHub push error (attempt %d): %s", attempt + 1, e)

    return False


def _add_to_pending(entry: dict) -> None:
    """Add entry to pending sync queue."""
    with _lock:
        try:
            pending = json.loads(KNOWLEDGE_PENDING_PATH.read_text(encoding="utf-8")) if KNOWLEDGE_PENDING_PATH.exists() else []
        except (json.JSONDecodeError, OSError):
            pending = []
        pending.append(entry)
        KNOWLEDGE_PENDING_PATH.write_text(json.dumps(pending, ensure_ascii=False, indent=2), encoding="utf-8")


def _flush_pending() -> None:
    """Try to push any pending entries to GitHub."""
    if not KNOWLEDGE_PENDING_PATH.exists():
        return
    try:
        pending = json.loads(KNOWLEDGE_PENDING_PATH.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return
    if not pending:
        return

    with _lock:
        local = load_knowledge_cache()
        local_ids = {e.get("id") for e in local}
        for e in pending:
            if e.get("id") not in local_ids:
                local.append(e)
        _save_knowledge_cache(local)

    if _push_to_github_sync(local):
        KNOWLEDGE_PENDING_PATH.unlink(missing_ok=True)
        logger.info("Flushed %d pending knowledge entries to GitHub", len(pending))


async def push_entry_async(entry: dict) -> None:
    """Append entry to local cache and async-push to GitHub (non-blocking)."""
    append_entry(entry)
    if not settings.GITHUB_TOKEN or not settings.GITHUB_REPO:
        return

    import asyncio
    loop = asyncio.get_event_loop()

    def _push():
        _flush_pending()
        with _lock:
            all_entries = load_knowledge_cache()
        ok = _push_to_github_sync(all_entries)
        if not ok:
            _add_to_pending(entry)

    loop.run_in_executor(None, _push)
