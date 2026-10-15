"""Тесты сквозного логирования сжатия: compress() → LoopEvents → UI.

Проверяем, что при срабатывании сжатия агент уведомляет UI
(в Telegram это сообщение «📦 История сжата: …»).
"""
from __future__ import annotations

import pytest

from agent_coder.agent.compression import CompressionResult
from agent_coder.agent.loop import AgentLoop, LoopEvents
from agent_coder.agent.prompts import Mode
from agent_coder.agent.session import Session
from agent_coder.llm.types import AssistantMessage, ChatResponse, Usage


class _EmptyRegistry:
    def openai_schemas(self, mode):
        return []

    def get(self, name):
        return None


def _mk_session(**kw) -> Session:
    s = Session.new(workspace="/tmp", model="test/model", mode=Mode.ACT)
    s.messages = [{"role": "system", "content": "SYS"}]
    for i in range(14):
        s.messages.append({"role": "user", "content": f"u{i} " + "x" * 400})
        s.messages.append({"role": "assistant", "content": f"a{i} " + "y" * 400})
    for k, v in kw.items():
        setattr(s, k, v)
    return s


class _SummaryClient:
    """Клиент, отвечающий на aux-вызов суммаризации."""

    def __init__(self):
        self.calls = 0

    async def chat(self, messages, **kwargs):
        self.calls += 1
        if kwargs.get("model") == "summary-model":
            return ChatResponse(
                message=AssistantMessage(content="РЕЗЮМЕ"),
                usage=Usage(input_tokens=10, output_tokens=5, cost=0.001),
            )
        return ChatResponse(
            message=AssistantMessage(content="готово"),
            usage=Usage(input_tokens=100, output_tokens=7, cost=0.01),
        )


def _events(captured: list) -> LoopEvents:
    async def on_compression(comp):
        captured.append(comp)

    async def request_approval(tc):
        return True

    return LoopEvents(
        on_assistant_text=lambda *a: None,
        on_tool_call=lambda *a: None,
        on_tool_result=lambda *a: None,
        on_usage=lambda *a: None,
        on_error=lambda *a: None,
        request_approval=request_approval,
        on_compression=on_compression,
    )


@pytest.mark.asyncio
async def test_loop_emits_compression_event(monkeypatch):
    """last_input_tokens выше порога → сжатие → событие в UI."""
    from agent_coder.agent import loop as loop_mod

    monkeypatch.setattr(loop_mod.config, "SUMMARY_MODEL", "summary-model")
    captured: list[CompressionResult] = []
    session = _mk_session(last_input_tokens=10**9)

    loop = AgentLoop(
        client=_SummaryClient(), registry=_EmptyRegistry(),
        session=session, events=_events(captured),
    )
    await loop.run("задача")

    assert len(captured) == 1
    comp = captured[0]
    assert comp.summary_count == 1
    assert comp.tokens_before > comp.tokens_after
    assert comp.messages_before > comp.messages_after


@pytest.mark.asyncio
async def test_loop_no_event_when_below_threshold(monkeypatch):
    """Порог не превышен — сжатия и события нет."""
    captured: list[CompressionResult] = []
    session = _mk_session(last_input_tokens=0)

    loop = AgentLoop(
        client=_SummaryClient(), registry=_EmptyRegistry(),
        session=session, events=_events(captured),
    )
    await loop.run("задача")

    assert captured == []
    assert session.summary_count == 0


@pytest.mark.asyncio
async def test_loop_works_without_on_compression(monkeypatch):
    """on_compression необязателен (CLI-режим) — цикл не падает."""
    from agent_coder.agent import loop as loop_mod

    monkeypatch.setattr(loop_mod.config, "SUMMARY_MODEL", "summary-model")
    session = _mk_session(last_input_tokens=10**9)

    async def request_approval(tc):
        return True

    events = LoopEvents(
        on_assistant_text=lambda *a: None,
        on_tool_call=lambda *a: None,
        on_tool_result=lambda *a: None,
        on_usage=lambda *a: None,
        on_error=lambda *a: None,
        request_approval=request_approval,
    )
    loop = AgentLoop(
        client=_SummaryClient(), registry=_EmptyRegistry(),
        session=session, events=events,
    )
    await loop.run("задача")
    assert session.summary_count == 1


def test_on_compression_defaults_to_none():
    async def request_approval(tc):
        return True

    events = LoopEvents(
        on_assistant_text=lambda *a: None,
        on_tool_call=lambda *a: None,
        on_tool_result=lambda *a: None,
        on_usage=lambda *a: None,
        on_error=lambda *a: None,
        request_approval=request_approval,
    )
    assert events.on_compression is None
