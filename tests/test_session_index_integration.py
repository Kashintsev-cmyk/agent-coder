"""Интеграция SessionIndex в Session: save/delete/list_all."""
from __future__ import annotations

from agent_coder.agent.prompts import Mode
from agent_coder.agent.session import Session
from agent_coder.agent.session_index import INDEX_FILENAME, SessionIndex


def _isolate(monkeypatch, tmp_path):
    from agent_coder.agent import session_index as _si
    from agent_coder.config import config
    monkeypatch.setattr(config, "SESSIONS_DIR", tmp_path)
    # заменить глобальный index на свежий, привязанный к tmp_path
    fresh = SessionIndex(path=tmp_path / INDEX_FILENAME)
    monkeypatch.setattr(_si, "index", fresh)
    return fresh


def test_save_updates_index(tmp_path, monkeypatch):
    idx = _isolate(monkeypatch, tmp_path)
    s = Session.new(workspace="/tmp/p", model="m", mode=Mode.ACT)
    s.title = "hello"
    s.save()

    m = idx.get(s.id)
    assert m is not None
    assert m.title == "hello"
    assert m.mode == "act"


def test_delete_removes_from_index(tmp_path, monkeypatch):
    idx = _isolate(monkeypatch, tmp_path)
    s = Session.new(workspace="/tmp/p", model="m", mode=Mode.ACT)
    s.save()
    assert idx.get(s.id) is not None

    s.delete()
    assert idx.get(s.id) is None


def test_list_all_uses_index(tmp_path, monkeypatch):
    _isolate(monkeypatch, tmp_path)
    for i in range(3):
        s = Session.new(workspace="/tmp/p", model="m", mode=Mode.ACT)
        s.title = f"t{i}"
        s.save()

    sessions = Session.list_all()
    assert len(sessions) == 3
    # messages пустой (лёгкая сессия из меты)
    assert all(s.messages == [] for s in sessions)
    # cost/request_count — из меты
    assert all(s.total_usage.cost == 0.0 for s in sessions)


def test_list_all_rebuild_on_disk_mismatch(tmp_path, monkeypatch):
    idx = _isolate(monkeypatch, tmp_path)
    s = Session.new(workspace="/tmp/p", model="m", mode=Mode.ACT)
    s.save()
    assert idx.count() == 1

    # ручками добавили ещё одну сессию на диск, минуя save()
    other = Session.new(workspace="/tmp/p", model="m", mode=Mode.PLAN)
    other.title = "manual"
    import json as _json
    (tmp_path / f"{other.id}.json").write_text(
        _json.dumps(other.to_dict(), ensure_ascii=False), encoding="utf-8",
    )

    sessions = Session.list_all()
    ids = {s.id for s in sessions}
    assert s.id in ids
    assert other.id in ids


def test_latest_uses_index(tmp_path, monkeypatch):
    _isolate(monkeypatch, tmp_path)
    s1 = Session.new(workspace="/tmp/a", model="m", mode=Mode.ACT)
    s1.save()
    import time
    time.sleep(0.01)
    s2 = Session.new(workspace="/tmp/b", model="m", mode=Mode.ACT)
    s2.save()

    latest = Session.latest()
    assert latest is not None
    # s2 свежее
    assert latest.id == s2.id

    latest_a = Session.latest(workspace="/tmp/a")
    assert latest_a is not None
    assert latest_a.id == s1.id


def test_from_meta_roundtrip(tmp_path, monkeypatch):
    idx = _isolate(monkeypatch, tmp_path)
    s = Session.new(workspace="/tmp/p", model="m", mode=Mode.PLAN)
    s.title = "X"
    s.save()

    m = idx.get(s.id)
    lite = Session.from_meta(m)
    assert lite.id == s.id
    assert lite.mode == Mode.PLAN
    assert lite.title == "X"
    assert lite.messages == []
