"""Ручные заметки проекта: <project>.notes.md (git-tracked, не перетирается).

Путь: AGENT_CODER_NOTES_DIR (env), default — /home/botuser/bots/_migrations.
Причина git-tracked: ручные решения (Deferred/Done) должны быть в истории,
а не в ~/.agent-coder (потеряются при переезде сервера).
"""

from __future__ import annotations

import os
from pathlib import Path

ENV_NOTES_DIR = "AGENT_CODER_NOTES_DIR"
_DEFAULT_NOTES_DIR = Path("/home/botuser/bots/_migrations")


def notes_dir() -> Path:
    raw = os.environ.get(ENV_NOTES_DIR)
    return Path(raw).expanduser() if raw else _DEFAULT_NOTES_DIR


def notes_path(project: str) -> Path:
    return notes_dir() / f"{project}.notes.md"


def read_notes(project: str) -> str:
    p = notes_path(project)
    if not p.is_file():
        return ""
    return p.read_text(encoding="utf-8")
