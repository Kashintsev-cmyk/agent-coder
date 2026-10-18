"""Фаза 3: work_state как скелет резюме при compress().

Проверяем:
- пустой work_state — поведение идентично старому (обратная совместимость);
- непустой — блок [WORK_STATE] в финальном сообщении (детерминированный
  suffix, не LLM-генерирован);
- факты из work_state, которых нет в transcript, НЕ помечаются
  (не подтверждено) — extra_known в _validate_summary;
- work_state в [ИЗВЕСТНЫЕ ФАКТЫ] для LLM (narrative не противоречит);
- state переживает compress (не очищается);
- _is_summary по-прежнему матчит сообщение (suffix не ломает startswith).
"""

from __future__ import annotations

import pytest

from agent_coder.agent.compression import _is_summary, compress
from agent_coder.agent.prompts import Mode
from agent_coder.agent.session import Session
from agent_coder.llm.types import Usage


class _FakeMessage:
    def __init__(self, content: str):
        self.content = content
        self.tool_calls: list = []


class _FakeResponse:
    def __init__(self, content: str, usage: Usage):
        self.message = _FakeMessage(content)
        self.usage = usage


class _FakeClient:
    def __init__(self, reply: str = "### Задача\n- x\n\n### Сделано\n- y"):
        self.reply = reply
        self.calls: list[dict] = []

    async def chat(self, messages, **kwargs):
        self.calls.append({"messages": messages, "kwargs": kwargs})
        return _FakeResponse(
            self.reply,
            Usage(input_tokens=100, output_tokens=20, cost=0.01),
        )


def _mk_session() -> Session:
    s = Session.new(workspace="/tmp", model="openai/gpt-5.6-luna", mode=Mode.ACT)
    s.messages = [{"role": "system", "content": "SYSTEM"}]
    for i in range(14):
        s.messages.append({"role": "user", "content": f"задача {i}"})
        s.messages.append({"role": "assistant", "content": f"ответ {i}"})
    return s


@pytest.mark.asyncio
async def test_compress_without_work_state_unchanged() -> None:
    """Пустой work_state — нет ни блока в промте, ни suffix в резюме."""
    s = _mk_session()
    client = _FakeClient()
    res = await compress(s, client)
    assert res is not None
    # В промт LLM не передан блок известных фактов
    user_msg = client.calls[0]["messages"][1]["content"]
    assert "[ИЗВЕСТНЫЕ ФАКТЫ" not in user_msg
    # В финальном резюме — нет [WORK_STATE]
    summary_msg = s.messages[1]
    assert "[WORK_STATE]" not in summary_msg["content"]


@pytest.mark.asyncio
async def test_compress_with_work_state_has_block_in_prompt() -> None:
    """Непустой state → блок [ИЗВЕСТНЫЕ ФАКТЫ] в user_content для LLM."""
    s = _mk_session()
    s.work_state = {"findings": ["src/x.py:42 — KeyError"], "todo": ["fix"]}
    client = _FakeClient()
    await compress(s, client)
    user_msg = client.calls[0]["messages"][1]["content"]
    assert "[ИЗВЕСТНЫЕ ФАКТЫ (work_state)]" in user_msg
    assert "src/x.py:42" in user_msg


@pytest.mark.asyncio
async def test_work_state_appended_to_summary() -> None:
    """Детерминированный suffix: [WORK_STATE] в финальном сообщении."""
    s = _mk_session()
    s.work_state = {"findings": ["src/x.py:42 — KeyError"], "todo": ["fix"]}
    client = _FakeClient()
    await compress(s, client)
    summary_msg = s.messages[1]
    assert "[WORK_STATE]" in summary_msg["content"]
    assert "src/x.py:42" in summary_msg["content"]
    assert "fix" in summary_msg["content"]


@pytest.mark.asyncio
async def test_summary_still_matched_by_is_summary() -> None:
    """suffix [WORK_STATE] не ломает startswith-проверку _is_summary."""
    s = _mk_session()
    s.work_state = {"findings": ["a"]}
    await compress(s, _FakeClient())
    assert _is_summary(s.messages[1])


@pytest.mark.asyncio
async def test_work_state_not_marked_unconfirmed() -> None:
    """Факт из work_state, которого нет в transcript, не помечается."""
    s = _mk_session()
    # Этот факт — только в work_state, в messages его нет
    s.work_state = {"findings": ["src/unique.py:99 — KeyError"]}
    client = _FakeClient()
    await compress(s, client)
    summary = s.messages[1]["content"]
    # В suffix факт есть, и он не должен быть помечен
    assert "src/unique.py:99" in summary
    # Извлекаем строку с фактом
    line = next(ln for ln in summary.splitlines() if "src/unique.py:99" in ln)
    assert "(не подтверждено)" not in line


@pytest.mark.asyncio
async def test_work_state_survives_compress() -> None:
    """compress не очищает work_state (persistent across compaction)."""
    s = _mk_session()
    s.work_state = {"findings": ["a"], "todo": ["b"]}
    await compress(s, _FakeClient())
    assert s.work_state == {"findings": ["a"], "todo": ["b"]}


@pytest.mark.asyncio
async def test_second_compress_uses_prev_summary_and_state() -> None:
    """Update-режим: prev_summary + work_state переданы, нет ложных пометок."""
    s = _mk_session()
    s.work_state = {"findings": ["src/a.py:10 — err"]}
    await compress(s, _FakeClient())
    assert s.summary_count == 1
    # Добавляем новые сообщения поверх recent
    for i in range(14):
        s.messages.append({"role": "user", "content": f"новая {i}"})
        s.messages.append({"role": "assistant", "content": f"ответ {i}"})
    s.last_input_tokens = 10**9
    client2 = _FakeClient(reply="### Задача\n- z")
    res2 = await compress(s, client2)
    assert res2 is not None
    user_msg = client2.calls[0]["messages"][1]["content"]
    # prev_summary содержит [WORK_STATE] из первого сжатия
    assert "ПРЕДЫДУЩЕЕ РЕЗЮМЕ" in user_msg
    # и новый work_state передан как известные факты
    assert "[ИЗВЕСТНЫЕ ФАКТЫ (work_state)]" in user_msg
    assert "src/a.py:10" in user_msg
