"""Общие утилиты и единый Router для всех Telegram-хендлеров.

Здесь:
- единый Router (фильтр по TELEGRAM_USER_ID)
- утилиты без глобального состояния: _list_projects, _fmt_cost,
  _session_summary, _get_active_session

Глобальное состояние (_running, _pending, _shutting_down, shutdown)
живёт здесь, чтобы быть доступным всем подмодулям (task.py, commands.py).
"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path

from aiogram import Bot, F, Router

from agent_coder.agent.session import Session
from agent_coder.config import config
from agent_coder.telegram import state as tg_state
from agent_coder.telegram.renderer import html_escape

logger = logging.getLogger(__name__)

# ---------- Глобальное состояние задач ----------
# Активные задачи: {chat_id: asyncio.Task}
_running: dict[int, asyncio.Task] = {}
# Ожидания approval: {request_id: asyncio.Future}
_pending: dict[str, asyncio.Future] = {}
# Идёт ли корректная остановка (меняет тексты уведомлений)
_shutting_down = False


async def shutdown(bot: Bot) -> None:
    """Корректная остановка: уведомить и прервать активные задачи.

    Вызывается при получении SIGTERM/SIGINT перед выходом процесса.
    """
    global _shutting_down
    _shutting_down = True

    # Висящие подтверждения сразу отклоняем, чтобы циклы не ждали таймаут
    for request_id, future in list(_pending.items()):
        _pending.pop(request_id, None)
        if not future.done():
            future.set_result(False)

    active = [(cid, t) for cid, t in _running.items() if not t.done()]
    if not active:
        return

    for cid, _ in active:
        try:
            await bot.send_message(cid, "🔄 Бот перезагружается, задача прервана.")
        except Exception as e:
            logger.warning("shutdown notify failed: %s", e)

    for _, task in active:
        task.cancel()
    await asyncio.gather(*(t for _, t in active), return_exceptions=True)


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


def _audit_reasoning_warning(session: Session) -> str:
    """Текст-предупреждение, если audit-режим на модели без reasoning.

    Возвращает "" если модель поддерживает reasoning или неизвестна как
    reasoning-способная — только явное отсутствие reasoning даёт warning.
    """
    from agent_coder.model_catalog import catalog

    m = catalog.get(session.model)
    if m is not None and m.reasoning:
        return ""
    name = html_escape(m.name if m else session.model)
    return (
        f"\n\n⚠️ <b>Модель {name} не поддерживает reasoning.</b>\n"
        f"Аудит без thinking будет поверхностным. Для глубокого анализа "
        f"выбери модель с бейджем ⚙/🧠 (например, Claude Sonnet или DeepSeek Flash)."
    )
