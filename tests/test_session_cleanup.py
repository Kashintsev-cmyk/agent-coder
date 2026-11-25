"""Tests for inactivity-based session retention."""

from __future__ import annotations

from pathlib import Path

from agent_coder.agent import session_index
from agent_coder.agent.prompts import Mode
from agent_coder.agent.session import Session
from agent_coder.agent.session_cleanup import cleanup_old_sessions
from agent_coder.agent.session_index import INDEX_FILENAME, SessionIndex
from agent_coder.config import config


def _session(title: str, *, updated_at: float) -> Session:
    session = Session.new(
        workspace="/home/user/project", model="test/model", mode=Mode.ACT, title=title
    )
    session.updated_at = updated_at
    session.save()
    return session


def _use_isolated_index(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(
        session_index,
        "index",
        SessionIndex(path=tmp_path / INDEX_FILENAME),
    )


def test_deletes_inactive_sessions_and_keeps_recent(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "SESSIONS_DIR", tmp_path)
    _use_isolated_index(tmp_path, monkeypatch)
    now = 2_000_000_000.0
    old = _session("old", updated_at=now - 31 * 86400)
    recent = _session("recent", updated_at=now - 29 * 86400)
    created_long_ago_but_active = _session("active", updated_at=now - 86400)
    created_long_ago_but_active.created_at = now - 365 * 86400
    created_long_ago_but_active.save()

    deleted = cleanup_old_sessions(retention_days=30, now=now)

    assert deleted == [old.id]
    assert Session.load(old.id) is None
    assert Session.load(recent.id) is not None
    assert Session.load(created_long_ago_but_active.id) is not None
    assert session_index.index.get(old.id) is None


def test_keeps_sessions_with_unknown_activity_timestamp(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "SESSIONS_DIR", tmp_path)
    _use_isolated_index(tmp_path, monkeypatch)
    unknown = _session("legacy", updated_at=0)

    assert cleanup_old_sessions(retention_days=30, now=2_000_000_000.0) == []
    assert Session.load(unknown.id) is not None


def test_retention_less_than_one_is_noop(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "SESSIONS_DIR", tmp_path)
    _use_isolated_index(tmp_path, monkeypatch)
    old = _session("old", updated_at=1)

    assert cleanup_old_sessions(retention_days=0, now=2_000_000_000.0) == []
    assert Session.load(old.id) is not None
