# 经验库 Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 为龙虾医生添加跨机器共享的经验知识库，自动提取问题解决经验，检索优先参考，通过 GitHub API 跨机同步。

**Architecture:** 新增 `agent/knowledge.py` 承载全部知识库逻辑（本地 JSON 读写、关键词检索、Haiku 提取、GitHub API 同步）。`brain.py` 在任务开始时注入相关经验、在用户确认解决时触发自动提取。`telegram_bot.py` 新增 `/remember`、`/knowledge` 指令。

**Tech Stack:** Python 3.14, anthropic SDK (Haiku), requests (GitHub API), asyncio

**Spec:** `docs/superpowers/specs/2026-03-24-knowledge-base-design.md`

---

## Chunk 1: Foundation — Task 1 (config) + Task 2 (local I/O)

### Task 1: 配置与依赖

**Files:**
- Modify: `config/settings.py`
- Modify: `requirements.txt`

- [ ] **Step 1: 在 requirements.txt 加入 requests**

```
requests>=2.31.0
```

- [ ] **Step 2: 在 settings.py 加入 GITHUB_TOKEN**

在 `GITHUB_REPO` 那行下方加入：

```python
GITHUB_TOKEN = os.environ.get("GITHUB_TOKEN", "")   # optional; enables cross-machine knowledge sync
```

- [ ] **Step 3: 安装新依赖**

```bash
source venv/bin/activate && pip install requests>=2.31.0
```

Expected: Successfully installed requests

- [ ] **Step 4: 验证 import**

```bash
source venv/bin/activate && python3 -c "from config import settings; print(settings.GITHUB_TOKEN)"
```

Expected: 输出空字符串（未配置时）

- [ ] **Step 5: Commit**

```bash
git add requirements.txt config/settings.py
git commit -m "feat(knowledge): add GITHUB_TOKEN config and requests dependency"
```

---

### Task 2: agent/knowledge.py — 数据模型与本地 I/O

**Files:**
- Create: `agent/knowledge.py`
- Create: `tests/test_knowledge.py`

- [ ] **Step 1: 创建 tests/ 目录并写失败测试**

```bash
mkdir -p tests && touch tests/__init__.py
```

创建 `tests/test_knowledge.py`：

```python
"""Tests for agent/knowledge.py — local I/O and data model."""
import json, pytest
from pathlib import Path


def test_append_and_load(tmp_path, monkeypatch):
    """append_entry writes to cache; load_knowledge_cache reads it back."""
    import agent.knowledge as k
    monkeypatch.setattr(k, "KNOWLEDGE_CACHE_PATH", tmp_path / "cache.json")
    monkeypatch.setattr(k, "KNOWLEDGE_PENDING_PATH", tmp_path / "pending.json")

    entry = {
        "id": "test-id-1",
        "time": "2026-03-24T01:00:00+08:00",
        "machine": "test-machine",
        "source": "auto",
        "tags": ["openclaw", "permission"],
        "affected_versions": [],
        "symptoms": ["Bot拒绝调用工具"],
        "root_cause": "权限策略收紧",
        "solution": "openclaw config set tools.profile full",
    }
    k.append_entry(entry)

    loaded = k.load_knowledge_cache()
    assert len(loaded) == 1
    assert loaded[0]["id"] == "test-id-1"
    assert loaded[0]["root_cause"] == "权限策略收紧"


def test_delete_entry(tmp_path, monkeypatch):
    """delete_entry removes the entry with matching id."""
    import agent.knowledge as k
    monkeypatch.setattr(k, "KNOWLEDGE_CACHE_PATH", tmp_path / "cache.json")
    monkeypatch.setattr(k, "KNOWLEDGE_PENDING_PATH", tmp_path / "pending.json")

    k.append_entry({"id": "a", "tags": [], "symptoms": [], "root_cause": "r1", "solution": "s1", "source": "auto", "time": "", "machine": "", "affected_versions": []})
    k.append_entry({"id": "b", "tags": [], "symptoms": [], "root_cause": "r2", "solution": "s2", "source": "auto", "time": "", "machine": "", "affected_versions": []})

    k.delete_entry("a")
    remaining = k.load_knowledge_cache()
    assert len(remaining) == 1
    assert remaining[0]["id"] == "b"
```

- [ ] **Step 2: 运行测试确认失败**

```bash
source venv/bin/activate && python3 -m pytest tests/test_knowledge.py -v
```

Expected: FAIL — `ModuleNotFoundError: No module named 'agent.knowledge'`

- [ ] **Step 3: 创建 agent/knowledge.py 实现本地 I/O**

```python
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
```

- [ ] **Step 4: 运行测试确认通过**

```bash
source venv/bin/activate && python3 -m pytest tests/test_knowledge.py -v
```

Expected: PASS (2 tests)

- [ ] **Step 5: Commit**

```bash
git add agent/knowledge.py tests/ requirements.txt
git commit -m "feat(knowledge): local I/O — append, load, delete, make_entry"
```

---

## Chunk 2: Core Logic — Task 3 (搜索) + Task 4 (Haiku 提取)

### Task 3: agent/knowledge.py — 关键词检索

**Files:**
- Modify: `agent/knowledge.py`
- Modify: `tests/test_knowledge.py`

- [ ] **Step 1: 在 tests/test_knowledge.py 追加搜索测试**

```python
def test_search_finds_relevant(tmp_path, monkeypatch):
    """search_knowledge returns entries matching query keywords."""
    import agent.knowledge as k
    monkeypatch.setattr(k, "KNOWLEDGE_CACHE_PATH", tmp_path / "cache.json")
    monkeypatch.setattr(k, "KNOWLEDGE_PENDING_PATH", tmp_path / "pending.json")

    k.append_entry(k.make_entry(
        tags=["openclaw", "permission"],
        symptoms=["Bot拒绝调用工具", "没有权限"],
        root_cause="OpenClaw权限策略收紧",
        solution="openclaw config set tools.profile full",
    ))
    k.append_entry(k.make_entry(
        tags=["nanoclaw", "crash"],
        symptoms=["进程崩溃"],
        root_cause="内存溢出",
        solution="重启服务",
    ))

    results = k.search_knowledge("openclaw 没有权限执行工具")
    assert len(results) >= 1
    assert any("权限" in r["root_cause"] for r in results)

    # unrelated query should not match
    results2 = k.search_knowledge("磁盘空间不足")
    assert all("权限" not in r["root_cause"] for r in results2)


def test_extract_keywords():
    """extract_keywords handles Chinese and English tokens."""
    import agent.knowledge as k
    kws = k.extract_keywords("OpenClaw升级后Bot拒绝调用工具permission denied")
    assert "OpenClaw" in kws or "openclaw" in kws.lower() if isinstance(kws, str) else any("openclaw" in w.lower() for w in kws)
    assert any("拒绝" in w or "permission" in w.lower() for w in kws)
```

- [ ] **Step 2: 运行测试确认失败**

```bash
source venv/bin/activate && python3 -m pytest tests/test_knowledge.py::test_search_finds_relevant tests/test_knowledge.py::test_extract_keywords -v
```

Expected: FAIL

- [ ] **Step 3: 在 agent/knowledge.py 追加搜索逻辑**

在文件末尾加入：

```python
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
```

- [ ] **Step 4: 运行全部测试**

```bash
source venv/bin/activate && python3 -m pytest tests/test_knowledge.py -v
```

Expected: PASS (4 tests)

- [ ] **Step 5: Commit**

```bash
git add agent/knowledge.py tests/test_knowledge.py
git commit -m "feat(knowledge): keyword search — extract_keywords, search_knowledge"
```

---

### Task 4: agent/knowledge.py — Haiku 提取

**Files:**
- Modify: `agent/knowledge.py`
- Modify: `tests/test_knowledge.py`

- [ ] **Step 1: 在 tests/test_knowledge.py 追加提取测试（mock Haiku）**

```python
def test_parse_haiku_knowledge_valid():
    """_parse_haiku_json returns structured entry from valid Haiku output."""
    import agent.knowledge as k
    raw = json.dumps({
        "worth_saving": True,
        "tags": ["openclaw", "permission"],
        "affected_versions": ["2026.3.x"],
        "symptoms": ["Bot拒绝调用工具"],
        "root_cause": "权限策略收紧",
        "solution": "openclaw config set tools.profile full && openclaw gateway restart",
    })
    result = k._parse_haiku_json(raw)
    assert result is not None
    assert result["root_cause"] == "权限策略收紧"
    assert result["tags"] == ["openclaw", "permission"]


def test_parse_haiku_knowledge_not_worth_saving():
    """_parse_haiku_json returns None when worth_saving is False."""
    import agent.knowledge as k
    raw = json.dumps({"worth_saving": False})
    result = k._parse_haiku_json(raw)
    assert result is None
```

- [ ] **Step 2: 运行测试确认失败**

```bash
source venv/bin/activate && python3 -m pytest tests/test_knowledge.py::test_parse_haiku_knowledge_valid tests/test_knowledge.py::test_parse_haiku_knowledge_not_worth_saving -v
```

Expected: FAIL

- [ ] **Step 3: 在 agent/knowledge.py 追加 Haiku 提取逻辑**

```python
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
{
  "worth_saving": true/false,
  "tags": ["关键词1", "关键词2"],  // 3~6个，用于检索
  "affected_versions": [],          // 有版本依赖时填，否则空数组
  "symptoms": ["现象1", "现象2"],   // 用户观察到的现象
  "root_cause": "一句话根本原因",
  "solution": "完整可执行的解决方案，含具体命令"
}

对话历史：
{conversation}"""


def _parse_haiku_json(raw: str) -> dict | None:
    """Parse Haiku JSON output. Returns None if not worth saving or parse error."""
    import re
    # Strip markdown code blocks if present
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
    """Use Haiku to extract a knowledge entry from conversation history.
    Returns None if not worth saving or extraction fails.
    """
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
```

- [ ] **Step 4: 运行全部测试**

```bash
source venv/bin/activate && python3 -m pytest tests/test_knowledge.py -v
```

Expected: PASS (6 tests)

- [ ] **Step 5: Commit**

```bash
git add agent/knowledge.py tests/test_knowledge.py
git commit -m "feat(knowledge): Haiku extraction — auto from conversation and manual from text"
```

---

## Chunk 3: GitHub Sync — Task 5

### Task 5: agent/knowledge.py — GitHub API 同步

**Files:**
- Modify: `agent/knowledge.py`

注意：此 Task 无单元测试（依赖外部 GitHub API）。通过 Step 5 手动验证。

- [ ] **Step 1: 在 agent/knowledge.py 追加 GitHub 同步逻辑**

```python
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
    """Fetch knowledge_base.json from GitHub knowledge branch, merge into local cache.
    No-op if GITHUB_TOKEN or GITHUB_REPO not configured. Failures are silent.
    """
    if not settings.GITHUB_TOKEN or not settings.GITHUB_REPO:
        return
    coords = _parse_github_coords()
    if not coords:
        return
    owner, repo = coords

    import base64, requests as req
    url = f"https://api.github.com/repos/{owner}/{repo}/contents/{_GITHUB_FILE}"
    try:
        resp = req.get(url, headers=_github_headers(), params={"ref": _GITHUB_BRANCH}, timeout=10)
        if resp.status_code == 404:
            return  # branch or file doesn't exist yet
        resp.raise_for_status()
        content = base64.b64decode(resp.json()["content"]).decode("utf-8")
        remote_entries: list[dict] = json.loads(content)
    except Exception as e:
        logger.warning("sync_from_github failed: %s", e)
        return

    # Merge: add remote entries not already in local cache (by id)
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

    import base64, requests as req

    api_url = f"https://api.github.com/repos/{owner}/{repo}/contents/{_GITHUB_FILE}"

    # Get current sha (needed for PUT)
    for attempt in range(3):
        try:
            get_resp = req.get(api_url, headers=_github_headers(), params={"ref": _GITHUB_BRANCH}, timeout=10)
            sha = get_resp.json().get("sha") if get_resp.status_code == 200 else None

            content_b64 = base64.b64encode(
                json.dumps(entries, ensure_ascii=False, indent=2).encode("utf-8")
            ).decode("ascii")

            body: dict = {
                "message": f"data(knowledge): +1 entry",
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
    """Add entry to pending sync queue (written when GitHub push fails)."""
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
```

- [ ] **Step 2: 验证语法**

```bash
source venv/bin/activate && python3 -c "import agent.knowledge; print('OK')"
```

Expected: OK

- [ ] **Step 3: 运行全部已有测试确认不破坏**

```bash
source venv/bin/activate && python3 -m pytest tests/test_knowledge.py -v
```

Expected: PASS (6 tests)

- [ ] **Step 4: 手动验证 _parse_github_coords**

```bash
source venv/bin/activate && python3 -c "
from config import settings
settings.GITHUB_REPO = 'https://github.com/gm4leejun-stack/service-guardian'
from agent.knowledge import _parse_github_coords
print(_parse_github_coords())
"
```

Expected: `('gm4leejun-stack', 'service-guardian')`

- [ ] **Step 5: Commit**

```bash
git add agent/knowledge.py
git commit -m "feat(knowledge): GitHub API sync — push, pull, pending queue"
```

---

## Chunk 4: brain.py Integration — Task 6

### Task 6: agent/brain.py — 注入经验检索 + 触发自动提取

**Files:**
- Modify: `agent/brain.py`

- [ ] **Step 1: 在 brain.py 顶部 import knowledge**

在 `from config import settings` 下方加入：

```python
from agent import knowledge as _knowledge
```

- [ ] **Step 2: 在 run_agent() 的 clear 分支中触发知识提取**

找到 `run_agent()` 中 `should_clear_working_memory` 的处理块（约第302行），在 `save_long_term_memory` 调用之后、`working_memory.pop` 之前加入：

```python
        # --- Extract knowledge (non-blocking) ---
        if not is_watchdog:
            with _memory_lock:
                history_snapshot = list(working_memory.get(thread_id, []))
            if len(history_snapshot) >= 3:
                async def _extract_and_push():
                    entry = await _knowledge.extract_knowledge_from_conversation(history_snapshot)
                    if entry:
                        await _knowledge.push_entry_async(entry)
                        logger.info("[brain] knowledge entry saved: %s", entry.get("root_cause", "")[:60])
                asyncio.create_task(_extract_and_push())
```

- [ ] **Step 3: 在 run_agent() 的 context 构建中注入经验库**

找到 `full_task = env_ctx + history_ctx + task + notify_hint`（约第346行），改为：

```python
    # --- Knowledge search (only for new tasks: empty working memory) ---
    knowledge_ctx = ""
    if not is_watchdog:
        with _memory_lock:
            is_new_task = len(working_memory.get(thread_id, [])) == 0
        if is_new_task:
            results = _knowledge.search_knowledge(task)
            if results:
                knowledge_ctx = _knowledge.format_search_result_for_prompt(results)

    full_task = env_ctx + history_ctx + knowledge_ctx + task + notify_hint
```

- [ ] **Step 4: 在 run_agent() 中记录 knowledge_summary 供 telegram_bot 展示**

找到 `logger.info("[brain] task ...` 那行之前，加入：

```python
    # Store knowledge summary for caller to display (if any matches found)
    _knowledge_summary_cache[thread_id] = _knowledge.format_search_summary_for_user(
        _knowledge.search_knowledge(task) if (not is_watchdog) else []
    ) if (not is_watchdog and not should_clear_working_memory(task)) else ""
```

在文件顶部 `last_usage` 定义处旁边加：

```python
# Knowledge summary shown to user when a match is found (per thread)
_knowledge_summary_cache: dict[str, str] = {}
```

并在 `run_agent()` 返回时，将 knowledge_summary 一起返回（修改返回类型）：

将函数签名和返回改为返回三元组 `(response_text, usage_dict, knowledge_summary_str)`：

```python
async def run_agent(
    task: str,
    chat_id: int | None = None,
    thread_id: str = "default",
) -> tuple[str, dict | None, str]:
    ...
    # clear 分支返回：
    return ("✅ 上下文已清除，开始新任务", None, "")
    ...
    # 超时分支返回：
    return ("❌ 执行超时（10分钟），请稍后重试", None, "")
    # 错误分支：
    return (f"❌ 执行出错: {e}", None, "")
    # 正常返回（末尾）：
    knowledge_summary = _knowledge_summary_cache.pop(thread_id, "")
    return (stdout or "(empty response)", usage, knowledge_summary)
```

同步更新 `run_agent_sync()`，忽略第三个返回值：

```python
    result_text, _usage, _ks = asyncio.run(
        run_agent(task, chat_id=effective_chat_id, thread_id=thread_id)
    )
    return result_text
```

- [ ] **Step 5: 验证语法**

```bash
source venv/bin/activate && python3 -c "import agent.brain; print('OK')"
```

Expected: OK

- [ ] **Step 6: Commit**

```bash
git add agent/brain.py agent/knowledge.py
git commit -m "feat(knowledge): integrate search injection and auto-extract in brain.py"
```

---

## Chunk 5: Bot Commands — Task 7 (/remember) + Task 8 (/knowledge)

### Task 7: bot/telegram_bot.py — /remember 指令

**Files:**
- Modify: `bot/telegram_bot.py`

先读 telegram_bot.py 了解现有指令模式，再加入 /remember。

- [ ] **Step 1: 在 telegram_bot.py 中找到 /new 指令处理，参照其模式加入 /remember**

在处理 `/new` 的函数附近加入：

```python
async def _cmd_remember(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/remember <text> — 手动录入经验到知识库。"""
    from agent import knowledge as _knowledge

    if not _is_allowed(update):
        return

    text = (update.message.text or "").removeprefix("/remember").strip()
    if not text:
        await update.message.reply_text("用法：/remember <经验内容>\n例：/remember OpenClaw升级后需要执行 openclaw config set tools.profile full 才能调用工具")
        return

    await update.message.reply_text("⏳ 正在整理经验...")

    entry = await _knowledge.extract_knowledge_from_text(text)
    if entry is None:
        await update.message.reply_text("❌ 无法从内容中提取有效经验，请提供更具体的问题和解法。")
        return

    await _knowledge.push_entry_async(entry)
    reply = (
        f"✅ 经验已记录\n"
        f"🏷️ 标签：{', '.join(entry['tags'])}\n"
        f"🔍 根因：{entry['root_cause']}\n"
        f"💡 解法：{entry['solution'][:100]}{'...' if len(entry['solution']) > 100 else ''}"
    )
    await update.message.reply_text(reply)
```

在 `application.add_handler(...)` 区域注册：

```python
application.add_handler(CommandHandler("remember", _cmd_remember))
```

在 `set_my_commands()` 中加入：

```python
BotCommand("remember", "手动录入经验到知识库"),
```

- [ ] **Step 2: 更新 /help 文本加入 /remember 说明**

在现有 /help 文本中的指令列表加入：

```
/remember <内容> — 手动录入经验到知识库
/knowledge — 查看经验库
```

- [ ] **Step 3: 验证语法**

```bash
source venv/bin/activate && python3 -c "import bot.telegram_bot; print('OK')"
```

Expected: OK

- [ ] **Step 4: Commit**

```bash
git add bot/telegram_bot.py
git commit -m "feat(knowledge): /remember command for manual knowledge entry"
```

---

### Task 8: bot/telegram_bot.py — /knowledge 指令

**Files:**
- Modify: `bot/telegram_bot.py`

- [ ] **Step 1: 加入 /knowledge 和 /knowledge delete 处理**

```python
async def _cmd_knowledge(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/knowledge — 列出最近10条经验；/knowledge delete <id> — 删除指定条目。"""
    from agent import knowledge as _knowledge

    if not _is_allowed(update):
        return

    args = context.args or []

    if args and args[0] == "delete" and len(args) >= 2:
        entry_id = args[1]
        deleted = _knowledge.delete_entry(entry_id)
        if deleted:
            await update.message.reply_text(f"✅ 已删除经验 {entry_id}")
        else:
            await update.message.reply_text(f"❌ 未找到 id={entry_id} 的经验")
        return

    # List mode
    entries = _knowledge.load_knowledge_cache()
    if not entries:
        await update.message.reply_text("📚 经验库为空")
        return

    recent = entries[-10:]
    lines = [f"📚 经验库（最近 {len(recent)} 条）\n"]
    for e in reversed(recent):
        lines.append(
            f"• [{e.get('id', '?')}] {e.get('root_cause', '')[:50]}\n"
            f"  标签：{', '.join(e.get('tags', []))}"
        )
    lines.append("\n删除：/knowledge delete <id>")
    await update.message.reply_text("\n".join(lines))
```

注册：

```python
application.add_handler(CommandHandler("knowledge", _cmd_knowledge))
```

在 `set_my_commands()` 加入：

```python
BotCommand("knowledge", "查看/管理经验库"),
```

- [ ] **Step 2: 更新 run_agent 调用处，展示知识库参考提示**

找到 telegram_bot.py 中调用 `brain.run_agent()` 的地方（返回 `(text, usage)` 的解包），更新为三元组解包，并在回复前加入知识摘要：

```python
text, usage, knowledge_summary = await brain.run_agent(task, chat_id=chat_id, thread_id=thread_id)
if knowledge_summary:
    text = knowledge_summary + text
```

- [ ] **Step 3: 验证语法**

```bash
source venv/bin/activate && python3 -c "import bot.telegram_bot; print('OK')"
```

Expected: OK

- [ ] **Step 4: Commit**

```bash
git add bot/telegram_bot.py
git commit -m "feat(knowledge): /knowledge list and delete commands; show knowledge hint in replies"
```

---

## Chunk 6: Wiring — Task 9

### Task 9: main.py + install.sh + .gitignore + CLAUDE.md

**Files:**
- Modify: `main.py`
- Modify: `install.sh`
- Modify: `.gitignore`
- Modify: `CLAUDE.md`

- [ ] **Step 1: main.py — 启动时 sync_from_github**

在 `main.py` 中找到服务启动入口（bot 启动前），加入：

```python
from agent.knowledge import sync_from_github
# Sync knowledge base from GitHub on startup (non-blocking failure)
try:
    sync_from_github()
    logger.info("Knowledge base synced from GitHub")
except Exception as e:
    logger.warning("Knowledge sync skipped: %s", e)
```

- [ ] **Step 2: .gitignore — 忽略运行时知识库文件**

在 `.gitignore` 末尾加入：

```
agent/knowledge_cache.json
agent/knowledge_pending.json
```

- [ ] **Step 3: install.sh — 新增 GITHUB_TOKEN 可选引导**

找到 GITHUB_REPO 相关的问题块，在其后加入：

```bash
echo ""
echo "📚 经验库跨机器同步（可选）"
echo "需要 GitHub Personal Access Token（repo 权限）才能启用跨机器知识同步"
echo "不配置则本机正常使用，只是经验不会同步到其他机器"
read -rp "GitHub Token（留空跳过）: " github_token
```

在 `.env` 写入时加入：

```bash
echo "GITHUB_TOKEN=${github_token}" >> .env
```

- [ ] **Step 4: CLAUDE.md — 新增 /remember 用法说明**

在 CLAUDE.md 的 Telegram 指令区域加入：

```markdown
### 经验库指令

| 指令 | 说明 |
|------|------|
| `/remember <内容>` | 手动录入经验（外部文档、版本变更说明等） |
| `/knowledge` | 查看最近 10 条经验 |
| `/knowledge delete <id>` | 删除指定经验条目 |

经验在用户说"好了/解决了"且对话 ≥ 3 轮时自动提取。
```

- [ ] **Step 5: 验证整体启动**

```bash
source venv/bin/activate && python3 -c "import main; print('import OK')"
```

Expected: import OK（不需要真正启动服务）

- [ ] **Step 6: 重启服务验证**

```bash
launchctl stop com.ai-supervisor && sleep 2 && launchctl start com.ai-supervisor
launchctl list com.ai-supervisor | grep PID
```

Expected: PID 不为空，服务正常运行

- [ ] **Step 7: 端到端验证**

在 Telegram 发送 `/remember OpenClaw升级到2026.3.x后需要执行 openclaw config set tools.profile full 才能使用工具`

Expected:
- 收到 `✅ 经验已记录` 回复，含标签和根因摘要
- 发送 `/knowledge` 能看到该条目

- [ ] **Step 8: Commit**

```bash
git add main.py install.sh .gitignore CLAUDE.md
git commit -m "feat(knowledge): wire startup sync, install.sh GITHUB_TOKEN, gitignore, CLAUDE.md docs"
```

---

## 收尾

- [ ] **更新 README.md** — 在功能列表加入"经验库"章节（`/remember`、`/knowledge` 用法）
- [ ] **推送到 GitHub**

```bash
git push origin master
```

- [ ] **在 GitHub 上手动创建 `knowledge` 分支**（首次推送需要，或由第一次 push_entry_async 自动创建）

```bash
git checkout --orphan knowledge
git rm -rf .
echo "[]" > knowledge_base.json
git add knowledge_base.json
git commit -m "init: knowledge base"
git push origin knowledge
git checkout master
```
