"""Бюджетные лимиты и отсутствие ротации моделей (защита от сжигания)."""

from __future__ import annotations

import pytest

from agent_coder.agent import loop as loop_mod
from agent_coder.agent.loop import AgentLoop, LoopEvents
from agent_coder.agent.prompts import Mode
from agent_coder.agent.session import Session
from agent_coder.llm.types import (
    AssistantMessage,
    ChatResponse,
    LLMError,
    Usage,
)


class _EmptyRegistry:
    def openai_schemas(self, mode):
        return []

    def get(self, name):
        return None


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


def _session(mode: Mode = Mode.ACT) -> Session:
    s = Session.new(workspace="/tmp", model="m", mode=mode)
    s.messages = [{"role": "system", "content": "S"}]
    return s


def _resp(cost: float, content: str = "готово") -> ChatResponse:
    return ChatResponse(
        message=AssistantMessage(content=content, tool_calls=[]),
        usage=Usage(input_tokens=100, output_tokens=20, cost=cost),
    )


class _CostClient:
    """Каждый вызов стоит cost; отвечает текстом (без tool_calls)."""

    def __init__(self, cost: float):
        self.cost = cost
        self.calls = 0

    async def chat(self, messages, **kwargs):
        self.calls += 1
        return _resp(self.cost)


@pytest.mark.asyncio
async def test_task_cost_limit_stops(monkeypatch) -> None:
    """Один вызов дороже MAX_COST_PER_TASK → стоп cost_limit_reached."""
    monkeypatch.setattr(loop_mod.config, "MAX_COST_PER_TASK", 10.0)
    monkeypatch.setattr(loop_mod.config, "MAX_COST_PER_SESSION", 1000.0)
    s = _session()
    client = _CostClient(cost=50.0)
    loop = AgentLoop(
        client=client, registry=_EmptyRegistry(), session=s, events=_events()
    )
    res = await loop.run("задача")
    assert res.cost_limit_reached
    assert res.task_cost == pytest.approx(50.0)
    assert "Бюджет задачи" in res.error_message
    assert client.calls == 1  # не пошли во вторую итерацию


@pytest.mark.asyncio
async def test_session_cost_limit_stops_before_start(monkeypatch) -> None:
    """Сессия уже за MAX_COST_PER_SESSION → не начинаем."""
    monkeypatch.setattr(loop_mod.config, "MAX_COST_PER_SESSION", 10.0)
    s = _session()
    s.total_usage = Usage(input_tokens=0, output_tokens=0, cost=20.0)
    client = _CostClient(cost=1.0)
    loop = AgentLoop(
        client=client, registry=_EmptyRegistry(), session=s, events=_events()
    )
    res = await loop.run("задача")
    assert res.cost_limit_reached
    assert "Бюджет сессии" in res.error_message
    assert client.calls == 0  # ни одного вызова


@pytest.mark.asyncio
async def test_402_stops_without_rotation(monkeypatch) -> None:
    """LLMError(retryable=False, 402) → provider_error, без ротации."""

    class _BalanceClient:
        calls = 0

        async def chat(self, messages, **kwargs):
            self.calls += 1
            raise LLMError("Недостаточно средств (402)", retryable=False, code=402)

    s = _session()
    client = _BalanceClient()
    loop = AgentLoop(
        client=client, registry=_EmptyRegistry(), session=s, events=_events()
    )
    res = await loop.run("задача")
    assert res.provider_error
    assert "402" in res.error_message or "средств" in res.error_message
    assert client.calls == 1  # ровно один вызов, не цепочка моделей


@pytest.mark.asyncio
async def test_429_stops_without_rotation(monkeypatch) -> None:
    """Обычное исключение (429 после retry) → provider_error, без ротации."""

    class _FlakyClient:
        calls = 0

        async def chat(self, messages, **kwargs):
            self.calls += 1
            raise RuntimeError("429 rate limit")

    s = _session()
    client = _FlakyClient()
    loop = AgentLoop(
        client=client, registry=_EmptyRegistry(), session=s, events=_events()
    )
    res = await loop.run("задача")
    assert res.provider_error
    assert "недоступен" in res.error_message
    assert client.calls == 1  # без ротации модели
