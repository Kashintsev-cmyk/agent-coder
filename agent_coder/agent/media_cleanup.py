"""Retention медиа-вложений сессий.

Файлы в sessions/media/<session_id>/ старше N дней удаляются фоновой
задачей бота. Пустые папки тоже подчищаются.

Срок хранения — config.MEDIA_RETENTION_DAYS (по умолчанию 30).
"""
from __future__ import annotations

import logging
import time
from pathlib import Path

from agent_coder.config import config

logger = logging.getLogger(__name__)


def cleanup_old_media(
    *,
    sessions_dir: Path | None = None,
    retention_days: int | None = None,
    now: float | None = None,
) -> tuple[int, int]:
    """Удалить медиа старше retention_days. Возвращает (files, dirs).

    - `sessions_dir` — по умолчанию config.SESSIONS_DIR.
    - `retention_days` — по умолчанию config.MEDIA_RETENTION_DAYS.
    - `now` — для тестов.

    Ошибки (permission и т.п.) логируются и не бросаются наружу.
    """
    base = sessions_dir if sessions_dir is not None else config.SESSIONS_DIR
    media_root = base / "media"
    if not media_root.exists() or not media_root.is_dir():
        return (0, 0)

    days = retention_days if retention_days is not None else config.MEDIA_RETENTION_DAYS
    if days < 1:
        return (0, 0)

    cutoff = (now if now is not None else time.time()) - days * 86400
    removed_files = 0
    removed_dirs = 0

    for session_dir in media_root.iterdir():
        if not session_dir.is_dir():
            continue
        try:
            for f in session_dir.iterdir():
                if not f.is_file():
                    continue
                try:
                    if f.stat().st_mtime < cutoff:
                        f.unlink()
                        removed_files += 1
                except OSError as e:
                    logger.warning("media cleanup: не удалось удалить %s: %s", f, e)
            # Пустую папку сессии тоже убираем
            try:
                if not any(session_dir.iterdir()):
                    session_dir.rmdir()
                    removed_dirs += 1
            except OSError as e:
                logger.warning("media cleanup: не удалось удалить папку %s: %s",
                               session_dir, e)
        except OSError as e:
            logger.warning("media cleanup: ошибка обхода %s: %s", session_dir, e)

    if removed_files or removed_dirs:
        logger.info(
            "media cleanup: удалено %d файлов и %d папок (старше %d дн.)",
            removed_files, removed_dirs, days,
        )
    return (removed_files, removed_dirs)
