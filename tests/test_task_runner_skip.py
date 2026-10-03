"""Тест: _TaskRunner не редактирует части, которые не менялись."""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from agent_coder.telegram.handlers import _TaskRunner


class _FakeBot:
    def __init__(self) -> None:
        self.edit_calls: list[int] = []   # message_id, которые редактировались
        self.send_calls: list[str] = []   # тексты, отправленные как новые
        self.deleted: list[int] = []
        self._next_id = 100

    async def edit_message_text(self, chat_id, message_id, text, **kw):
        self.edit_calls.append(message_id)
        return SimpleNamespace(message_id=message_id)

    async def send_message(self, chat_id, text, **kw):
        self.send_calls.append(text)
        self._next_id += 1
        return SimpleNamespace(message_id=self._next_id)

    async def delete_message(self, chat_id, message_id):
        self.deleted.append(message_id)


@pytest.mark.asyncio
async def test_second_flush_skips_unchanged_parts():
    """После первого flush с N частями повторный flush с теми же
    частями не должен дёргать edit_message_text."""
    bot = _FakeBot()
    runner = _TaskRunner(bot, chat_id=42, message_id=1)

    # Собираем буфер так, чтобы получить 2+ части
    runner.buffer = [
        "line " + str(i) + " " + "x" * 100
        for i in range(100)  # ~10 KB → 3+ части по 3900
    ]

    await runner.flush()
    first_edits = list(bot.edit_calls)
    first_sends = list(bot.send_calls)

    # Ничего не меняли → повторный flush
    await runner.flush()

    assert bot.edit_calls == first_edits, (
        f"skip не сработал: было {first_edits}, стало {bot.edit_calls}"
    )
    assert bot.send_calls == first_sends


@pytest.mark.asyncio
async def test_append_only_edits_tail():
    """При добавлении строки редактируется только хвостовая часть."""
    bot = _FakeBot()
    runner = _TaskRunner(bot, chat_id=42, message_id=1)

    runner.buffer = ["line " + str(i) + " " + "x" * 100 for i in range(100)]
    await runner.flush()
    edits_after_first = len(bot.edit_calls)

    # Добавляем строку в конец → меняется только последняя часть
    runner.buffer.append("новый кусок " + "y" * 100)
    await runner.flush()
    new_edits = len(bot.edit_calls) - edits_after_first

    # Реально изменилась только последняя часть (и, возможно, main,
    # если он был «продолжение ниже» и это первый раз, когда len>1).
    # Но заведомо: не все N частей.
    assert new_edits <= 2, f"ожидалось 1–2 edits, было {new_edits}"
