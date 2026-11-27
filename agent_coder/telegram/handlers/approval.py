"""Callback'и подтверждения опасных операций (approve/deny).

Работают с _pending из shared — там живут Future'ы, которые ждёт
_TaskRunner в момент approval-запроса.
"""

from __future__ import annotations

import logging
from contextlib import suppress

from aiogram import F
from aiogram.types import CallbackQuery

from .shared import _pending, router

logger = logging.getLogger(__name__)


@router.callback_query(F.data.startswith("approve:"))
async def cb_approve(query: CallbackQuery) -> None:
    rid = query.data.split(":", 1)[1]
    fut = _pending.get(rid)
    if fut and not fut.done():
        fut.set_result(True)
        with suppress(Exception):
            await query.message.edit_text(
                "✅ <b>Разрешено</b>",
                reply_markup=None,
                parse_mode="HTML",
            )
    else:
        await query.answer("Устарело", show_alert=True)
    await query.answer()


@router.callback_query(F.data.startswith("deny:"))
async def cb_deny(query: CallbackQuery) -> None:
    rid = query.data.split(":", 1)[1]
    fut = _pending.get(rid)
    if fut and not fut.done():
        fut.set_result(False)
        with suppress(Exception):
            await query.message.edit_text(
                "❌ <b>Отклонено</b>",
                reply_markup=None,
                parse_mode="HTML",
            )
    else:
        await query.answer("Устарело", show_alert=True)
    await query.answer()
