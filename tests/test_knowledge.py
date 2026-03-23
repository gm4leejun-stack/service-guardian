"""Tests for agent/knowledge.py — local I/O and data model."""
import json
import pytest
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
