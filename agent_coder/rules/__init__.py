"""Встроенные правила для system prompt (не специфичны для проекта)."""

from __future__ import annotations

import logging
from functools import lru_cache
from pathlib import Path

logger = logging.getLogger(__name__)

STANDARD_PATH = Path(__file__).parent / "project_standard.md"
AUDIT_STANDARD_PATH = Path(__file__).parent / "audit_standard.md"


@lru_cache(maxsize=1)
def load_project_standard() -> str:
    """Вернуть текст встроенного стандарта или '' при ошибке чтения.

    Кэш на уровне процесса: файл меняется только с коммитом,
    кэш сбрасывается перезапуском (или lru_cache.cache_clear() в тестах).
    """
    try:
        return STANDARD_PATH.read_text(encoding="utf-8")
    except OSError as e:
        logger.warning("project_standard.md: не удалось прочитать: %s", e)
        return ""


@lru_cache(maxsize=1)
def load_audit_standard() -> str:
    """Вернуть текст встроенного audit-промта или '' при ошибке чтения."""
    try:
        return AUDIT_STANDARD_PATH.read_text(encoding="utf-8")
    except OSError as e:
        logger.warning("audit_standard.md: не удалось прочитать: %s", e)
        return ""
