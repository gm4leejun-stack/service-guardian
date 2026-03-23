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
