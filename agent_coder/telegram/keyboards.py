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
    from agent_coder.model_catalog import catalog
    from agent_coder.models import feature_badges
    rows = []
    for m in catalog.all():
        mark = "✅" if m.id == current_model else "▫️"
        badges = feature_badges(m)
        text = f"{mark} {m.name} · {m.input_price:.1f}/{m.output_price:.0f}₽ · {m.context // 1000}k"
        if badges:
            text += f" · {badges}"
        rows.append([InlineKeyboardButton(text=text, callback_data=f"info:{m.id}")])
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


def verbosity_keyboard(current: str) -> InlineKeyboardMarkup:
    """Уровни verbosity (длина ответа)."""
    from agent_coder.models import VERBOSITY_LEVELS
    labels = {
        "low":    "🟢 low — коротко",
        "medium": "🟡 medium — баланс",
        "high":   "🟠 high — подробно",
        "xhigh":  "🔴 xhigh — очень подробно",
        "max":    "🔥 max — максимум",
    }
    rows = []
    for level in VERBOSITY_LEVELS:
        mark = "✅ " if level == current else "▫️ "
        rows.append([InlineKeyboardButton(
            text=mark + labels.get(level, level),
            callback_data=f"setverbosity:{level}",
        )])
    rows.append([InlineKeyboardButton(text="⬅️ Назад", callback_data="session_back")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


TIER_CYCLE: list[str] = ["default", "flex", "priority"]


def model_card_keyboard(model_id: str, is_current: bool) -> InlineKeyboardMarkup:
    """Карточка модели: описание + метрики + выбор/назад."""
    row: list[InlineKeyboardButton] = []
    if not is_current:
        row.append(InlineKeyboardButton(
            text="✅ Выбрать",
            callback_data=f"setmodel:{model_id}",
        ))
    row.append(InlineKeyboardButton(
        text="⬅️ К списку",
        callback_data="menu_model",
    ))
    return InlineKeyboardMarkup(inline_keyboard=[row])


def session_menu_keyboard(session: Session) -> InlineKeyboardMarkup:
    """Главное меню сессии, адаптивное под возможности выбранной модели.

    Всегда: 🧠 Модель, [Plan|Act|Audit], [Стоимость|Новая], [История|Удалить].
    Условно:
      💭 Reasoning  — если model.reasoning
      🌐 Web search — если model.web_search_builtin
    """
    from agent_coder.model_catalog import catalog
    from agent_coder.models import feature_badges

    m = catalog.get(session.model)
    model_label = m.name if m else session.model
    badges = feature_badges(m)
    model_text = f"🧠 Модель: {model_label}"
    if badges:
        model_text += f" · {badges}"

    rows: list[list[InlineKeyboardButton]] = [
        [InlineKeyboardButton(text=model_text, callback_data="menu_model")],
    ]

    # Компактный блок быстрых настроек: reasoning | web | tier | verbosity.
    # Адаптивно — только применимые к модели; раскладка 1/2/3/2+2.
    quick: list[InlineKeyboardButton] = []
    if m and m.reasoning:
        quick.append(InlineKeyboardButton(
            text=f"💭 {session.reasoning_effort}",
            callback_data="menu_reasoning",
        ))
    if m and m.web_search_builtin:
        ws = "вкл" if session.web_search else "выкл"
        quick.append(InlineKeyboardButton(
            text=f"🌐 {ws}",
            callback_data="web_toggle",
        ))
    from agent_coder.config import config as _cfg
    cur_tier = session.service_tier or _cfg.SERVICE_TIER
    quick.append(InlineKeyboardButton(
        text=f"⚡ {cur_tier}",
        callback_data="tier_toggle",
    ))
    if m and m.verbosity:
        v = session.verbosity or "medium"
        quick.append(InlineKeyboardButton(
            text=f"📏 {v}",
            callback_data="menu_verbosity",
        ))

    # Раскладка: 1 → 1 в ряд; 2 → 2 в ряд; 3 → 3 в ряд; 4 → 2+2.
    if len(quick) <= 3:
        rows.append(quick)
    else:
        rows.append(quick[:2])
        rows.append(quick[2:])

    sid = session.id
    rows.append([
        InlineKeyboardButton(text="🟡 Plan",  callback_data=f"mode:plan:{sid}"),
        InlineKeyboardButton(text="🟢 Act",   callback_data=f"mode:act:{sid}"),
    ])
    rows.append([
        InlineKeyboardButton(text="🔒 Audit", callback_data=f"mode:audit:{sid}"),
        InlineKeyboardButton(text="💬 Chat",  callback_data=f"mode:chat:{sid}"),
    ])
    rows.append([
        InlineKeyboardButton(text="💰 Стоимость", callback_data=f"cost:{sid}"),
        InlineKeyboardButton(text="🔄 Новая",     callback_data="sessions"),
    ])
    rows.append([
        InlineKeyboardButton(text="📜 История", callback_data=f"history:{sid}"),
        InlineKeyboardButton(text="🗑 Удалить", callback_data=f"del:{sid}"),
    ])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def approval_keyboard(request_id: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="✅ Разрешить", callback_data=f"approve:{request_id}"),
        InlineKeyboardButton(text="❌ Отклонить", callback_data=f"deny:{request_id}"),
    ]])

def server_menu_keyboard() -> InlineKeyboardMarkup:
    """Верхний уровень серверной диагностики: список групп, 2 в ряд."""
    from agent_coder.server_ops import SERVER_GROUPS
    rows: list[list[InlineKeyboardButton]] = []
    row: list[InlineKeyboardButton] = []
    for gid, glabel in SERVER_GROUPS:
        row.append(InlineKeyboardButton(text=glabel, callback_data=f"srv_grp:{gid}"))
        if len(row) == 2:
            rows.append(row)
            row = []
    if row:
        rows.append(row)
    rows.append([InlineKeyboardButton(text="⬅️ Назад", callback_data="session_back")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def server_group_keyboard(group: str) -> InlineKeyboardMarkup:
    """Кнопки одной группы серверных op'ов, 2 в ряд."""
    from agent_coder.server_ops import ops_in_group
    rows: list[list[InlineKeyboardButton]] = []
    row: list[InlineKeyboardButton] = []
    for op in ops_in_group(group):
        row.append(InlineKeyboardButton(text=op.label, callback_data=f"srv:{op.id}"))
        if len(row) == 2:
            rows.append(row)
            row = []
    if row:
        rows.append(row)
    rows.append([
        InlineKeyboardButton(text="⬅️ К группам", callback_data="srv_back"),
        InlineKeyboardButton(text="⬅️ В сессию", callback_data="session_back"),
    ])
    return InlineKeyboardMarkup(inline_keyboard=rows)

def audit_menu_keyboard() -> InlineKeyboardMarkup:
    """Верхний уровень аудита: список групп, 2 в ряд."""
    from agent_coder.audit_ops import AUDIT_GROUPS
    rows: list[list[InlineKeyboardButton]] = []
    row: list[InlineKeyboardButton] = []
    for gid, glabel in AUDIT_GROUPS:
        row.append(InlineKeyboardButton(text=glabel, callback_data=f"aud_grp:{gid}"))
        if len(row) == 2:
            rows.append(row)
            row = []
    if row:
        rows.append(row)
    rows.append([InlineKeyboardButton(text="⬅️ Назад", callback_data="session_back")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def audit_group_keyboard(group: str) -> InlineKeyboardMarkup:
    """Кнопки одной группы audit-оп'ов, 2 в ряд."""
    from agent_coder.audit_ops import ops_in_group
    rows: list[list[InlineKeyboardButton]] = []
    row: list[InlineKeyboardButton] = []
    for op in ops_in_group(group):
        row.append(InlineKeyboardButton(text=op.label, callback_data=f"aud:{op.id}"))
        if len(row) == 2:
            rows.append(row)
            row = []
    if row:
        rows.append(row)
    rows.append([
        InlineKeyboardButton(text="⬅️ К группам", callback_data="aud_back"),
        InlineKeyboardButton(text="⬅️ В сессию", callback_data="session_back"),
    ])
    return InlineKeyboardMarkup(inline_keyboard=rows)
