"""Смена режима НЕ сбрасывает историю сообщений.

Баг: /mode, кнопка режима в меню сессии и CLI-команды /plan /act /audit
очищали session.messages. Юзер терял контекст диалога, хотя режим влияет
только на system prompt и набор инструментов — а system prompt
пересобирается на каждом запуске (AgentLoop._refresh_system_prompt).
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from agent_coder.agent.prompts import Mode
from agent_coder.agent.session import Session
from agent_coder.telegram.handlers import commands


def _session_with_history(mode: Mode = Mode.ACT) -> Session:
    s = Session.new(workspace="/tmp/proj", model="m", mode=mode)
    s.messages = [
        {"role": "system", "content": "S"},
        {"role": "user", "content": "что в файле X?"},
        {"role": "assistant", "content": "в X находится Y"},
    ]
    s.save()
    return s


def _msg(text: str) -> MagicMock:
    m = MagicMock()
    m.text = text
    m.chat.id = 1
    m.answer = AsyncMock()
    return m


def _cb(data: str) -> MagicMock:
    q = MagicMock()
    q.data = data
    q.answer = AsyncMock()
    q.message = MagicMock()
    q.message.edit_text = AsyncMock()
    return q


async def test_mode_command_keeps_messages(monkeypatch: pytest.MonkeyPatch) -> None:
    s = _session_with_history(Mode.ACT)
    monkeypatch.setattr(commands, "_get_active_session", lambda chat_id: s)

    await commands.cmd_mode(_msg("/mode audit"))

    reloaded = Session.load(s.id)
    assert reloaded is not None
    assert reloaded.mode == Mode.AUDIT
    assert len(reloaded.messages) == 3


async def test_mode_callback_keeps_messages(monkeypatch: pytest.MonkeyPatch) -> None:
    s = _session_with_history(Mode.ACT)

    await commands.cb_mode(_cb(f"mode:plan:{s.id}"))

    reloaded = Session.load(s.id)
    assert reloaded is not None
    assert reloaded.mode == Mode.PLAN
    assert len(reloaded.messages) == 3


def test_cli_mode_slash_keeps_messages(monkeypatch: pytest.MonkeyPatch) -> None:
    import asyncio

    from agent_coder import __main__ as cli

    monkeypatch.setattr(cli.ui, "print_success", lambda *a, **k: None)
    s = _session_with_history(Mode.ACT)

    asyncio.run(cli._handle_slash("/audit", s))

    assert s.mode == Mode.AUDIT
    assert len(s.messages) == 3
