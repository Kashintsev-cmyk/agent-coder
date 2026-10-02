"""Тесты сессии: сериализация, заголовок, sanitize_tool_calls, удаление."""
from __future__ import annotations

from agent_coder.agent.prompts import Mode
from agent_coder.agent.session import Session, _make_default_title
from agent_coder.llm.types import Usage


def _mk_session(**kw) -> Session:
    return Session.new(
        workspace="/tmp/proj",
        model="test/model",
        mode=kw.pop("mode", Mode.ACT),
        **kw,
    )


class TestDefaultTitle:
    def test_uses_first_user_message(self):
        msgs = [{"role": "assistant", "content": "hi"},
                {"role": "user", "content": "сделай фичу"}]
        assert _make_default_title(msgs) == "сделай фичу"

    def test_truncates_long_text(self):
        msgs = [{"role": "user", "content": "a" * 100}]
        title = _make_default_title(msgs)
        assert len(title) == 61  # 60 + «…»
        assert title.endswith("…")

    def test_newlines_replaced(self):
        msgs = [{"role": "user", "content": "первая\nвторая"}]
        assert _make_default_title(msgs) == "первая вторая"

    def test_empty(self):
        assert _make_default_title([]) == "(без названия)"


class TestRoundtrip:
    def test_to_dict_from_dict(self):
        s = _mk_session(title="тест")
        s.add_message({"role": "user", "content": "привет"})
        s.add_usage(Usage(input_tokens=10, output_tokens=5, cost=0.25))

        restored = Session.from_dict(s.to_dict())

        assert restored.id == s.id
        assert restored.workspace == s.workspace
        assert restored.mode == s.mode
        assert restored.title == s.title
        assert restored.messages == s.messages
        assert restored.total_usage.cost == 0.25
        assert restored.request_count == 1

    def test_mode_serialized_as_value(self):
        s = _mk_session(mode=Mode.PLAN)
        assert s.to_dict()["mode"] == "plan"

    def test_old_session_without_reasoning(self):
        # Совместимость: старый JSON без reasoning_effort
        data = {
            "id": "abc",
            "workspace": "/tmp",
            "model": "m",
            "mode": "act",
            "total_usage": Usage().__dict__ if hasattr(Usage(), "__dict__") else {},
            "messages": [],
        }
        # from_dict ожидает плоский dict для total_usage
        from dataclasses import asdict
        data["total_usage"] = asdict(Usage())
        restored = Session.from_dict(data)
        assert restored.reasoning_effort  # подставлен дефолт


class TestSaveLoad:
    def test_save_and_load(self):
        s = _mk_session(title="сохранённая")
        s.add_message({"role": "user", "content": "задача"})
        s.save()

        loaded = Session.load(s.id)
        assert loaded is not None
        assert loaded.title == "сохранённая"
        assert loaded.messages[0]["content"] == "задача"

    def test_load_missing_returns_none(self):
        assert Session.load("нет_такой") is None

    def test_delete_removes_file(self):
        s = _mk_session()
        s.save()
        assert Session.load(s.id) is not None
        removed = s.delete()
        assert removed >= 1
        assert Session.load(s.id) is None


class TestSanitizeToolCalls:
    def test_empty(self):
        assert _mk_session().sanitize_tool_calls() == 0

    def test_all_answered(self):
        s = _mk_session()
        s.messages = [
            {"role": "assistant", "tool_calls": [{"id": "1", "name": "x"}]},
            {"role": "tool", "tool_call_id": "1", "content": "ok"},
        ]
        assert s.sanitize_tool_calls() == 0

    def test_adds_stubs_for_unanswered_calls(self):
        # Последний assistant с tool_calls, ответов нет вовсе
        s = _mk_session()
        s.messages = [
            {"role": "assistant", "tool_calls": [
                {"id": "1", "name": "x"}, {"id": "2", "name": "y"},
            ]},
        ]
        assert s.sanitize_tool_calls() == 2
        answered = {m.get("tool_call_id") for m in s.messages if m["role"] == "tool"}
        assert answered == {"1", "2"}

    def test_partial_answers(self):
        # Один tool_call отвечен (но как последнее сообщение — tool),
        # поэтому функция считает состояние корректным
        s = _mk_session()
        s.messages = [
            {"role": "assistant", "tool_calls": [
                {"id": "1", "name": "x"}, {"id": "2", "name": "y"},
            ]},
            {"role": "tool", "tool_call_id": "1", "content": "ok"},
        ]
        # Последнее сообщение — tool → по логике всё закрыто
        assert s.sanitize_tool_calls() == 0

    def test_assistant_without_tool_calls(self):
        s = _mk_session()
        s.messages = [{"role": "assistant", "content": "просто текст"}]
        assert s.sanitize_tool_calls() == 0

    def test_trailing_user_after_tool(self):
        s = _mk_session()
        s.messages = [
            {"role": "assistant", "tool_calls": [{"id": "1", "name": "x"}]},
            {"role": "tool", "tool_call_id": "1", "content": "ok"},
            {"role": "user", "content": "новый запрос"},
        ]
        assert s.sanitize_tool_calls() == 0


class TestAddMessage:
    def test_sets_title_from_first_user(self):
        s = _mk_session()
        s.add_message({"role": "user", "content": "первая задача"})
        assert s.title == "первая задача"

    def test_title_not_overwritten(self):
        s = _mk_session()
        s.add_message({"role": "user", "content": "первая"})
        s.add_message({"role": "user", "content": "вторая"})
        assert s.title == "первая"

class TestWebSearch:
    def test_default_false(self):
        s = _mk_session()
        assert s.web_search is False

    def test_roundtrip(self):
        s = _mk_session()
        s.web_search = True
        d = s.to_dict()
        assert d["web_search"] is True
        s2 = Session.from_dict(d)
        assert s2.web_search is True

    def test_backward_compat(self):
        # Старая сессия без ключа web_search — не должна падать
        s = _mk_session()
        d = s.to_dict()
        d.pop("web_search", None)
        s2 = Session.from_dict(d)
        assert s2.web_search is False

