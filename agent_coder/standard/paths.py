"""Пути инфраструктуры: из env, не хардкод."""

from __future__ import annotations

import os
from pathlib import Path

ENV_BOTS_DIR = "AGENT_CODER_BOTS_DIR"
_DEFAULT_BOTS_DIR = Path("/home/botuser/bots")


def bots_dir() -> Path:
    """Корень bots/. Переопределяется через AGENT_CODER_BOTS_DIR."""
    raw = os.environ.get(ENV_BOTS_DIR)
    return Path(raw).expanduser() if raw else _DEFAULT_BOTS_DIR


def project_dir(name: str) -> Path:
    """Каталог конкретного проекта внутри bots/."""
    return bots_dir() / name
