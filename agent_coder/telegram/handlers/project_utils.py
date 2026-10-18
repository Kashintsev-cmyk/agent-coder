"""Утилиты проектов: создание, валидация имени, сессия.

Используется из /new, /clone, /ghnew и gh_offer-callback'ов.
"""

from __future__ import annotations

import asyncio
import logging
import re
from pathlib import Path

from aiogram.types import Message

from agent_coder.agent.prompts import Mode
from agent_coder.agent.session import Session
from agent_coder.config import config
from agent_coder.scaffold import scaffold_project
from agent_coder.standard import bots_dir
from agent_coder.telegram import keyboards
from agent_coder.telegram import state as tg_state

logger = logging.getLogger(__name__)


# Директория по умолчанию для новых проектов
# Единый источник — standard.paths.bots_dir() (env AGENT_CODER_BOTS_DIR)
_PROJECTS_DEFAULT_DIR: Path = bots_dir()
# Валидация имени: буква/цифра, затем любые [a-zA-Z0-9_.-], до 40 символов
_PROJECT_NAME_RE = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9_.-]{0,39}$")


def _validate_project_name(name: str) -> str | None:
    """Вернуть текст ошибки или None, если имя валидно."""
    if not name:
        return "пустое имя"
    if not _PROJECT_NAME_RE.match(name):
        return (
            "недопустимое имя (буквы/цифры/подчёркивания/дефисы/точки, "
            "до 40 символов, не начинается с точки или дефиса)"
        )
    return None


async def _create_project_on_disk(name: str) -> Path:
    """Создать проект по стандарту bots/ через scaffold_project.

    Единый путь: Telegram /new и GitHub-репо (ghnew) используют scaffold
    из agent_coder/templates/. Возвращает Path, бросает при ошибке.
    """
    default_dir = _PROJECTS_DEFAULT_DIR
    target = (default_dir / name).resolve()
    try:
        target.relative_to(default_dir.resolve())
    except ValueError:
        raise ValueError(f"путь вне {default_dir}") from None

    report = await asyncio.to_thread(scaffold_project, name, target=target)
    if "error" in report:
        if report["error"] == "target_not_empty":
            raise FileExistsError(f"уже существует: {target}")
        raise RuntimeError(f"scaffold {name}: {report['error']} ({report['path']})")
    return target


async def _create_session(
    chat_id: int,
    message: Message,
    path_str: str,
    *,
    mode: Mode = Mode.ACT,
    title: str | None = None,
) -> None:
    """Создать сессию в указанной директории.

    mode — режим сессии (по умолчанию ACT).
    title — заголовок (по умолчанию "Новая сессия в <dir>").
    """
    workspace = Path(path_str).resolve()
    if not workspace.is_dir():
        await message.answer(
            f"❌ Директория не найдена: <code>{workspace}</code>", parse_mode="HTML"
        )
        return

    session = Session.new(
        workspace=str(workspace),
        model=config.MODEL_MAIN,
        mode=mode,
    )
    session.title = title or f"Новая сессия в {workspace.name}"
    session.save()
    tg_state.set_active_session(chat_id, session.id)

    await message.answer(
        f"✅ Сессия создана: <b>{session.title}</b>\n"
        f"   <code>{session.id}</code> · режим: {mode.value} · модель: {session.model}\n\n"
        f"Отправь задачу текстом.",
        reply_markup=keyboards.session_menu_keyboard(session),
        parse_mode="HTML",
    )
