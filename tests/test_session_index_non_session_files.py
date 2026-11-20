"""A13: не-сессионные файлы в SESSIONS_DIR не должны ломать сверку индекса.

В SESSIONS_DIR кроме сессий лежит `telegram_state.json`. list_all() считал
его сессией (glob *.json минус index.json), а rebuild() его пропускал →
число файлов на диске никогда не сходилось с размером индекса → полная
пересборка индекса при КАЖДОМ вызове /sessions (346 файлов за клик).
"""

from __future__ import annotations

import json

from agent_coder.agent import session_index as _si
from agent_coder.agent.prompts import Mode
from agent_coder.agent.session import Session
from agent_coder.agent.session_index import (
    INDEX_FILENAME,
    SessionIndex,
    is_session_file,
)


def _isolate(monkeypatch, tmp_path):
    from agent_coder.config import config

    monkeypatch.setattr(config, "SESSIONS_DIR", tmp_path)
    fresh = SessionIndex(path=tmp_path / INDEX_FILENAME)
    monkeypatch.setattr(_si, "index", fresh)
    return fresh


def test_is_session_file():
    assert is_session_file("a1b2c3d4.json")
    assert not is_session_file(INDEX_FILENAME)
    assert not is_session_file("telegram_state.json")
    assert not is_session_file("a1b2c3d4.json.tmp")
    assert not is_session_file("notes.txt")


def test_telegram_state_does_not_trigger_rebuild(tmp_path, monkeypatch):
    """С telegram_state.json в каталоге индекс стабилен: rebuild один раз."""
    idx = _isolate(monkeypatch, tmp_path)
    s = Session.new(workspace="/tmp/p", model="m", mode=Mode.ACT)
    s.messages = [{"role": "user", "content": "hi"}]
    s.save()

    # telegram_state.json появляется у бота в том же каталоге
    (tmp_path / "telegram_state.json").write_text(
        json.dumps({"active_sessions": {"1": s.id}}), encoding="utf-8"
    )

    calls = {"n": 0}
    real_rebuild = idx.rebuild

    def counting_rebuild(sessions_dir):
        calls["n"] += 1
        return real_rebuild(sessions_dir)

    monkeypatch.setattr(idx, "rebuild", counting_rebuild)

    list1 = Session.list_all()
    list2 = Session.list_all()

    assert [x.id for x in list1] == [x.id for x in list2] == [s.id]
    assert calls["n"] == 0, "индекс рассинхронен — rebuild на каждом вызове"


def test_index_count_matches_disk_sessions(tmp_path, monkeypatch):
    idx = _isolate(monkeypatch, tmp_path)
    for _ in range(3):
        s = Session.new(workspace="/tmp/p", model="m", mode=Mode.ACT)
        s.save()
    (tmp_path / "telegram_state.json").write_text("{}", encoding="utf-8")

    Session.list_all()
    assert idx.count() == 3
