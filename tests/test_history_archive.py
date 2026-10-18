"""Тесты архива сжатой истории (agent_coder.agent.history_archive)."""

from __future__ import annotations

import json

import pytest

from agent_coder.agent import history_archive


@pytest.fixture
def session_id(monkeypatch, tmp_path):
    """Изолированный SESSIONS_DIR + фиксированный id."""
    monkeypatch.setattr(
        type(history_archive.config), "SESSIONS_DIR", tmp_path, raising=False
    )
    return "testsess"


def test_append_and_load(session_id):
    msgs = [
        {"role": "user", "content": "задача"},
        {"role": "assistant", "content": "ответ", "tool_calls": []},
    ]
    assert history_archive.append_messages(session_id, msgs) == 2
    loaded = history_archive.load(session_id)
    assert loaded == msgs
    # JSONL: каждая строка — валидный JSON
    path = history_archive.archive_path(session_id)
    for line in path.read_text(encoding="utf-8").splitlines():
        json.loads(line)


def test_append_appends_not_overwrites(session_id):
    history_archive.append_messages(session_id, [{"role": "user", "content": "1"}])
    history_archive.append_messages(session_id, [{"role": "user", "content": "2"}])
    assert len(history_archive.load(session_id)) == 2


def test_append_empty_noop(session_id):
    assert history_archive.append_messages(session_id, []) == 0
    assert not history_archive.archive_path(session_id).exists()


def test_load_missing_returns_empty(session_id):
    assert history_archive.load("no-such") == []


def test_load_skips_broken_lines(session_id):
    path = history_archive.archive_path(session_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps({"role": "user", "content": "ok"})
        + "\n"
        + "НЕ_JSON\n"
        + json.dumps({"role": "assistant", "content": "ok2"})
        + "\n",
        encoding="utf-8",
    )
    loaded = history_archive.load(session_id)
    assert len(loaded) == 2


def test_delete(session_id):
    history_archive.append_messages(session_id, [{"role": "user", "content": "x"}])
    assert history_archive.delete(session_id) is True
    assert history_archive.delete(session_id) is False
    assert history_archive.load(session_id) == []


def test_unicode_preserved(session_id):
    history_archive.append_messages(
        session_id, [{"role": "user", "content": "привет мир — ёж"}]
    )
    raw = history_archive.archive_path(session_id).read_text(encoding="utf-8")
    assert "привет мир — ёж" in raw  # не \u-эскейпы
