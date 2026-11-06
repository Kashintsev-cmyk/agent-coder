"""Docs-sync: ловит drift между кодом и доками.

Проверяет ТОЛЬКО направление «в коде есть → в доках должно быть».
Обратное (в доках есть что-то, чего нет в коде) не проверяется —
в README/TELEGRAM.md могут быть исторические упоминания, roadmap,
примеры и пр.

Правила:
- инструменты (Tool(name="...") в agent_coder/tools/*.py) → `name` в README.md
- команды BOT_COMMANDS (bot.py) → `/cmd` в docs/TELEGRAM.md
- ENV-переменные (getenv("...") в config.py) → `VAR` в README.md
"""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TOOLS_DIR = ROOT / "agent_coder" / "tools"
BOT_PY = ROOT / "agent_coder" / "telegram" / "bot.py"
CONFIG_PY = ROOT / "agent_coder" / "config.py"
README = ROOT / "README.md"
TELEGRAM_MD = ROOT / "docs" / "TELEGRAM.md"


def _tool_names() -> set[str]:
    names: set[str] = set()
    for p in TOOLS_DIR.glob("*.py"):
        names |= set(re.findall(r'name="([a-z_]+)"', p.read_text(encoding="utf-8")))
    return names


def _bot_commands() -> set[str]:
    return set(
        re.findall(
            r'command="([a-z_]+)"',
            BOT_PY.read_text(encoding="utf-8"),
        )
    )


def _env_vars() -> set[str]:
    return set(
        re.findall(
            r'getenv\("([A-Z][A-Z0-9_]+)"',
            CONFIG_PY.read_text(encoding="utf-8"),
        )
    )


def test_extraction_sanity():
    """Sanity: регексы находят что-то осмысленное, иначе drift не ловится."""
    assert len(_tool_names()) >= 10, "сломан паттерн извлечения инструментов"
    assert len(_bot_commands()) >= 15, "сломан паттерн извлечения команд"
    assert len(_env_vars()) >= 15, "сломан паттерн извлечения ENV"


def test_all_tools_documented_in_readme():
    doc = README.read_text(encoding="utf-8")
    missing = sorted(t for t in _tool_names() if f"`{t}`" not in doc)
    assert not missing, (
        f"Инструменты есть в коде, но не упомянуты в README.md: {missing}. "
        "Добавь строку в таблицу '## Инструменты'."
    )


def test_all_bot_commands_documented_in_telegram_md():
    doc = TELEGRAM_MD.read_text(encoding="utf-8")
    missing = sorted(
        c for c in _bot_commands()
        if not re.search(rf"`/{c}(?:`|\s|\|)", doc)
    )
    assert not missing, (
        f"Команды есть в BOT_COMMANDS, но не упомянуты в docs/TELEGRAM.md: "
        f"{missing}. Добавь строку в '## Команды'."
    )


def test_all_env_vars_documented_in_readme():
    doc = README.read_text(encoding="utf-8")
    missing = sorted(v for v in _env_vars() if f"`{v}`" not in doc)
    assert not missing, (
        f"ENV-переменные есть в config.py, но не упомянуты в README.md: "
        f"{missing}. Добавь строку в '## Переменные окружения'."
    )
