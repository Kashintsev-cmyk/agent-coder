"""Меню сессии: модель, reasoning, verbosity, tier, web_search.

Все callbacks, которые меняют per-session настройки.
"""
from __future__ import annotations

import logging

from aiogram import F
from aiogram.types import CallbackQuery

from agent_coder.model_catalog import catalog
from agent_coder.models import feature_badges, get_model_name
from agent_coder.telegram import keyboards
from agent_coder.telegram.renderer import html_escape

from .shared import (
    _fmt_cost,
    _get_active_session,
    router,
)

logger = logging.getLogger(__name__)


# ---------- Меню сессии: модель, reasoning ----------

@router.callback_query(F.data == "menu_model")
async def cb_menu_model(query: CallbackQuery) -> None:
    session = _get_active_session(query.message.chat.id)
    if not session:
        await query.answer("Нет активной сессии", show_alert=True)
        return
    await query.message.edit_text(
        "🧠 <b>Выбор модели</b>\n\n"
        "Бейджи: 👁 vision · 🌐 web search · 🧠 reasoning · ⚙ reasoning levels · 🔧 tools\n\n"
        f"Текущая: <code>{session.model}</code>",
        reply_markup=keyboards.models_keyboard(session.model),
        parse_mode="HTML",
    )
    await query.answer()


@router.callback_query(F.data.startswith("info:"))
async def cb_model_info(query: CallbackQuery) -> None:
    """Карточка модели: описание + метрики + кнопки выбора/назад."""

    model_id = query.data.split(":", 1)[1]
    m = catalog.get(model_id)
    if m is None:
        await query.answer("Модель не найдена", show_alert=True)
        return

    session = _get_active_session(query.message.chat.id)
    is_current = bool(session and session.model == model_id)

    badges = feature_badges(m)
    badges_line = f" · {badges}" if badges else ""

    ctx = m.context
    ctx_str = f"{ctx:,}".replace(",", " ") if ctx else "—"

    lines = [
        f"🧠 <b>{html_escape(m.name)}</b>{badges_line}",
        "",
        html_escape(m.description) if m.description else "<i>(нет описания)</i>",
        "",
        f"📊 Контекст: <b>{ctx_str}</b> токенов",
        f"💰 Вход: <b>{m.input_price:.1f}₽</b> · Выход: <b>{m.output_price:.0f}₽</b> за 1M",
    ]
    if m.tag:
        lines.append(f"🏷 {html_escape(m.tag)}")
    if is_current:
        lines.append("")
        lines.append("✅ <b>Это текущая модель</b>")

    await query.message.edit_text(
        "\n".join(lines),
        reply_markup=keyboards.model_card_keyboard(model_id, is_current),
        parse_mode="HTML",
    )
    await query.answer()


@router.callback_query(F.data.startswith("setmodel:"))
async def cb_setmodel(query: CallbackQuery) -> None:
    model_id = query.data.split(":", 1)[1]
    session = _get_active_session(query.message.chat.id)
    if not session:
        await query.answer("Нет сессии", show_alert=True)
        return
    session.model = model_id
    session.model_manual = True  # ручной выбор — роутер не перекрывает
    session.messages = []  # сбрасываем историю — модель поменялась
    session.save()
    await query.message.edit_text(
        f"✅ Модель: <b>{get_model_name(model_id)}</b>\n"
        f"<code>{model_id}</code>\n\n"
        f"История сообщений очищена.",
        reply_markup=keyboards.session_menu_keyboard(session),
        parse_mode="HTML",
    )
    await query.answer(f"→ {get_model_name(model_id)}")


@router.callback_query(F.data == "menu_reasoning")
async def cb_menu_reasoning(query: CallbackQuery) -> None:
    session = _get_active_session(query.message.chat.id)
    if not session:
        await query.answer("Нет сессии", show_alert=True)
        return
    m = catalog.get(session.model)
    if not m or not m.reasoning:
        await query.answer("Модель не поддерживает reasoning", show_alert=True)
        return
    await query.message.edit_text(
        f"💭 <b>Уровень reasoning</b>\n\nТекущий: <b>{session.reasoning_effort}</b>",
        reply_markup=keyboards.reasoning_keyboard(session.reasoning_effort),
        parse_mode="HTML",
    )
    await query.answer()


@router.callback_query(F.data.startswith("setreason:"))
async def cb_setreason(query: CallbackQuery) -> None:
    level = query.data.split(":", 1)[1]
    session = _get_active_session(query.message.chat.id)
    if not session:
        await query.answer("Нет сессии", show_alert=True)
        return
    session.reasoning_effort = level
    session.save()
    await query.message.edit_text(
        f"✅ Reasoning: <b>{level}</b>",
        reply_markup=keyboards.session_menu_keyboard(session),
        parse_mode="HTML",
    )
    await query.answer(f"→ {level}")


@router.callback_query(F.data == "menu_verbosity")
async def cb_menu_verbosity(query: CallbackQuery) -> None:
    session = _get_active_session(query.message.chat.id)
    if not session:
        await query.answer("Нет сессии", show_alert=True)
        return
    m = catalog.get(session.model)
    if not m or not m.verbosity:
        await query.answer("Модель не поддерживает verbosity", show_alert=True)
        return
    cur = session.verbosity or "medium"
    await query.message.edit_text(
        f"📏 <b>Verbosity</b>\n\nТекущий: <b>{cur}</b>",
        reply_markup=keyboards.verbosity_keyboard(cur),
        parse_mode="HTML",
    )
    await query.answer()


@router.callback_query(F.data.startswith("setverbosity:"))
async def cb_setverbosity(query: CallbackQuery) -> None:
    level = query.data.split(":", 1)[1]
    session = _get_active_session(query.message.chat.id)
    if not session:
        await query.answer("Нет сессии", show_alert=True)
        return
    session.verbosity = level
    session.save()
    await query.message.edit_text(
        f"✅ Verbosity: <b>{level}</b>",
        reply_markup=keyboards.session_menu_keyboard(session),
        parse_mode="HTML",
    )
    await query.answer(f"→ {level}")


@router.callback_query(F.data == "tier_toggle")
async def cb_tier_toggle(query: CallbackQuery) -> None:
    """Циклическое переключение service_tier: default → flex → priority."""
    session = _get_active_session(query.message.chat.id)
    if not session:
        await query.answer("Нет сессии", show_alert=True)
        return
    from agent_coder.config import config as _cfg
    from agent_coder.telegram.keyboards import TIER_CYCLE
    cur = session.service_tier or _cfg.SERVICE_TIER
    if cur not in TIER_CYCLE:
        cur = "default"
    nxt = TIER_CYCLE[(TIER_CYCLE.index(cur) + 1) % len(TIER_CYCLE)]
    session.service_tier = nxt
    session.save()
    try:
        await query.message.edit_reply_markup(
            reply_markup=keyboards.session_menu_keyboard(session)
        )
    except Exception:
        pass
    await query.answer(f"Service tier: {nxt}")


@router.callback_query(F.data == "web_toggle")
async def cb_web_toggle(query: CallbackQuery) -> None:
    """Переключает per-session флаг web_search.

    Пока только сохраняет флаг — реальное влияние на API RouterAI
    (передача plugins=[{id:web}]) будет отдельным шагом.
    """
    session = _get_active_session(query.message.chat.id)
    if not session:
        await query.answer("Нет сессии", show_alert=True)
        return
    m = catalog.get(session.model)
    if not (m and m.web_search_builtin):
        await query.answer("Модель не поддерживает встроенный web search", show_alert=True)
        return
    session.web_search = not session.web_search
    session.save()
    try:
        await query.message.edit_reply_markup(
            reply_markup=keyboards.session_menu_keyboard(session)
        )
    except Exception:
        pass
    await query.answer(f"Web search: {'вкл' if session.web_search else 'выкл'}")


@router.callback_query(F.data == "session_back")
async def cb_session_back(query: CallbackQuery) -> None:
    session = _get_active_session(query.message.chat.id)
    if not session:
        await query.answer("Нет сессии", show_alert=True)
        return
    await query.message.edit_text(
        f"Сессия: <b>{session.title}</b>\n"
        f"<code>{session.id}</code> · {session.mode.value}\n"
        f"Стоимость: {_fmt_cost(session.total_usage.cost)}",
        reply_markup=keyboards.session_menu_keyboard(session),
        parse_mode="HTML",
    )
    await query.answer()
