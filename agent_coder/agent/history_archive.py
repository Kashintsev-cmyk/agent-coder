"""Архив сжатой истории сессий (RAG-lite).

При сжатии старые сообщения не выбрасываются, а дописываются в
sessions/archive/<session_id>.jsonl (одна строка JSON = одно сообщение).
Позже агент может найти потерянные детали через инструмент search_history
(поиск по архиву через ripgrep).

Формат JSONL выбран, чтобы дописывать без перечитывания всего файла.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from agent_coder.config import config

logger = logging.getLogger(__name__)


def archive_path(session_id: str) -> Path:
    return config.SESSIONS_DIR / "archive" / f"{session_id}.jsonl"


def append_messages(session_id: str, messages: list[dict[str, Any]]) -> int:
    """Дописать сообщения в архив сессии. Возвращает число записанных.

    Ошибки не пробрасываются: архив — вспомогательная функция, сжатие
    не должно падать из-за проблем с диском.
    """
    if not messages:
        return 0
    path = archive_path(session_id)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as f:
            for m in messages:
                f.write(json.dumps(m, ensure_ascii=False) + "\n")
        return len(messages)
    except OSError as e:
        logger.warning("history_archive.append failed for %s: %s", session_id, e)
        return 0


def load(session_id: str) -> list[dict[str, Any]]:
    """Прочитать весь архив сессии (для тестов и отладки)."""
    path = archive_path(session_id)
    if not path.exists():
        return []
    out: list[dict[str, Any]] = []
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    except OSError as e:
        logger.warning("history_archive.load failed for %s: %s", session_id, e)
    return out


def delete(session_id: str) -> bool:
    """Удалить архив сессии (вызывается из Session.delete)."""
    path = archive_path(session_id)
    try:
        if path.exists():
            path.unlink()
            return True
    except OSError as e:
        logger.warning("history_archive.delete failed for %s: %s", session_id, e)
    return False
