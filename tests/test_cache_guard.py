"""Защита от пропадания кеша провайдера (CacheGuard)."""

from __future__ import annotations

import pytest

from agent_coder.agent import loop as loop_mod
from agent_coder.agent.cache_guard import CacheGuard
from agent_coder.agent.loop import AgentLoop, LoopEvents
from agent_coder.agent.prompts import Mode
from agent_coder.agent.session import Session
from agent_coder.llm.types import AssistantMessage, ChatResponse, Usage


def _guard(streak: int = 2) -> CacheGuard:
    return CacheGuard(min_input=1000, ratio=0.5, streak=streak)


def test_first_request_never_trips() -> None:
    g = _guard()
    assert g.observe(50_000, 0, 50_000) is False


def test_healthy_cache_growth_does_not_trip() -> None:
    g = _guard()
    assert g.observe(10_000, 0, 10_000) is False
    assert g.observe(12_000, 10_000, 2_000) is False
    assert g.observe(14_000, 12_000, 2_000) is False
    assert g.bad_run == 0


def test_two_bad_requests_in_a_row_trip() -> None:
    """Кеш был 100k, затем read=0 при in=110k → 1-й плохой; 2-й плохой → стоп."""
    g = _guard(streak=2)
    assert g.observe(100_000, 90_000, 10_000) is False
    assert g.observe(110_000, 0, 110_000) is False  # первый плохой
    assert g.observe(111_000, 4_000, 107_000) is True  # второй плохой → стоп


def test_good_request_resets_streak() -> None:
    g = _guard(streak=2)
    g.observe(100_000, 90_000, 10_000)
    assert g.observe(110_000, 0, 110_000) is False  # плохой
    assert g.observe(111_000, 110_000, 1_000) is False  # восстановился
    assert g.bad_run == 0
    assert g.observe(112_000, 0, 112_000) is False  # снова один плохой, не стоп


def test_provider_without_cache_is_not_tripped() -> None:
    """Провайдер без кеша: все поля 0 — защита не должна срабатывать."""
    g = _guard(streak=2)
    for _ in range(5):
        assert g.observe(50_000, 0, 0) is False


def test_small_prompt_is_ignored() -> None:
    g = _guard(streak=2)
    g.observe(500, 400, 100)
    assert g.observe(600, 0, 600) is False
    assert g.observe(700, 0, 700) is False


def test_reset_clears_history() -> None:
    g = _guard(streak=2)
    g.observe(100_000, 90_000, 10_000)
    g.observe(110_000, 0, 110_000)  # плохой
    g.reset()
    assert g.bad_run == 0
    # после reset первый запрос — это новая база, не сравнивается
    assert g.observe(111_000, 0, 111_000) is False


def test_streak_must_be_positive() -> None:
    with pytest.raises(ValueError):
        CacheGuard(min_input=1, ratio=0.5, streak=0)


# --- интеграция с AgentLoop ---------------------------------------------------


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


def _session() -> Session:
    s = Session.new(workspace="/tmp", model="m", mode=Mode.ACT)
    s.messages = [{"role": "system", "content": "S"}]
    return s


class _TextClient:
    """Отвечает текстом (без tool_calls); usage задаётся сценарием."""

    def __init__(self, usages: list[Usage]):
        self._usages = usages
        self.calls = 0

    async def chat(self, messages, **kwargs):
        i = min(self.calls, len(self._usages) - 1)
        self.calls += 1
        return ChatResponse(
            message=AssistantMessage(content="ok", tool_calls=[]),
            usage=self._usages[i],
        )


@pytest.mark.asyncio
async def test_loop_stops_on_cache_loss(monkeypatch) -> None:
    monkeypatch.setattr(loop_mod.config, "CACHE_GUARD_ENABLED", True)
    monkeypatch.setattr(loop_mod.config, "CACHE_GUARD_MIN_INPUT", 1000)
    monkeypatch.setattr(loop_mod.config, "CACHE_GUARD_RATIO", 0.5)
    monkeypatch.setattr(loop_mod.config, "CACHE_GUARD_STREAK", 2)
    monkeypatch.setattr(loop_mod.config, "MAX_COST_PER_TASK", 1e9)
    monkeypatch.setattr(loop_mod.config, "MAX_COST_PER_SESSION", 1e9)
    usages = [
        Usage(
            input_tokens=100_000, cache_read_tokens=90_000, cache_write_tokens=10_000
        ),
        Usage(input_tokens=110_000, cache_read_tokens=0, cache_write_tokens=110_000),
        Usage(input_tokens=111_000, cache_read_tokens=0, cache_write_tokens=111_000),
        Usage(input_tokens=112_000, cache_read_tokens=0, cache_write_tokens=112_000),
    ]
    client = _TextClient(usages)
    loop = AgentLoop(
        client=client, registry=_EmptyRegistry(), session=_session(), events=_events()
    )
    # Каждый run() — один запрос (ответ без tool_calls). Состояние защиты
    # живёт на экземпляре AgentLoop, поэтому серия идёт через несколько run().
    res1 = await loop.run("задача 1")  # база кеша
    res2 = await loop.run("задача 2")  # первый плохой
    assert not res1.cache_guard_tripped
    assert not res2.cache_guard_tripped
    res3 = await loop.run("задача 3")  # второй плохой → стоп
    assert res3.cache_guard_tripped
    assert "Кеш промпта" in res3.error_message
    assert client.calls == 3  # стоп на втором плохом запросе, не на четвёртом


@pytest.mark.asyncio
async def test_loop_disabled_guard_does_not_stop(monkeypatch) -> None:
    monkeypatch.setattr(loop_mod.config, "CACHE_GUARD_ENABLED", False)
    monkeypatch.setattr(loop_mod.config, "CACHE_GUARD_MIN_INPUT", 1000)
    monkeypatch.setattr(loop_mod.config, "MAX_COST_PER_TASK", 1e9)
    monkeypatch.setattr(loop_mod.config, "MAX_COST_PER_SESSION", 1e9)
    usages = [
        Usage(
            input_tokens=100_000, cache_read_tokens=90_000, cache_write_tokens=10_000
        ),
        Usage(input_tokens=110_000, cache_read_tokens=0, cache_write_tokens=110_000),
        Usage(input_tokens=111_000, cache_read_tokens=0, cache_write_tokens=111_000),
    ]
    client = _TextClient(usages)
    loop = AgentLoop(
        client=client, registry=_EmptyRegistry(), session=_session(), events=_events()
    )
    for i in range(3):
        res = await loop.run(f"задача {i}")
        assert not res.cache_guard_tripped
    assert client.calls == 3
