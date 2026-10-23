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


# --------------------------------------------------------------------------
# B2: OSError в load() сбрасывает state; NaN/inf cost -> 0.0 в мете.
# --------------------------------------------------------------------------


def test_load_oserror_resets_state(tmp_path):
    """OSError при read_text -> _loaded=False, _sessions={} (как в других
    error-ветках). Иначе stale in-memory + save() поверх."""
    from agent_coder.agent.session_index import SessionIndex, SessionMeta

    idx = SessionIndex(path=tmp_path / "index.json")
    assert idx.load() is True  # missing -> ok, _loaded=True
    idx._sessions["s1"] = SessionMeta(
        id="s1",
        title="",
        workspace="",
        model="",
        mode="act",
        cost=0.0,
        request_count=0,
        created_at=0.0,
        updated_at=0.0,
    )
    assert idx._loaded is True
    # превращаем index.json в директорию -> IsADirectoryError (OSError)
    (tmp_path / "index.json").mkdir()
    assert idx.load() is False
    assert idx._loaded is False
    assert idx._sessions == {}


def test_meta_cost_nonfinite_is_zero():
    """SessionMeta.from_session: NaN/inf cost -> 0.0, конечное — как есть."""
    from agent_coder.agent.prompts import Mode
    from agent_coder.agent.session import Session
    from agent_coder.agent.session_index import SessionMeta

    s = Session.new(workspace="/tmp", model="openai/gpt-5.6-luna", mode=Mode.ACT)
    s.total_usage.cost = float("nan")
    assert SessionMeta.from_session(s).cost == 0.0
    s.total_usage.cost = float("inf")
    assert SessionMeta.from_session(s).cost == 0.0
    s.total_usage.cost = 1.5
    assert SessionMeta.from_session(s).cost == 1.5


def test_load_nonfinite_cost_from_json(tmp_path):
    """Индекс с "NaN"/"inf" в cost: load не падает, мета с cost=0.0."""
    import json as _json

    from agent_coder.agent.session_index import INDEX_VERSION, SessionIndex

    payload = {
        "_version": INDEX_VERSION,
        "_updated_at": 0.0,
        "sessions": {
            "s1": {
                "id": "s1",
                "title": "",
                "workspace": "",
                "model": "",
                "mode": "act",
                "cost": "NaN",
                "request_count": 0,
                "created_at": 0.0,
                "updated_at": 0.0,
            },
            "s2": {
                "id": "s2",
                "title": "",
                "workspace": "",
                "model": "",
                "mode": "act",
                "cost": "Infinity",
                "request_count": 0,
                "created_at": 0.0,
                "updated_at": 0.0,
            },
        },
    }
    (tmp_path / "index.json").write_text(_json.dumps(payload))
    idx = SessionIndex(path=tmp_path / "index.json")
    assert idx.load() is True
    assert idx._sessions["s1"].cost == 0.0
    assert idx._sessions["s2"].cost == 0.0
