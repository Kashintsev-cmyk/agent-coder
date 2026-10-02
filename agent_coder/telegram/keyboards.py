"""Inline-клавиатуры."""
from __future__ import annotations

from pathlib import Path

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from agent_coder.agent.session import Session


def projects_keyboard(projects: list[Path]) -> InlineKeyboardMarkup:
    # callback_data ограничен 64 байтами, поэтому передаём индекс проекта
    # в детерминированно отсортированном списке _list_projects(), а не путь.
    rows = []
    for idx, p in enumerate(projects):
        rows.append([
            InlineKeyboardButton(text=f"📁 {p.name}", callback_data=f"new:{idx}")
        ])
    rows.append([
        InlineKeyboardButton(text="📚 Все сессии", callback_data="sessions")
    ])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def sessions_keyboard(sessions: list[Session]) -> InlineKeyboardMarkup:
    rows = []
    for s in sessions[:15]:
        icon = "🟡" if s.mode.value == "plan" else "🟢"
        cost = s.total_usage.cost
        title = (s.title or "(без названия)")[:35]
        rows.append([
            InlineKeyboardButton(
                text=f"{icon} {title} · {cost:.2f}₽",
                callback_data=f"open:{s.id}",
            )
        ])
    rows.append([InlineKeyboardButton(text="⬅️ Назад", callback_data="back")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def models_keyboard(current_model: str) -> InlineKeyboardMarkup:
    """Список кураторских моделей для выбора."""
    from agent_coder.models import CURATED_MODELS
    rows = []
    for m in CURATED_MODELS:
        mark = "✅" if m.id == current_model else "▫️"
        text = f"{mark} {m.name} · {m.input_price:.1f}/{m.output_price:.0f}₽ · {m.context // 1000}k"
        rows.append([InlineKeyboardButton(text=text, callback_data=f"setmodel:{m.id}")])
    rows.append([InlineKeyboardButton(text="⬅️ Назад", callback_data="session_back")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def reasoning_keyboard(current: str) -> InlineKeyboardMarkup:
    """Уровни reasoning, которые принимает routerai."""
    from agent_coder.models import REASONING_LEVELS
    labels = {
        "none":    "⚪ none — без раздумий",
        "minimal": "🟢 minimal — быстро",
        "low":     "🟢 low — быстро",
        "medium":  "🟡 medium — баланс",
        "high":    "🟠 high — глубоко",
        "xhigh":   "🔴 xhigh — очень глубоко",
        "max":     "🔴 max — максимум",
        "ultra":   "🔥 ultra — экстрим",
    }
    rows = []
    for level in REASONING_LEVELS:
        mark = "✅ " if level == current else "▫️ "
        rows.append([InlineKeyboardButton(
            text=mark + labels.get(level, level),
            callback_data=f"setreason:{level}",
        )])
    rows.append([InlineKeyboardButton(text="⬅️ Назад", callback_data="session_back")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def session_menu_keyboard(session_id: str, model_id: str, reasoning: str) -> InlineKeyboardMarkup:
    """Главное меню сессии."""
    from agent_coder.models import get_model_name
    model_name = get_model_name(model_id)
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(
            text=f"🧠 Модель: {model_name}",
            callback_data="menu_model",
        )],
        [InlineKeyboardButton(
            text=f"💭 Reasoning: {reasoning}",
            callback_data="menu_reasoning",
        )],
        [
            InlineKeyboardButton(text="🟡 Plan",  callback_data=f"mode:plan:{session_id}"),
            InlineKeyboardButton(text="🟢 Act",   callback_data=f"mode:act:{session_id}"),
            InlineKeyboardButton(text="🔒 Audit", callback_data=f"mode:audit:{session_id}"),
        ],
        [
            InlineKeyboardButton(text="💰 Стоимость", callback_data=f"cost:{session_id}"),
            InlineKeyboardButton(text="🔄 Новая",     callback_data="sessions"),
        ],
        [
            InlineKeyboardButton(text="📜 История", callback_data=f"history:{session_id}"),
            InlineKeyboardButton(text="🗑 Удалить", callback_data=f"del:{session_id}"),
        ],
    ])


def approval_keyboard(request_id: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="✅ Разрешить", callback_data=f"approve:{request_id}"),
        InlineKeyboardButton(text="❌ Отклонить", callback_data=f"deny:{request_id}"),
    ]])

def server_menu_keyboard() -> InlineKeyboardMarkup:
    """Меню серверной диагностики — по 2 кнопки в ряд."""
    from agent_coder.server_ops import SERVER_OPS
    rows = []
    row: list[InlineKeyboardButton] = []
    for op in SERVER_OPS:
        row.append(InlineKeyboardButton(text=op.label, callback_data=f"srv:{op.id}"))
        if len(row) == 2:
            rows.append(row)
            row = []
    if row:
        rows.append(row)
    rows.append([InlineKeyboardButton(text="⬅️ Назад", callback_data="session_back")])
    return InlineKeyboardMarkup(inline_keyboard=rows)

def audit_menu_keyboard() -> InlineKeyboardMarkup:
    """Меню аудита проекта."""
    from agent_coder.audit_ops import AUDIT_OPS
    rows = []
    row: list[InlineKeyboardButton] = []
    for op in AUDIT_OPS:
        row.append(InlineKeyboardButton(text=op.label, callback_data=f"aud:{op.id}"))
        if len(row) == 2:
            rows.append(row)
            row = []
    if row:
        rows.append(row)
    rows.append([InlineKeyboardButton(text="⬅️ Назад", callback_data="session_back")])
    return InlineKeyboardMarkup(inline_keyboard=rows)