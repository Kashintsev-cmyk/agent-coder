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
        msgs = [
            {"role": "assistant", "content": "hi"},
            {"role": "user", "content": "сделай фичу"},
        ]
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
            {
                "role": "assistant",
                "tool_calls": [
                    {"id": "1", "name": "x"},
                    {"id": "2", "name": "y"},
                ],
            },
        ]
        assert s.sanitize_tool_calls() == 2
        answered = {m.get("tool_call_id") for m in s.messages if m["role"] == "tool"}
        assert answered == {"1", "2"}

    def test_partial_answers(self):
        """Регрессия: часть tool_calls без ответов — добиваем заглушками.

        Раньше функция возвращала 0 (считала состояние «ок», если последнее
        сообщение — tool), и API падал с 400 (tool_calls without responses)
        после аварийного рестарта. Теперь: добавляем по заглушке на каждый
        неотвеченный id.
        """
        s = _mk_session()
        s.messages = [
            {
                "role": "assistant",
                "tool_calls": [
                    {"id": "1", "name": "x"},
                    {"id": "2", "name": "y"},
                ],
            },
            {"role": "tool", "tool_call_id": "1", "content": "ok"},
        ]
        added = s.sanitize_tool_calls()
        assert added == 1
        assert s.messages[-1]["role"] == "tool"
        assert s.messages[-1]["tool_call_id"] == "2"

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


class TestHistoryStats:
    def test_defaults(self):
        s = _mk_session()
        assert s.last_input_tokens == 0
        assert s.summary_count == 0

    def test_roundtrip(self):
        s = _mk_session()
        s.last_input_tokens = 12345
        s.summary_count = 2
        s2 = Session.from_dict(s.to_dict())
        assert s2.last_input_tokens == 12345
        assert s2.summary_count == 2

    def test_backward_compat(self):
        s = _mk_session()
        d = s.to_dict()
        d.pop("last_input_tokens", None)
        d.pop("summary_count", None)
        s2 = Session.from_dict(d)
        assert s2.last_input_tokens == 0
        assert s2.summary_count == 0


class TestDeleteMedia:
    def test_delete_removes_media_dir(self, tmp_path, monkeypatch):
        from agent_coder.config import config

        monkeypatch.setattr(config, "SESSIONS_DIR", tmp_path)
        s = _mk_session()
        s.save()
        # имитируем два медиа-файла
        p1 = s.save_media(b"a", ext="jpg")
        p2 = s.save_media(b"b", ext="png")
        assert p1.exists() and p2.exists()
        media_dir = p1.parent

        removed = s.delete()
        assert removed >= 3  # session json + 2 медиа
        assert not media_dir.exists()

    def test_delete_no_media_ok(self, tmp_path, monkeypatch):
        from agent_coder.config import config

        monkeypatch.setattr(config, "SESSIONS_DIR", tmp_path)
        s = _mk_session()
        s.save()
        # медиа нет — не падаем
        removed = s.delete()
        assert removed >= 1


class TestSanitizePartialAnswers:
    """Регрессия: после рестарта часть tool_calls без ответов — добиваем."""

    def test_trailing_tool_still_ok(self):
        s = _mk_session()
        s.messages = [
            {"role": "user", "content": "з"},
            {
                "role": "assistant",
                "content": "",
                "tool_calls": [{"id": "x", "name": "read_file"}],
            },
            {"role": "tool", "tool_call_id": "x", "content": "ok"},
        ]
        assert s.sanitize_tool_calls() == 0

    def test_session_atomic_save_no_tmp_left(self, tmp_path, monkeypatch):
        from agent_coder.config import config

        monkeypatch.setattr(config, "SESSIONS_DIR", tmp_path)
        s = _mk_session()
        s.save()
        # ни одного .tmp не осталось
        tmps = list(tmp_path.glob("*.tmp"))
        assert tmps == []
        assert (tmp_path / f"{s.id}.json").exists()


class TestLoadSafety:
    """load() не должен бросать исключения наружу."""

    def test_load_broken_json_returns_none(self, tmp_path, monkeypatch):
        from agent_coder.config import config

        monkeypatch.setattr(config, "SESSIONS_DIR", tmp_path)
        (tmp_path / "broken.json").write_text("{ not json")
        assert Session.load("broken") is None

    def test_load_wrong_mode_returns_none(self, tmp_path, monkeypatch):
        import json as _json

        from agent_coder.config import config

        monkeypatch.setattr(config, "SESSIONS_DIR", tmp_path)
        payload = {
            "id": "x",
            "workspace": "/tmp",
            "model": "m",
            "mode": "NOT_A_MODE",
            "total_usage": {},
        }
        (tmp_path / "x.json").write_text(_json.dumps(payload))
        assert Session.load("x") is None

    def test_load_missing_total_usage_returns_none(self, tmp_path, monkeypatch):
        import json as _json

        from agent_coder.config import config

        monkeypatch.setattr(config, "SESSIONS_DIR", tmp_path)
        payload = {
            "id": "x",
            "workspace": "/tmp",
            "model": "m",
            "mode": "act",
        }
        (tmp_path / "x.json").write_text(_json.dumps(payload))
        assert Session.load("x") is None


def test_sanitize_tool_calls_neutral_prefix() -> None:
    """Заглушка dangling tool_calls НЕ начинается с «Ошибка».

    Аудит 2026-10-19: watchdog (loop.py) считает tool-результаты с
    префиксом «Ошибка» ошибками. После аварийного рестарта с 3
    dangling tool_calls consecutive_errors = 3/5 → ложный стоп.
    """
    from agent_coder.agent.prompts import Mode
    from agent_coder.agent.session import Session

    s = Session.new(workspace="/tmp", model="m", mode=Mode.ACT)
    s.messages = [
        {"role": "system", "content": "S"},
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [
                {
                    "id": "c1",
                    "type": "function",
                    "function": {"name": "read_file", "arguments": "{}"},
                }
            ],
        },
    ]
    fixed = s.sanitize_tool_calls()
    assert fixed == 1
    stub = s.messages[-1]
    assert stub["role"] == "tool"
    assert stub["content"].startswith("Прервано")
    assert not stub["content"].lstrip().startswith("Ошибка")
