"""read_paths: прочитанные файлы переживают compress (защита от повторов).

Сессия 6cc7d5bd: 92 read_file, 37 уникальных, 55 повторов после 5 сжатий.
compress() выбрасывает историю чтений → модель не помнит → перечитывает.
read_paths — детерминированный digest (header + def/class/import),
инжектится в system prompt блоком [ПРОЧИТАННЫЕ ФАЙЛЫ].
"""

from __future__ import annotations

import pytest

from agent_coder.agent import loop as loop_mod
from agent_coder.agent.loop import AgentLoop, LoopEvents
from agent_coder.agent.prompts import Mode, system_prompt
from agent_coder.agent.session import Session
from agent_coder.agent.work_state import make_read_digest, render_read_paths
from agent_coder.llm.types import AssistantMessage, ChatResponse, ToolCall, Usage

# ---------- make_read_digest ----------


def test_digest_header_and_defs() -> None:
    result = (
        "x.py (10 строк):\n"
        "    1│ import os\n"
        "    2│ def foo():\n"
        "    3│ class Bar:\n"
        "    4│ from a import b\n"
    )
    d = make_read_digest("x.py", result)
    assert d is not None
    assert "x.py (10 строк)" in d
    assert "import os" in d
    assert "def foo" in d
    assert "class Bar" in d


def test_digest_capped_200() -> None:
    result = "x.py (1):\n" + "\n".join(f"    {i}│ def f{i}():" for i in range(50))
    d = make_read_digest("x.py", result)
    assert d is not None
    assert len(d) <= 200


def test_digest_error_returns_none() -> None:
    for bad in (
        "Ошибка: не указан path",
        "Файл не найден: x.py",
        "Файл слишком большой (999999 байт).",
    ):
        assert make_read_digest("x.py", bad) is None


def test_digest_empty_returns_none() -> None:
    assert make_read_digest("x.py", "") is None
    assert make_read_digest("x.py", "   \n  ") is None


# ---------- render_read_paths ----------


def test_render_empty() -> None:
    assert render_read_paths(None) == ""
    assert render_read_paths({}) == ""


def test_render_block() -> None:
    out = render_read_paths({"a.py": "a.py (5): def foo", "b.py": "b.py (3): x"})
    assert "[ПРОЧИТАННЫЕ ФАЙЛЫ]" in out
    assert "a.py" in out and "b.py" in out
    assert "не перечитывай" in out


def test_render_hidden_counter() -> None:
    paths = {f"f{i}.py": f"f{i}.py" for i in range(80)}
    out = render_read_paths(paths)
    assert "файлов скрыто" in out


# ---------- system_prompt ----------


def test_prompt_includes_read_paths() -> None:
    p = system_prompt(Mode.AUDIT, "/tmp", read_paths={"x.py": "x.py (5): def foo"})
    assert "[ПРОЧИТАННЫЕ ФАЙЛЫ]" in p
    assert "x.py" in p


def test_prompt_without_read_paths_no_block() -> None:
    """Без read_paths блока нет (правило в audit_standard.md упоминает
    название — проверяем специфичную строку блока, не подстроку)."""
    assert "[ПРОЧИТАННЫЕ ФАЙЛЫ] — уже читал" not in system_prompt(Mode.AUDIT, "/tmp")


# ---------- session roundtrip ----------


def test_read_paths_roundtrip() -> None:
    s = Session.new(workspace="/tmp", model="m", mode=Mode.ACT)
    s.read_paths = {"x.py": "x.py (5): def foo"}
    s2 = Session.from_dict(s.to_dict())
    assert s2.read_paths == {"x.py": "x.py (5): def foo"}


def test_read_paths_from_old_session() -> None:
    """Старые сессии без read_paths грузятся с пустым dict."""
    old = {
        "id": "old",
        "workspace": "/tmp",
        "model": "m",
        "mode": "act",
        "messages": [],
        "total_usage": {
            "input_tokens": 0,
            "output_tokens": 0,
            "cache_read_tokens": 0,
            "cache_write_tokens": 0,
            "cost": 0.0,
        },
    }
    s = Session.from_dict(old)
    assert s.read_paths == {}


# ---------- loop: запись digest + сброс seen_paths при compress ----------


class _Tool:
    name = "read_file"
    read_only = True
    requires_approval = False

    def needs_approval(self, args):
        return False

    async def executor(self, args, ctx):
        path = args.get("path", "x.py")
        return f"{path} (10 строк):\n    1│ import os\n    2│ def foo():"


class _Registry:
    def openai_schemas(self, mode):
        return []

    def get(self, name):
        return _Tool() if name == "read_file" else None

    def is_call_allowed(self, mode, tool, args):
        return True


def _events() -> LoopEvents:
    async def request_approval(tc):
        return True

    return LoopEvents(
        on_assistant_text=lambda *a: None,
        on_tool_call=lambda *a: None,
        on_tool_result=lambda *a: None,
        on_usage=lambda *a: None,
        on_error=lambda *a: None,
        request_approval=request_approval,
    )


class _ReadOnceClient:
    def __init__(self):
        self.calls = 0

    async def chat(self, messages, **kwargs):
        self.calls += 1
        if self.calls == 1:
            return ChatResponse(
                message=AssistantMessage(
                    content="",
                    tool_calls=[
                        ToolCall(id="c1", name="read_file", arguments={"path": "a.py"})
                    ],
                ),
                usage=Usage(input_tokens=10, output_tokens=5, cost=0.0),
            )
        return ChatResponse(
            message=AssistantMessage(content="готово", tool_calls=[]),
            usage=Usage(input_tokens=10, output_tokens=5, cost=0.0),
        )


@pytest.mark.asyncio
async def test_loop_records_read_paths() -> None:
    """После read_file в session.read_paths появляется digest."""
    s = Session.new(workspace="/tmp", model="m", mode=Mode.AUDIT)
    s.messages = [{"role": "system", "content": "S"}]
    loop = AgentLoop(
        client=_ReadOnceClient(), registry=_Registry(), session=s, events=_events()
    )
    await loop.run("аудит")
    assert "a.py" in s.read_paths
    assert "def foo" in s.read_paths["a.py"]


@pytest.mark.asyncio
async def test_compress_resets_seen_paths(monkeypatch) -> None:
    """После compress перечитывание — легитимно (seen_paths сброшен)."""
    # Проверяем косвенно: run() не должен падать по no_progress, если
    # compress сработал и модель перечитывает те же файлы.
    # Эмулируем: MAX_ITERATIONS_NO_PROGRESS=2, compress вызывается после
    # первого чтения, затем модель читает тот же файл ещё раз.
    monkeypatch.setattr(loop_mod.config, "MAX_ITERATIONS_NO_PROGRESS", 3)
    monkeypatch.setattr(loop_mod.config, "MAX_COST_PER_TASK", 10_000.0)
    monkeypatch.setattr(loop_mod.config, "MAX_COST_PER_SESSION", 10_000.0)

    compressed = {"done": False}

    async def fake_compress(session, client):
        compressed["done"] = True

    monkeypatch.setattr(loop_mod.compression, "compress", fake_compress)

    class _AlwaysSameRead:
        def __init__(self):
            self.calls = 0

        async def chat(self, messages, **kwargs):
            self.calls += 1
            if self.calls > 8:
                return ChatResponse(
                    message=AssistantMessage(content="готово", tool_calls=[]),
                    usage=Usage(input_tokens=10, output_tokens=5, cost=0.0),
                )
            return ChatResponse(
                message=AssistantMessage(
                    content="",
                    tool_calls=[
                        ToolCall(
                            id=f"c{self.calls}",
                            name="read_file",
                            arguments={"path": "same.py"},
                        )
                    ],
                ),
                usage=Usage(input_tokens=10, output_tokens=5, cost=0.0),
            )

    s = Session.new(workspace="/tmp", model="m", mode=Mode.AUDIT)
    s.messages = [{"role": "system", "content": "S"}]
    s.last_input_tokens = 10**9  # compress сработает на первой итерации
    client = _AlwaysSameRead()
    loop = AgentLoop(client=client, registry=_Registry(), session=s, events=_events())
    await loop.run("аудит")
    assert compressed["done"], "compress должен был вызваться"
