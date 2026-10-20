"""Аварийное сжатие при огромном recent-блоке (Риск 1).

Обычная защита MIN_OLD_SHARE (20%) блокирует сжатие, когда old — малая
доля истории (огромный recent). Но если total >> soft_limit, это
единственное спасение: old будет копиться, пока не упрёмся. Аварийный
режим сжимает принудительно, но только если в old есть что сжимать
(>= EMERGENCY_MIN_OLD_TOKENS) — иначе «резюме от резюме» ради нуля.
"""

from __future__ import annotations

import pytest

from agent_coder.agent.compression import compress
from agent_coder.agent.prompts import Mode
from agent_coder.agent.session import Session
from agent_coder.llm.types import Usage


@pytest.fixture(autouse=True)
def _fixed_history_cap(monkeypatch):
    """Тесты рассчитаны на soft_limit ≈ 60K (см. комментарии в тестах).

    ``openai/gpt-5.6-luna`` в каталоге: context=0 → ``soft_limit`` =
    ``MAX_HISTORY_TOKENS``. Локальный .env содержит 60000 (B11.0 поднял
    дефолт, но .env перебивает); в CI-sim .env нет → 150000 → тест
    test_emergency_compress_when_total_far_over_soft («150K recent >> soft*2»)
    ломается. Фиксируем cap, чтобы файл не зависел от окружения.
    """
    monkeypatch.setattr("agent_coder.config.config.MAX_HISTORY_TOKENS", 60000)


class _FakeMessage:
    def __init__(self, content: str):
        self.content = content
        self.tool_calls: list = []


class _FakeResponse:
    def __init__(self, content: str, usage: Usage):
        self.message = _FakeMessage(content)
        self.usage = usage


class _FakeClient:
    def __init__(self, reply: str = "### Задача\n- x"):
        self.reply = reply
        self.calls: list[dict] = []

    async def chat(self, messages, **kwargs):
        self.calls.append({"messages": messages, "kwargs": kwargs})
        return _FakeResponse(
            self.reply, Usage(input_tokens=100, output_tokens=20, cost=0.01)
        )


def _mk_session() -> Session:
    return Session.new(workspace="/tmp", model="openai/gpt-5.6-luna", mode=Mode.ACT)


@pytest.mark.asyncio
async def test_emergency_compress_when_total_far_over_soft() -> None:
    """old ~4k токенов (>=3000), recent огромный, total >> soft → сжимаем."""
    s = _mk_session()
    s.messages = [{"role": "system", "content": "S"}]
    # old: 10 пар (~5k токенов, >= EMERGENCY_MIN_OLD_TOKENS=3000)
    for i in range(10):
        s.messages.append({"role": "user", "content": f"задача {i} " + "x" * 1000})
        s.messages.append({"role": "assistant", "content": f"ответ {i} " + "y" * 1000})
    # recent: ровно 6 сообщений (find_cut min_recent=6), ~150k токенов > soft*2
    for _ in range(3):
        s.messages.append({"role": "user", "content": "z" * 100_000})
        s.messages.append({"role": "assistant", "content": "w" * 100_000})

    client = _FakeClient()
    comp = await compress(s, client)
    assert comp is not None, "аварийный режим должен сжать"
    assert len(client.calls) == 1
    # summary_count инкрементнут, recent сохранён
    assert s.summary_count == 1


@pytest.mark.asyncio
async def test_emergency_not_triggered_when_old_too_small() -> None:
    """old < EMERGENCY_MIN_OLD_TOKENS → даже при total >> soft пропускаем."""
    s = _mk_session()
    s.messages = [{"role": "system", "content": "S"}]
    # old: 10 пар, но крошечных (~50 токенов всего, < 3000)
    for i in range(10):
        s.messages.append({"role": "user", "content": f"u{i}"})
        s.messages.append({"role": "assistant", "content": f"a{i}"})
    # recent: ровно 6 сообщений, ~150k токенов > soft*2 — но old мал
    for _ in range(3):
        s.messages.append({"role": "user", "content": "z" * 100_000})
        s.messages.append({"role": "assistant", "content": "w" * 100_000})

    client = _FakeClient()
    comp = await compress(s, client)
    assert comp is None, "old слишком мал — сжатие бесполезно, пропускаем"
    assert len(client.calls) == 0


@pytest.mark.asyncio
async def test_normal_share_unaffected_by_emergency() -> None:
    """Обычный путь (share >= MIN_OLD_SHARE) не задет аварией."""
    s = _mk_session()
    s.messages = [{"role": "system", "content": "S"}]
    for i in range(14):
        s.messages.append({"role": "user", "content": f"задача {i}"})
        s.messages.append({"role": "assistant", "content": f"ответ {i}"})

    client = _FakeClient()
    comp = await compress(s, client)
    assert comp is not None
    assert len(client.calls) == 1
