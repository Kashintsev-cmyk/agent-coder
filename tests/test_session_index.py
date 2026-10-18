"""Тесты SessionIndex — лёгкий индекс метаданных сессий."""

from __future__ import annotations

import json

from agent_coder.agent.prompts import Mode
from agent_coder.agent.session import Session
from agent_coder.agent.session_index import (
    INDEX_FILENAME,
    INDEX_VERSION,
    SessionIndex,
    SessionMeta,
)


def _mk_session(sid: str = "aaaa1111", **kw) -> Session:
    s = Session.new(
        workspace=kw.pop("workspace", "/tmp/proj"),
        model=kw.pop("model", "test/model"),
        mode=kw.pop("mode", Mode.ACT),
    )
    s.id = sid
    s.title = kw.pop("title", "test title")
    return s


# ---------- load/save ----------


def test_load_missing_ok(tmp_path):
    idx = SessionIndex(path=tmp_path / "index.json")
    assert idx.load() is True
    assert idx.count() == 0


def test_save_then_load_roundtrip(tmp_path):
    path = tmp_path / "index.json"
    idx = SessionIndex(path=path)
    s = _mk_session()
    idx.update(s)

    assert path.exists()
    payload = json.loads(path.read_text())
    assert payload["_version"] == INDEX_VERSION
    assert s.id in payload["sessions"]

    idx2 = SessionIndex(path=path)
    assert idx2.load() is True
    m = idx2.get(s.id)
    assert m is not None
    assert m.id == s.id
    assert m.title == "test title"
    assert m.mode == "act"


def test_load_bad_json_returns_false(tmp_path):
    path = tmp_path / "index.json"
    path.write_text("{ not json")
    idx = SessionIndex(path=path)
    assert idx.load() is False
    assert idx.count() == 0


def test_load_wrong_version_returns_false(tmp_path):
    path = tmp_path / "index.json"
    path.write_text(json.dumps({"_version": 99, "sessions": {}}))
    idx = SessionIndex(path=path)
    assert idx.load() is False
    assert idx.count() == 0


# ---------- update/remove/list ----------


def test_update_overwrites(tmp_path):
    idx = SessionIndex(path=tmp_path / "index.json")
    s = _mk_session(title="v1")
    idx.update(s)
    s.title = "v2"
    idx.update(s)
    assert idx.get(s.id).title == "v2"


def test_remove(tmp_path):
    idx = SessionIndex(path=tmp_path / "index.json")
    s = _mk_session()
    idx.update(s)
    assert idx.get(s.id) is not None
    idx.remove(s.id)
    assert idx.get(s.id) is None
    # файл перезаписан без этой сессии
    payload = json.loads((tmp_path / "index.json").read_text())
    assert s.id not in payload["sessions"]


def test_remove_missing_is_noop(tmp_path):
    path = tmp_path / "index.json"
    idx = SessionIndex(path=path)
    idx.update(_mk_session(sid="keep"))
    mtime = path.stat().st_mtime

    import time

    time.sleep(0.01)
    idx.remove("no-such-id")
    # файл не перезаписан
    assert path.stat().st_mtime == mtime


def test_list_sorted_by_updated_at(tmp_path):
    idx = SessionIndex(path=tmp_path / "index.json")
    s1 = _mk_session(sid="old", title="old")
    s2 = _mk_session(sid="new", title="new")
    import time

    s1.updated_at = 100.0
    idx.update(s1)
    time.sleep(0.01)
    s2.updated_at = 200.0
    idx.update(s2)

    lst = idx.list()
    assert [m.id for m in lst] == ["new", "old"]


# ---------- rebuild ----------


def test_rebuild_from_sessions(tmp_path, monkeypatch):
    from agent_coder.config import config

    monkeypatch.setattr(config, "SESSIONS_DIR", tmp_path)

    # три сессии на диске
    for i in range(3):
        s = _mk_session(sid=f"id{i}", title=f"t{i}")
        s.save()

    path = tmp_path / INDEX_FILENAME
    idx = SessionIndex(path=path)
    n = idx.rebuild(tmp_path)
    assert n == 3
    assert idx.count() == 3
    assert path.exists()


def test_rebuild_skips_broken(tmp_path, monkeypatch):
    from agent_coder.config import config

    monkeypatch.setattr(config, "SESSIONS_DIR", tmp_path)

    s = _mk_session(sid="good")
    s.save()
    (tmp_path / "broken.json").write_text("{ not json }")

    idx = SessionIndex(path=tmp_path / INDEX_FILENAME)
    n = idx.rebuild(tmp_path)
    assert n == 1
    assert idx.get("good") is not None


def test_rebuild_ignores_index_file(tmp_path, monkeypatch):
    from agent_coder.config import config

    monkeypatch.setattr(config, "SESSIONS_DIR", tmp_path)

    s = _mk_session(sid="x")
    s.save()
    # испорченный index.json в той же папке не должен считаться сессией
    (tmp_path / INDEX_FILENAME).write_text("{ bad }")
    idx = SessionIndex(path=tmp_path / INDEX_FILENAME)
    n = idx.rebuild(tmp_path)
    assert n == 1


# ---------- SessionMeta ----------


def test_session_meta_from_session():
    s = _mk_session()
    m = SessionMeta.from_session(s)
    assert m.id == s.id
    assert m.mode == "act"
    assert isinstance(m.cost, float)
    assert m.cost == 0.0
