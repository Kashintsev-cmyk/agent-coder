"""Чтение AGENTS.md из workspace и врезка в system prompt.

Файл <workspace>/AGENTS.md подхватывается при сборке system prompt
для режимов PLAN, ACT и CHAT. Отсутствие файла или любая ошибка чтения не
влияют на поведение агента — system prompt остаётся базовым.

Содержимое кэшируется по (mtime_ns, size): пока файл не менялся,
повторные вызовы не перечитывают его с диска. Размер ограничен
MAX_AGENTS_MD_BYTES, хвост обрезается с маркером
[AGENTS.md truncated].
"""

from __future__ import annotations

import logging
import os
import threading
from pathlib import Path

logger = logging.getLogger(__name__)

AGENTS_MD_NAME = "AGENTS.md"
MAX_AGENTS_MD_BYTES = int(os.getenv("AGENT_CODER_AGENTS_MD_MAX_BYTES", "8192"))
TRUNCATION_MARKER = "\n\n[AGENTS.md truncated]\n"
SECTION_HEADER = "\n\n--- AGENTS.md ---\n\n"
SECTION_FOOTER = "\n--- end AGENTS.md ---\n"

_cache: dict[str, tuple[int, int, str]] = {}
_lock = threading.Lock()


def clear_cache() -> None:
    """Сбросить кэш (нужно в тестах)."""
    with _lock:
        _cache.clear()


def _read_text(path: Path) -> str | None:
    """Прочитать не более MAX_AGENTS_MD_BYTES + 1 байт. None при OSError."""
    try:
        with path.open("rb") as f:
            raw = f.read(MAX_AGENTS_MD_BYTES + 1)
    except OSError as e:
        logger.debug("AGENTS.md: не удалось прочитать %s: %s", path, e)
        return None
    truncated = len(raw) > MAX_AGENTS_MD_BYTES
    if truncated:
        raw = raw[:MAX_AGENTS_MD_BYTES]
    text = raw.decode("utf-8", errors="replace")
    if truncated:
        text += TRUNCATION_MARKER
    return text


def load_agents_md(workspace: str | Path) -> str | None:
    """Вернуть содержимое <workspace>/AGENTS.md или None.

    Кэш по (mtime_ns, size). Изменение файла инвалидирует запись.
    """
    try:
        path = Path(workspace) / AGENTS_MD_NAME
        st = path.stat()
    except OSError:
        return None
    key = str(path.resolve())
    sig = (st.st_mtime_ns, st.st_size)
    with _lock:
        cached = _cache.get(key)
        if cached is not None and cached[0] == sig[0] and cached[1] == sig[1]:
            return cached[2]
    text = _read_text(path)
    if text is None:
        return None
    with _lock:
        _cache[key] = (sig[0], sig[1], text)
    return text


def append_agents_md(prompt: str, workspace: str | Path) -> str:
    """Дополнить prompt секцией AGENTS.md, если файл есть."""
    text = load_agents_md(workspace)
    if not text:
        return prompt
    return prompt + SECTION_HEADER + text.rstrip() + SECTION_FOOTER
