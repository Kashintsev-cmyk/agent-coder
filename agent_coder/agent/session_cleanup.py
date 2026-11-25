"""Retention старых сессий по времени последней активности."""

from __future__ import annotations

import logging
import time

from agent_coder.agent.session import Session
from agent_coder.config import config

logger = logging.getLogger(__name__)


def cleanup_old_sessions(
    *,
    retention_days: int | None = None,
    now: float | None = None,
) -> list[str]:
    """Удалить сессии без активности дольше retention_days и вернуть их ID.

    Возраст определяется по updated_at, а не created_at: активная сессия
    сохраняется, даже если была создана давно. Нулевой/неизвестный timestamp
    не считается устаревшим, чтобы не удалять legacy-данные ошибочно.
    Каждая сессия удаляется через Session.delete() вместе с зависимыми файлами.
    Ошибка одной сессии не прерывает очистку остальных.
    """
    days = (
        retention_days if retention_days is not None else config.SESSION_RETENTION_DAYS
    )
    if days < 1:
        return []

    cutoff = (now if now is not None else time.time()) - days * 86400
    deleted: list[str] = []
    for session in Session.list_all():
        if session.updated_at <= 0 or session.updated_at >= cutoff:
            continue
        try:
            session.delete()
        except Exception:
            logger.exception("session cleanup: не удалось удалить %s", session.id)
            continue
        deleted.append(session.id)

    if deleted:
        logger.info(
            "session cleanup: удалено %d сессий без активности более %d дней",
            len(deleted),
            days,
        )
    return deleted
