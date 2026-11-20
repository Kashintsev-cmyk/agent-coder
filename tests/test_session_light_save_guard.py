"""A12: save() на light-сессии (из list_all/from_meta) не должен затирать историю.

Light-сессия — объект, собранный из sidecar-индекса: только метаданные,
messages/read_paths/work_state пустые. Запись такого объекта на диск
заменила бы полный JSON пустым списком сообщений и потеряла бы историю
(именно так появилась сессия a6a74ebf: cost=75.99₽, messages=0).
"""

from __future__ import annotations

import json

import pytest

from agent_coder.agent import session_index as _si
from agent_coder.agent.prompts import Mode
from agent_coder.agent.session import Session
from agent_coder.agent.session_index import INDEX_FILENAME, SessionIndex


def _isolate(monkeypatch, tmp_path):
    from agent_coder.config import config

    monkeypatch.setattr(config, "SESSIONS_DIR", tmp_path)
    fresh = SessionIndex(path=tmp_path / INDEX_FILENAME)
    monkeypatch.setattr(_si, "index", fresh)
    return fresh


def _session_with_history(tmp_path, monkeypatch) -> Session:
    _isolate(monkeypatch, tmp_path)
    s = Session.new(workspace="/tmp/p", model="m", mode=Mode.ACT)
    s.title = "с историей"
    s.messages = [
        {"role": "user", "content": "важная задача"},
        {"role": "assistant", "content": "ответ"},
    ]
    s.read_paths = {"a.py": "def f() -> None: ..."}
    s.work_state = {"findings": ["факт"], "done": [], "todo": [], "open_questions": []}
    s.total_usage.cost = 12.5
    s.request_count = 2
    s.save()
    return s


def test_from_meta_marks_light():
    s = Session.new(workspace="/tmp/p", model="m", mode=Mode.PLAN)
    assert s._light is False


def test_save_on_light_session_raises(tmp_path, monkeypatch):
    idx = _isolate(monkeypatch, tmp_path)
    s = Session.new(workspace="/tmp/p", model="m", mode=Mode.ACT)
    s.save()

    lite = Session.from_meta(idx.get(s.id))
    assert lite._light is True
    with pytest.raises(RuntimeError, match="light-сессии"):
        lite.save()


def test_light_save_does_not_clobber_history(tmp_path, monkeypatch):
    """Главное: попытка save() light-объекта не трогает файл на диске."""
    s = _session_with_history(tmp_path, monkeypatch)
    path = tmp_path / f"{s.id}.json"
    before = json.loads(path.read_text(encoding="utf-8"))
    assert len(before["messages"]) == 2

    lite = next(x for x in Session.list_all() if x.id == s.id)
    assert lite._light is True
    with pytest.raises(RuntimeError):
        lite.save()

    after = json.loads(path.read_text(encoding="utf-8"))
    assert after["messages"] == before["messages"]
    assert after["read_paths"] == before["read_paths"]
    assert after["work_state"] == before["work_state"]


def test_loaded_session_can_save(tmp_path, monkeypatch):
    """Session.load(id) даёт полноценный объект — сохранение разрешено."""
    s = _session_with_history(tmp_path, monkeypatch)

    full = Session.load(s.id)
    assert full is not None
    assert full._light is False
    full.title = "изменено"
    full.save()

    again = Session.load(s.id)
    assert again is not None
    assert again.title == "изменено"
    assert len(again.messages) == 2


def test_light_flag_not_persisted(tmp_path, monkeypatch):
    """_light — флаг происхождения, в JSON и в round-trip не попадает."""
    s = _session_with_history(tmp_path, monkeypatch)
    raw = json.loads((tmp_path / f"{s.id}.json").read_text(encoding="utf-8"))
    assert "_light" not in raw

    full = Session.load(s.id)
    assert full is not None
    assert full._light is False
    assert "_light" not in full.to_dict()
