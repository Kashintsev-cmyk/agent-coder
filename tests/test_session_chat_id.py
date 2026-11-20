"""Владелец сессии (chat_id): персистентность, индекс, фильтр /sessions.

Зачем: /sessions показывал весь каталог (346 сессий = 44 страницы), включая
чужие и мусорные от pytest. Юзер листал каталог вместо своих сессий и
попадал в пустые. chat_id позволяет показать только сессии своего чата.
"""

from __future__ import annotations

import json
import tempfile

from agent_coder.agent import session_index as _si
from agent_coder.agent.prompts import Mode
from agent_coder.agent.session import Session
from agent_coder.agent.session_index import INDEX_FILENAME, SessionIndex


def _isolate(monkeypatch, tmp_path) -> SessionIndex:
    from agent_coder.config import config

    monkeypatch.setattr(config, "SESSIONS_DIR", tmp_path)
    fresh = SessionIndex(path=tmp_path / INDEX_FILENAME)
    monkeypatch.setattr(_si, "index", fresh)
    return fresh


# ---------- персистентность ----------


def test_new_stores_chat_id():
    s = Session.new(workspace="/tmp/p", model="m", mode=Mode.ACT, chat_id=42)
    assert s.chat_id == 42


def test_new_defaults_chat_id_to_zero():
    s = Session.new(workspace="/tmp/p", model="m", mode=Mode.ACT)
    assert s.chat_id == 0


def test_chat_id_survives_save_load(tmp_path, monkeypatch):
    _isolate(monkeypatch, tmp_path)
    s = Session.new(workspace="/tmp/p", model="m", mode=Mode.ACT, chat_id=201821839)
    s.save()

    loaded = Session.load(s.id)
    assert loaded is not None
    assert loaded.chat_id == 201821839


def test_legacy_session_without_chat_id_loads_as_zero(tmp_path, monkeypatch):
    """Старые сессии на диске не имеют поля chat_id — грузятся, а не падают."""
    _isolate(monkeypatch, tmp_path)
    s = Session.new(workspace="/tmp/p", model="m", mode=Mode.ACT, chat_id=7)
    s.save()

    path = tmp_path / f"{s.id}.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    del data["chat_id"]
    path.write_text(json.dumps(data), encoding="utf-8")

    loaded = Session.load(s.id)
    assert loaded is not None
    assert loaded.chat_id == 0


def test_chat_id_not_written_as_extra_field(tmp_path, monkeypatch):
    """_light и прочие служебные флаги в файл не попадают."""
    _isolate(monkeypatch, tmp_path)
    s = Session.new(workspace="/tmp/p", model="m", mode=Mode.ACT, chat_id=5)
    s.save()

    data = json.loads((tmp_path / f"{s.id}.json").read_text(encoding="utf-8"))
    assert data["chat_id"] == 5
    assert "_light" not in data


# ---------- индекс ----------


def test_index_meta_carries_chat_id(tmp_path, monkeypatch):
    idx = _isolate(monkeypatch, tmp_path)
    s = Session.new(workspace="/tmp/p", model="m", mode=Mode.ACT, chat_id=99)
    s.save()

    meta = idx.get(s.id)
    assert meta is not None
    assert meta.chat_id == 99


def test_index_survives_reload(tmp_path, monkeypatch):
    _isolate(monkeypatch, tmp_path)
    s = Session.new(workspace="/tmp/p", model="m", mode=Mode.ACT, chat_id=123)
    s.save()

    fresh = SessionIndex(path=tmp_path / INDEX_FILENAME)
    assert fresh.load() is True
    meta = fresh.get(s.id)
    assert meta is not None
    assert meta.chat_id == 123


def test_old_index_v1_is_rebuilt(tmp_path, monkeypatch):
    """Индекс версии 1 (без chat_id) должен пересобраться, а не отдавать 0."""
    _isolate(monkeypatch, tmp_path)
    s = Session.new(workspace="/tmp/p", model="m", mode=Mode.ACT, chat_id=555)
    s.save()

    # Эмулируем старый индекс: версия 1, без chat_id.
    path = tmp_path / INDEX_FILENAME
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["_version"] = 1
    for rec in payload["sessions"].values():
        rec.pop("chat_id", None)
    path.write_text(json.dumps(payload), encoding="utf-8")

    fresh = SessionIndex(path=path)
    assert fresh.load() is False  # версия не та → требуется rebuild
    fresh.rebuild(tmp_path)
    meta = fresh.get(s.id)
    assert meta is not None
    assert meta.chat_id == 555


# ---------- фильтр list_all ----------


def test_list_all_filters_by_chat_id(tmp_path, monkeypatch):
    _isolate(monkeypatch, tmp_path)
    mine = Session.new(workspace="/tmp/a", model="m", mode=Mode.ACT, chat_id=1)
    mine.save()
    other = Session.new(workspace="/tmp/b", model="m", mode=Mode.ACT, chat_id=2)
    other.save()
    legacy = Session.new(workspace="/tmp/c", model="m", mode=Mode.ACT)
    legacy.save()

    ids = {s.id for s in Session.list_all(chat_id=1)}
    assert ids == {mine.id}


def test_list_all_without_chat_id_returns_everything(tmp_path, monkeypatch):
    _isolate(monkeypatch, tmp_path)
    for chat in (0, 1, 2):
        Session.new(
            workspace=f"/tmp/{chat}", model="m", mode=Mode.ACT, chat_id=chat
        ).save()

    assert len(Session.list_all()) == 3


def test_list_all_filter_excludes_legacy_zero(tmp_path, monkeypatch):
    """Сессии без владельца (0) не подмешиваются в чужой фильтр."""
    _isolate(monkeypatch, tmp_path)
    Session.new(workspace="/tmp/x", model="m", mode=Mode.ACT, chat_id=0).save()
    Session.new(workspace="/tmp/y", model="m", mode=Mode.ACT, chat_id=1).save()

    assert len(Session.list_all(chat_id=1)) == 1
    assert len(Session.list_all(chat_id=0)) == 1


def test_list_all_empty_when_no_match(tmp_path, monkeypatch):
    _isolate(monkeypatch, tmp_path)
    Session.new(workspace="/tmp/x", model="m", mode=Mode.ACT, chat_id=1).save()

    assert Session.list_all(chat_id=999) == []


def test_light_session_from_meta_has_chat_id(tmp_path, monkeypatch):
    _isolate(monkeypatch, tmp_path)
    s = Session.new(workspace="/tmp/p", model="m", mode=Mode.ACT, chat_id=8)
    s.save()

    lite = next(x for x in Session.list_all(chat_id=8))
    assert lite.chat_id == 8
    assert lite.id == s.id


# ---------- latest ----------


def test_latest_respects_chat_id(tmp_path, monkeypatch):
    _isolate(monkeypatch, tmp_path)
    a = Session.new(workspace="/tmp/w", model="m", mode=Mode.ACT, chat_id=1)
    a.save()
    b = Session.new(workspace="/tmp/w", model="m", mode=Mode.ACT, chat_id=2)
    b.updated_at = a.updated_at + 100
    b.save()

    assert Session.latest(chat_id=1).id == a.id
    assert Session.latest(chat_id=2).id == b.id
    assert Session.latest().id == b.id


# ---------- backfill ----------


def _legacy(workspace: str, *, messages=1, cost=1.0, **kw) -> Session:
    """Сессия, как её записал старый код: без поля chat_id."""
    s = Session.new(workspace=workspace, model="m", mode=Mode.ACT)
    s.messages = [{"role": "user", "content": "задача"}] * messages
    s.total_usage.cost = cost
    s.request_count = 1 if messages else 0
    s.save()

    path = config_sessions_dir() / f"{s.id}.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    del data["chat_id"]
    path.write_text(json.dumps(data), encoding="utf-8")
    return s


def config_sessions_dir():
    from agent_coder.config import config

    return config.SESSIONS_DIR


def test_backfill_assigns_owner_to_real_sessions(tmp_path, monkeypatch):
    _isolate(monkeypatch, tmp_path)
    real = _legacy("/home/botuser/projects/app")

    assert Session.backfill_chat_id(777) == 1
    assert Session.load(real.id).chat_id == 777


def test_backfill_skips_pytest_scrap(tmp_path, monkeypatch):
    """workspace=/tmp — мусор от pytest, владельца не получает."""
    _isolate(monkeypatch, tmp_path)
    _legacy("/tmp/pytest-of-botuser/pytest-12/test_x0")

    assert Session.backfill_chat_id(777) == 0
    assert Session.list_all(chat_id=777) == []


def test_backfill_skips_empty_zero_cost(tmp_path, monkeypatch):
    """Пустая сессия без единого запроса — тоже не работа, не закрепляем."""
    _isolate(monkeypatch, tmp_path)
    _legacy("/home/botuser/projects/app", messages=0, cost=0.0)

    assert Session.backfill_chat_id(777) == 0


def test_backfill_keeps_already_owned(tmp_path, monkeypatch):
    _isolate(monkeypatch, tmp_path)
    s = Session.new(workspace="/home/p/app", model="m", mode=Mode.ACT, chat_id=5)
    s.save()

    assert Session.backfill_chat_id(777) == 0
    assert Session.load(s.id).chat_id == 5


def test_backfill_is_idempotent(tmp_path, monkeypatch):
    _isolate(monkeypatch, tmp_path)
    _legacy("/home/botuser/projects/app")

    assert Session.backfill_chat_id(777) == 1
    assert Session.backfill_chat_id(777) == 0
    assert len(Session.list_all(chat_id=777)) == 1


def test_backfill_preserves_unknown_fields(tmp_path, monkeypatch):
    """Миграция не должна терять поля, которых нет в dataclass."""
    _isolate(monkeypatch, tmp_path)
    s = _legacy("/home/botuser/projects/app")
    path = config_sessions_dir() / f"{s.id}.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    data["legacy_extra"] = {"kept": True}
    path.write_text(json.dumps(data), encoding="utf-8")

    Session.backfill_chat_id(777)

    after = json.loads(path.read_text(encoding="utf-8"))
    assert after["legacy_extra"] == {"kept": True}
    assert after["chat_id"] == 777


# ---------- is_scrap_workspace ----------


def test_scrap_workspace_detects_system_tempdir():
    from agent_coder.agent.session_index import is_scrap_workspace

    tmp = tempfile.gettempdir()
    assert is_scrap_workspace(tmp) is True
    assert is_scrap_workspace(f"{tmp}/pytest-of-botuser/pytest-12/test_x0") is True


def test_scrap_workspace_accepts_real_project():
    from agent_coder.agent.session_index import is_scrap_workspace

    assert is_scrap_workspace("/home/botuser/bots/agent-coder") is False
    assert is_scrap_workspace("") is False


def test_scrap_workspace_no_false_positive_on_tmp_prefix():
    """`/tmpfoo` — не временный каталог, префикс должен совпасть по границе."""
    from agent_coder.agent.session_index import is_scrap_workspace

    tmp = tempfile.gettempdir().rstrip("/")
    assert is_scrap_workspace(f"{tmp}foo/project") is False
