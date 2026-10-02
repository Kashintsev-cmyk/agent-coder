"""Тесты поведения _TaskRunner: вывод вывода и запрос подтверждения.

Ключевой контракт: сообщение с кнопками «Разрешить/Отклонить» всегда
отправляется НОВЫМ сообщением и оказывается под актуальным выводом
(после сброса буфера), а не редактирует старое подтверждение.
"""
from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from agent_coder.telegram import handlers
from agent_coder.telegram.handlers import _TaskRunner


class FakeBot:
    """Минимальный двойник Bot: запоминает вызовы send/edit/delete."""

    def __init__(self) -> None:
        self.sent: list[dict] = []
        self.edited: list[dict] = []
        self.deleted: list[int] = []
        self._next_id = 1000

    async def send_message(self, chat_id, text, reply_markup=None, parse_mode=None):
        self._next_id += 1
        self.sent.append({
            "chat_id": chat_id,
            "text": text,
            "reply_markup": reply_markup,
            "parse_mode": parse_mode,
            "message_id": self._next_id,
        })
        return SimpleNamespace(message_id=self._next_id)

    async def edit_message_text(self, chat_id, message_id, text,
                                reply_markup=None, parse_mode=None):
        self.edited.append({
            "chat_id": chat_id,
            "message_id": message_id,
            "text": text,
            "reply_markup": reply_markup,
        })
        return SimpleNamespace(message_id=message_id)

    async def delete_message(self, chat_id, message_id):
        self.deleted.append(message_id)


def _make_runner() -> tuple[_TaskRunner, FakeBot]:
    bot = FakeBot()
    runner = _TaskRunner(bot, chat_id=42, message_id=1)
    return runner, bot


def _tc(name: str = "run_command", args: dict | None = None):
    return SimpleNamespace(name=name, arguments=args or {"cmd": "ls"})


@pytest.fixture(autouse=True)
def _clear_pending():
    handlers._pending.clear()
    yield
    handlers._pending.clear()


async def _run_approval(runner: _TaskRunner, tc) -> asyncio.Task:
    """Запустить request_approval и дождаться, пока она повесит future."""
    task = asyncio.create_task(runner._request_approval(tc))
    # Дать корутине дойти до ожидания future
    for _ in range(10):
        await asyncio.sleep(0)
        if handlers._pending:
            break
    return task


class TestApprovalAlwaysNewMessage:
    async def test_sends_new_message_with_buttons(self):
        runner, bot = _make_runner()
        task = await _run_approval(runner, _tc())

        # Сразу разрешаем
        rid = next(iter(handlers._pending))
        handlers._pending[rid].set_result(True)
        assert await task is True

        assert len(bot.sent) == 1
        assert bot.sent[0]["reply_markup"] is not None
        # Подтверждение не редактирует основное сообщение вывода
        assert all(e["reply_markup"] is None for e in bot.edited) or not bot.edited

    async def test_two_approvals_keep_single_message(self):
        runner, bot = _make_runner()

        for expected in (True, False):
            task = await _run_approval(runner, _tc())
            rid = next(iter(handlers._pending))
            handlers._pending[rid].set_result(expected)
            assert await task is expected

        # Оба раза — новое сообщение, но предыдущее подтверждение удаляется,
        # поэтому в чате одновременно живёт ровно одна запись с кнопками.
        assert len(bot.sent) == 2
        assert bot.deleted == [bot.sent[0]["message_id"]]


class TestApprovalFlushesBeforeSend:
    async def test_flush_called_before_approval_message(self):
        runner, bot = _make_runner()
        # Накопленный вывод агента, ожидающий сброса
        runner.buffer.append("⏺ какой-то вывод")
        runner.last_edit = float("inf")  # троттлинг заблокировал бы сброс

        task = await _run_approval(runner, _tc())
        rid = next(iter(handlers._pending))
        handlers._pending[rid].set_result(True)
        await task

        # Вывод был сброшен (edit основного сообщения) ДО отправки кнопок
        assert any(e["message_id"] == runner.message_id for e in bot.edited)
        # И сообщение с кнопками — последнее отправленное
        assert bot.sent[-1]["reply_markup"] is not None

    async def test_timeout_removes_buttons(self):
        runner, bot = _make_runner()
        # Обнуляем таймаут ожидания, чтобы сразу сработала ветка timeout
        import agent_coder.telegram.handlers as h
        orig = h.config.APPROVAL_TIMEOUT
        h.config.APPROVAL_TIMEOUT = 0.0
        try:
            task = asyncio.create_task(runner._request_approval(_tc()))
            result = await task
        finally:
            h.config.APPROVAL_TIMEOUT = orig

        assert result is False
        # Кнопки убраны редактированием сообщения с таймаутом
        assert any(
            e["text"] == "⌛ Время ожидания истекло"
            for e in bot.edited
        )
