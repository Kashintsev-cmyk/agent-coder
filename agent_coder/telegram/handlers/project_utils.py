"""Утилиты проектов: создание, валидация имени, сессия.

Используется из /new, /clone, /ghnew и gh_offer-callback'ов.
"""
from __future__ import annotations

import logging
import re
from pathlib import Path

from aiogram.types import Message

from agent_coder.agent.prompts import Mode
from agent_coder.agent.session import Session
from agent_coder.config import config
from agent_coder.shell_runner import run_shell
from agent_coder.telegram import keyboards
from agent_coder.telegram import state as tg_state

logger = logging.getLogger(__name__)


# Директория по умолчанию для новых проектов
_PROJECTS_DEFAULT_DIR: Path = Path.home() / "bots"
# Валидация имени: буква/цифра, затем любые [a-zA-Z0-9_.-], до 40 символов
_PROJECT_NAME_RE = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9_.-]{0,39}$")

_GITIGNORE_PY = """\
# Python
__pycache__/
*.py[cod]
*$py.class
*.egg-info/
.venv/
venv/
env/
build/
dist/
.pytest_cache/
.mypy_cache/
.ruff_cache/
.coverage
htmlcov/

# IDE
.idea/
.vscode/
*.swp
*.swo

# Env
.env
.env.*
!.env.example

# OS
.DS_Store
Thumbs.db

# Project-local
sessions/
"""

_README_TEMPLATE = """\
# {name}

Описание проекта.

## Установка

    python3 -m venv .venv
    source .venv/bin/activate
    pip install -r requirements.txt

## Запуск

    python -m {module}
"""


def _validate_project_name(name: str) -> str | None:
    """Вернуть текст ошибки или None, если имя валидно."""
    if not name:
        return "пустое имя"
    if not _PROJECT_NAME_RE.match(name):
        return ("недопустимое имя (буквы/цифры/подчёркивания/дефисы/точки, "
                "до 40 символов, не начинается с точки или дефиса)")
    return None


async def _create_project_on_disk(name: str) -> Path:
    """Создать проект в _PROJECTS_DEFAULT_DIR/<name> по стандартам.

    - git init
    - README.md, .gitignore
    - git add + initial commit
    Возвращает Path. Бросает исключение при ошибке.
    """
    target = (_PROJECTS_DEFAULT_DIR / name).resolve()
    try:
        target.relative_to(_PROJECTS_DEFAULT_DIR.resolve())
    except ValueError:
        raise ValueError(f"путь вне {_PROJECTS_DEFAULT_DIR}")

    if target.exists():
        raise FileExistsError(f"уже существует: {target}")

    target.mkdir(parents=True)
    (target / ".gitignore").write_text(_GITIGNORE_PY, encoding="utf-8")
    (target / "README.md").write_text(
        _README_TEMPLATE.format(
            name=name,
            module=name.replace("-", "_").replace(".", "_"),
        ),
        encoding="utf-8",
    )

    for cmd in (
        "git init -q",
        "git add .gitignore README.md",
        "git -c commit.gpgsign=false commit -q -m 'Initial commit'",
    ):
        r = await run_shell(cmd, cwd=target, timeout=30)
        if not r.ok:
            logger.warning("create_project %r: %s", cmd, r.output)

    return target


async def _create_session(chat_id: int, message: Message, path_str: str) -> None:
    workspace = Path(path_str).resolve()
    if not workspace.is_dir():
        await message.answer(f"❌ Директория не найдена: <code>{workspace}</code>",
                            parse_mode="HTML")
        return

    session = Session.new(
        workspace=str(workspace),
        model=config.MODEL_MAIN,
        mode=Mode.ACT,
    )
    session.title = f"Новая сессия в {workspace.name}"
    session.save()
    tg_state.set_active_session(chat_id, session.id)

    await message.answer(
        f"✅ Сессия создана: <b>{workspace.name}</b>\n"
        f"   <code>{session.id}</code> · режим: act · модель: {session.model}\n\n"
        f"Отправь задачу текстом.",
        reply_markup=keyboards.session_menu_keyboard(session),
        parse_mode="HTML",
    )
