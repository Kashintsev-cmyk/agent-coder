"""Общие утилиты и единый Router для всех Telegram-хендлеров.

Здесь:
- единый Router (фильтр по TELEGRAM_USER_ID)
- утилиты без глобального состояния: _list_projects, _fmt_cost,
  _session_summary, _get_active_session

Глобальное состояние (_running, _pending, _shutting_down, shutdown)
пока остаётся в handlers/__init__.py — переедет при разбиении task.py.
"""
from __future__ import annotations

import logging
from pathlib import Path

from aiogram import F, Router

from agent_coder.agent.session import Session
from agent_coder.config import config
from agent_coder.telegram import state as tg_state

logger = logging.getLogger(__name__)

# Единый роутер — регистрируется в handlers/__init__.py.
# Все подмодули вешают свои хендлеры через @router.message / @router.callback_query
router = Router()

# Ограничиваем доступ одним пользователем
router.message.filter(F.from_user.id == config.TELEGRAM_USER_ID)
router.callback_query.filter(F.from_user.id == config.TELEGRAM_USER_ID)


def _list_projects() -> list[Path]:
    """Список проектов в стабильном (детерминированном) порядке.

    Порядок — глобальная сортировка по str(Path.resolve()) независимо от
    порядка обхода корневых папок, чтобы индекс в callback_data совпадал
    между рендером клавиатуры и обработкой нажатия.
    """
    projects: list[Path] = []
    for root in config.TELEGRAM_PROJECTS_DIRS:
        if not root.exists():
            continue
        try:
            for child in root.iterdir():
                if child.is_dir() and not child.name.startswith("."):
                    projects.append(child)
        except PermissionError:
            continue
    projects.sort(key=lambda p: str(p.resolve()))
    return projects[:30]


def _fmt_cost(value: float) -> str:
    from agent_coder.llm.types import format_cost
    return format_cost(value)


def _session_summary(s: Session) -> str:
    icon = "🟡" if s.mode.value == "plan" else "🟢"
    return (
        f"{icon} <b>{s.title or '(без названия)'}</b>\n"
        f"   <code>{s.id}</code> · {s.mode.value} · "
        f"{_fmt_cost(s.total_usage.cost)} · {s.age_str()}"
    )


def _get_active_session(chat_id: int) -> Session | None:
    sid = tg_state.get_active_session(chat_id)
    if not sid:
        return None
    return Session.load(sid)
