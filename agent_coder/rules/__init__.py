"""Встроенные правила для system prompt (не специфичны для проекта)."""

from __future__ import annotations

import logging
from functools import lru_cache
from pathlib import Path

logger = logging.getLogger(__name__)

STANDARD_PATH = Path(__file__).parent / "project_standard.md"
COMMAND_STYLE_STANDARD_PATH = Path(__file__).parent / "command_style.md"
AUDIT_STANDARD_PATH = Path(__file__).parent / "audit_standard.md"
VERIFY_STANDARD_PATH = Path(__file__).parent / "verify_standard.md"
COMMAND_STYLE_HEADER = "\n\n--- COMMAND STYLE (builtin) ---\n\n"
COMMAND_STYLE_FOOTER = "\n--- end COMMAND STYLE ---\n"


@lru_cache(maxsize=1)
def load_command_style_standard() -> str:
    """Вернуть встроенный командный стандарт или '' при ошибке чтения."""
    try:
        return COMMAND_STYLE_STANDARD_PATH.read_text(encoding="utf-8")
    except OSError as e:
        logger.warning("command_style.md: не удалось прочитать: %s", e)
        return ""


def append_command_style(prompt: str) -> str:
    """Добавить общий командный стандарт к системному промпту."""
    text = load_command_style_standard()
    if not text:
        return prompt
    return prompt + COMMAND_STYLE_HEADER + text.rstrip() + COMMAND_STYLE_FOOTER


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


@lru_cache(maxsize=1)
def load_verify_standard() -> str:
    """Вернуть текст встроенного verify-промта (B9) или '' при ошибке."""
    try:
        return VERIFY_STANDARD_PATH.read_text(encoding="utf-8")
    except OSError as e:
        logger.warning("verify_standard.md: не удалось прочитать: %s", e)
        return ""
