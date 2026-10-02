"""Тест: до какого количества знаков Telegram-сообщение доходит до LLM.

Проверяем весь путь без сети:
    message.text → handlers.handle_task → _start_task → loop.run
                 → session.messages → LLMClient.chat(messages=...)

Ожидание: текст пользователя НЕ обрезается нигде в пайплайне, вплоть до
жёсткого лимита Telegram Bot API для входящего текста (4096 символов).
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from agent_coder.agent.loop import AgentLoop
from agent_coder.agent.prompts import Mode
from agent_coder.agent.session import Session
from agent_coder.llm.types import AssistantMessage, ChatResponse, Usage
from agent_coder.telegram import handlers

# Telegram Bot API: "Text of the message to be sent, 1-4096 characters".
# Входящий текст, который бот вообще способен получить, не длиннее 4096.
TELEGRAM_TEXT_LIMIT = 4096


class FakeBot:
    """Минимальный двойник Bot — только send_message."""

    async def send_message(self, chat_id, text, **kwargs):
        return SimpleNamespace(message_id=1)


def _make_message(text: str, chat_id: int = 42):
    msg = SimpleNamespace(chat=SimpleNamespace(id=chat_id), text=text)

    async def answer(t, **kwargs):
        return SimpleNamespace(message_id=1)

    msg.answer = answer
    return msg


@pytest.fixture
def _stub_session(monkeypatch):
    """Подсунуть активную сессию без диска/state-файла."""
    session = Session.new(
        workspace="/tmp/proj", model="test/model", mode=Mode.ACT,
    )
    monkeypatch.setattr(handlers, "_get_active_session", lambda chat_id: session)
    return session


async def _dispatch_text(monkeypatch, text: str) -> str:
    """Прогнать текст через handle_task и вернуть task_text, дошедший до loop."""
    captured: dict[str, str] = {}

    async def fake_run_task(bot, chat_id, session, task_text, images=None):
        captured["task_text"] = task_text

    monkeypatch.setattr(handlers, "_run_task", fake_run_task)

    await handlers.handle_task(_make_message(text), FakeBot())

    # _start_task создаёт asyncio.Task — даём ей выполниться.
    task = handlers._running.get(42)
    if task is not None:
        await task
    handlers._running.clear()
    return captured["task_text"]


@pytest.mark.parametrize("n", [1, 100, 700, 800, 1000, 2000, 3900, 4095, 4096])
async def test_incoming_text_reaches_loop_untruncated(monkeypatch, _stub_session, n):
    """Текст любой длины (до лимита Telegram) доходит до loop.run целиком."""
    text = "Ж" * n
    delivered = await _dispatch_text(monkeypatch, text)
    assert delivered == text
    assert len(delivered) == n


async def test_message_above_telegram_limit_still_passed(monkeypatch, _stub_session):
    """Даже если каким-то образом прилетит >4096 — мы не режем его сами.

    Тест фиксирует, что наш код НЕ добавляет собственного лимита:
    обрезку выполняет только Telegram до доставки боту.
    """
    text = "A" * (TELEGRAM_TEXT_LIMIT + 500)
    delivered = await _dispatch_text(monkeypatch, text)
    assert delivered == text


async def test_user_text_stored_in_session_verbatim(_stub_session):
    """AgentLoop.run кладёт user-сообщение в сессию без изменений."""
    text = "Б" * 4096
    captured: dict = {}

    class FakeClient:
        async def chat(self, messages, **kwargs):
            captured["messages"] = messages
            return ChatResponse(
                message=AssistantMessage(content="ok"),
                usage=Usage(),
            )

    loop = AgentLoop(
        client=FakeClient(),
        registry=_EmptyRegistry(),
        session=_stub_session,
        events=_NullEvents(),
    )
    await loop.run(text)

    user_msgs = [m for m in _stub_session.messages if m["role"] == "user"]
    assert user_msgs[-1]["content"] == text
    # и в LLM ушёл ровно тот же (не обрезанный) текст
    sent_user = [m for m in captured["messages"] if m["role"] == "user"]
    assert sent_user[-1]["content"] == text


class _EmptyRegistry:
    def openai_schemas(self, mode):
        return []

    def get(self, name):
        return None


class _NullEvents:
    def on_assistant_text(self, *a): ...
    def on_tool_call(self, *a): ...
    def on_tool_result(self, *a): ...
    def on_usage(self, *a): ...
    def on_error(self, *a): ...
    async def request_approval(self, *a): return True
