"""Pre-check сжатия: учитываем сообщения, добавленные после прошлого LLM-запроса.

Проблема (аудит 2026-10-19): compress срабатывал по last_input_tokens > soft,
но между прошлым запросом и текущим добавились tool-результаты (до 4096 симв.
каждый). last=58000 < soft=60000, а 5 результатов добавили ~5000 токенов →
текущий запрос мог упереться в контекст модели.

Фикс (Cline-паттерн): effective = last_input_tokens + estimate_tokens(новые
сообщения с прошлого client.chat).
"""

from __future__ import annotations

import pytest

from agent_coder.agent import loop as loop_mod
from agent_coder.agent.loop import AgentLoop, LoopEvents
from agent_coder.agent.prompts import Mode
from agent_coder.agent.session import Session
from agent_coder.llm.types import AssistantMessage, ChatResponse, ToolCall, Usage


class _Tool:
    name = "read_file"
    read_only = True
    requires_approval = False

    def needs_approval(self, args):
        return False

    async def executor(self, args, ctx):
        # Крупный результат: имитирует tool, добавляющий много токенов.
        return f"{args.get('path', 'x.py')} (500 строк):\n" + ("x" * 8000)


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


class _Client:
    """Первый запрос: read_file (крупный). Второй: финал."""

    def __init__(self):
        self.calls = 0
        self.input_tokens_seen: list[int] = []

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
async def test_precompress_triggers_on_delta(monkeypatch) -> None:
    """last_input_tokens ниже soft, но delta новых сообщений выводит за порог.

    Сценарий: soft маленький (100). Первый запрос: last=10 (ниже). После
    read_file добавлен крупный tool-результат (~2000 токенов). Перед вторым
    запросом pre-check должен увидеть effective = 10 + ~2000 > 100 и вызвать
    compress.
    """
    # Уменьшаем soft, чтобы delta сработала.
    monkeypatch.setattr(loop_mod.compression, "soft_limit", lambda s: 100)

    compressed = {"count": 0}

    async def fake_compress(session, client):
        compressed["count"] += 1

    monkeypatch.setattr(loop_mod.compression, "compress", fake_compress)
    monkeypatch.setattr(loop_mod.config, "MAX_COST_PER_TASK", 10_000.0)
    monkeypatch.setattr(loop_mod.config, "MAX_COST_PER_SESSION", 10_000.0)
    monkeypatch.setattr(loop_mod.config, "MAX_ITERATIONS_NO_PROGRESS", 50)

    s = Session.new(workspace="/tmp", model="m", mode=Mode.ACT)
    s.messages = [{"role": "system", "content": "S"}]
    s.last_input_tokens = 10  # ниже soft=100
    client = _Client()
    loop = AgentLoop(client=client, registry=_Registry(), session=s, events=_events())
    await loop.run("задача")

    # compress должен был вызваться на 2-й итерации (delta > soft)
    assert compressed["count"] >= 1, (
        "pre-check не сработал: delta новых сообщений не учтён"
    )


@pytest.mark.asyncio
async def test_precompress_not_triggered_without_delta(monkeypatch) -> None:
    """last_input_tokens ниже soft, новых сообщений нет — compress не нужен."""
    monkeypatch.setattr(loop_mod.compression, "soft_limit", lambda s: 100_000)

    compressed = {"count": 0}

    async def fake_compress(session, client):
        compressed["count"] += 1

    monkeypatch.setattr(loop_mod.compression, "compress", fake_compress)
    monkeypatch.setattr(loop_mod.config, "MAX_COST_PER_TASK", 10_000.0)
    monkeypatch.setattr(loop_mod.config, "MAX_COST_PER_SESSION", 10_000.0)
    monkeypatch.setattr(loop_mod.config, "MAX_ITERATIONS_NO_PROGRESS", 50)

    class _FinalClient:
        async def chat(self, messages, **kwargs):
            return ChatResponse(
                message=AssistantMessage(content="готово", tool_calls=[]),
                usage=Usage(input_tokens=10, output_tokens=5, cost=0.0),
            )

    s = Session.new(workspace="/tmp", model="m", mode=Mode.ACT)
    s.messages = [{"role": "system", "content": "S"}]
    s.last_input_tokens = 10
    loop = AgentLoop(
        client=_FinalClient(), registry=_Registry(), session=s, events=_events()
    )
    await loop.run("задача")

    assert compressed["count"] == 0
