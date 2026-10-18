"""GitHub-интеграция: /clone, /ghnew, предложение GitHub после /new.

Использует github_api (создание репо) + project_utils (локальный проект).
"""

from __future__ import annotations

import logging
import re
from pathlib import Path

from aiogram import F
from aiogram.filters import Command
from aiogram.types import CallbackQuery, Message

from agent_coder.config import config
from agent_coder.shell_runner import run_shell
from agent_coder.telegram import keyboards
from agent_coder.telegram.renderer import html_escape

from .project_utils import (
    _PROJECTS_DEFAULT_DIR,
    _create_project_on_disk,
    _create_session,
    _validate_project_name,
)
from .shared import router

logger = logging.getLogger(__name__)


# ---------- GitHub helpers ----------


async def _gh_create_repo_only(
    name: str,
    *,
    private: bool,
    path: Path,
) -> str:
    """Создать репо на GitHub и привязать origin (без push).

    path — локальная папка с уже готовым git-репозиторием.
    Возвращает html_url. Бросает исключение при ошибке.
    """
    from agent_coder.github_api import create_repo, whoami

    token = config.GITHUB_TOKEN
    if not token:
        raise RuntimeError(
            "GITHUB_TOKEN не задан в .env — создание на GitHub недоступно"
        )

    await whoami(token)  # валидация токена
    repo = await create_repo(name, private=private, token=token)
    ssh_url = repo["ssh_url"]
    html_url = repo["html_url"]

    # Привязать origin (если уже есть — заменить)
    r = await run_shell("git remote -v", cwd=path, timeout=10)
    if "origin" in r.stdout:
        await run_shell("git remote remove origin", cwd=path, timeout=10)
    r = await run_shell(f"git remote add origin {ssh_url}", cwd=path, timeout=10)
    if not r.ok:
        raise RuntimeError(f"git remote add: {r.output[:200]}")

    return html_url


async def _gh_create_and_push(
    name: str,
    *,
    private: bool,
    path: Path,
) -> str:
    """Создать репо на GitHub + push текущей ветки.

    Возвращает html_url. Бросает исключение при ошибке.
    """
    html_url = await _gh_create_repo_only(name, private=private, path=path)

    r = await run_shell(
        "git push -u origin HEAD",
        cwd=path,
        timeout=120,
    )
    if not r.ok:
        raise RuntimeError(f"git push: {r.output[:300]}")

    logger.info("gh: created %s and pushed %s", html_url, path)
    return html_url


# ---------- /ghnew ----------


@router.message(Command("ghnew"))
async def cmd_ghnew(message: Message) -> None:
    """Создать проект на GitHub + локально + push.

    Формат: /ghnew <name>
    """
    if not config.GITHUB_TOKEN:
        await message.answer(
            "❌ <code>GITHUB_TOKEN</code> не задан в <code>.env</code>.\n"
            "Создай токен на github.com/settings/tokens (scope: repo).",
            parse_mode="HTML",
        )
        return

    parts = (message.text or "").split(maxsplit=1)
    if len(parts) < 2:
        await message.answer(
            "Укажи имя проекта:\n<code>/ghnew my-project</code>\n\n"
            f"Будет создан репозиторий на GitHub + локально в "
            f"<code>{_PROJECTS_DEFAULT_DIR}/</code>",
            parse_mode="HTML",
        )
        return

    name = parts[1].strip()
    err = _validate_project_name(name)
    if err:
        await message.answer(f"❌ Имя: {html_escape(err)}", parse_mode="HTML")
        return

    target = (_PROJECTS_DEFAULT_DIR / name).resolve()
    if target.exists():
        await message.answer(
            f"❌ <code>{html_escape(str(target))}</code> уже существует.",
            parse_mode="HTML",
        )
        return

    await message.answer(
        f"🐙 Создаём <b>{html_escape(name)}</b>…\n\nВыбери приватность репозитория:",
        reply_markup=keyboards.gh_private_keyboard(name),
        parse_mode="HTML",
    )


@router.callback_query(F.data.startswith("gh:pub:") | F.data.startswith("gh:priv:"))
async def cb_ghnew_create(query: CallbackQuery) -> None:
    data = query.data
    if data.startswith("gh:pub:"):
        name = data[len("gh:pub:") :]
        private = False
        label = "публичный"
    else:
        name = data[len("gh:priv:") :]
        private = True
        label = "приватный"

    err = _validate_project_name(name)
    if err:
        await query.answer("Некорректное имя", show_alert=True)
        return

    target = (_PROJECTS_DEFAULT_DIR / name).resolve()
    if target.exists():
        await query.message.edit_text(
            f"❌ <code>{html_escape(str(target))}</code> уже существует.",
            parse_mode="HTML",
        )
        await query.answer()
        return

    await query.message.edit_text(
        f"⏺ Создаю {label} репо <b>{html_escape(name)}</b>…",
        parse_mode="HTML",
    )
    await query.answer()

    try:
        # 1. Локально (git init + README + .gitignore + commit)
        path = await _create_project_on_disk(name)
        # 2. GitHub + push
        html_url = await _gh_create_and_push(name, private=private, path=path)
    except Exception as e:
        logger.exception("ghnew failed")
        await query.message.edit_text(
            f"❌ Не удалось:\n<code>{html_escape(str(e))}</code>",
            parse_mode="HTML",
        )
        return

    await query.message.edit_text(
        f"✅ Готово\n\n"
        f"📁 Локально: <code>{html_escape(str(path))}</code>\n"
        f"🌐 GitHub: {html_escape(html_url)}",
        parse_mode="HTML",
    )
    await _create_session(query.message.chat.id, query.message, str(path))


@router.callback_query(F.data == "gh:skip")
async def cb_ghnew_skip(query: CallbackQuery) -> None:
    await query.message.edit_text("⏭ Пропущено.")
    await query.answer()


# ---------- /clone ----------

# Регулярка: извлекает owner/repo из GitHub-URL или сокращения user/repo
_GITHUB_URL_RE = re.compile(
    r"^(?:"
    r"https?://github\.com/"
    r"|git@github\.com:"
    r")?(?P<owner>[\w.-]+)/(?P<repo>[\w.-]+?)(?:\.git)?$"
)


def _normalize_github_url(raw: str) -> tuple[str, str] | None:
    """Нормализовать URL/сокращение в (ssh_url, repo_name).

    Возвращает None, если не удалось распарсить.
    """
    raw = raw.strip()
    if not raw:
        return None
    m = _GITHUB_URL_RE.match(raw)
    if not m:
        return None
    owner = m.group("owner")
    repo = m.group("repo")
    ssh_url = f"git@github.com:{owner}/{repo}.git"
    return ssh_url, repo


async def _clone_repo(ssh_url: str, dest: Path) -> None:
    """Клонировать репо в dest. Бросает исключение при ошибке."""
    if dest.exists():
        raise FileExistsError(f"уже существует: {dest}")

    r = await run_shell(
        f"git clone --quiet {ssh_url} {dest}",
        cwd=dest.parent,
        timeout=300,
    )
    if not r.ok:
        raise RuntimeError(r.output[:300])


@router.message(Command("clone"))
async def cmd_clone(message: Message) -> None:
    """Склонировать GitHub-репозиторий в ~/bots/<name>."""
    parts = (message.text or "").split(maxsplit=1)
    if len(parts) < 2 or not parts[1].strip():
        await message.answer(
            "Укажи репозиторий:\n\n"
            "<code>/clone user/repo</code>\n"
            "<code>/clone https://github.com/user/repo</code>\n"
            "<code>/clone git@github.com:user/repo.git</code>\n\n"
            f"Клонируется в <code>{_PROJECTS_DEFAULT_DIR}/&lt;repo&gt;</code>",
            parse_mode="HTML",
        )
        return

    raw = parts[1].strip()
    parsed = _normalize_github_url(raw)
    if parsed is None:
        await message.answer(
            f"❌ Не разобрал URL: <code>{html_escape(raw)}</code>\n"
            "Ожидаю <code>owner/repo</code> или полный GitHub-URL.",
            parse_mode="HTML",
        )
        return

    ssh_url, repo_name = parsed
    dest = (_PROJECTS_DEFAULT_DIR / repo_name).resolve()

    try:
        dest.relative_to(_PROJECTS_DEFAULT_DIR.resolve())
    except ValueError:
        await message.answer("❌ Путь вне разрешённой директории.")
        return

    if dest.exists():
        await message.answer(
            f"❌ <code>{html_escape(str(dest))}</code> уже существует.",
            parse_mode="HTML",
        )
        return

    await message.answer(
        f"⏺ Клонирую <b>{html_escape(repo_name)}</b>…\n"
        f"<code>{html_escape(ssh_url)}</code>",
        parse_mode="HTML",
    )

    try:
        await _clone_repo(ssh_url, dest)
    except Exception as e:
        logger.exception("clone failed")
        await message.answer(
            f"❌ Не удалось клонировать:\n<code>{html_escape(str(e))}</code>",
            parse_mode="HTML",
        )
        return

    await message.answer(
        f"✅ Склонировано: <code>{html_escape(str(dest))}</code>",
        parse_mode="HTML",
    )
    await _create_session(message.chat.id, message, str(dest))


@router.callback_query(F.data == "ghnew_hint")
async def cb_ghnew_hint(query: CallbackQuery) -> None:
    if not config.GITHUB_TOKEN:
        await query.message.edit_text(
            "🌐 <b>Создать репо на GitHub</b>\n\n"
            "❌ <code>GITHUB_TOKEN</code> не задан в <code>.env</code>.\n"
            "Создай токен на github.com/settings/tokens (scope: repo).",
            parse_mode="HTML",
        )
        await query.answer()
        return
    await query.message.edit_text(
        "🌐 <b>Создать репо на GitHub</b>\n\n"
        "Отправь: <code>/ghnew my-project</code>\n\n"
        f"• Репо создастся на GitHub (приватный или публичный — выберешь кнопкой)\n"
        f"• Локально: <code>{_PROJECTS_DEFAULT_DIR}/&lt;name&gt;</code>\n"
        "• git init + README.md + .gitignore + push",
        parse_mode="HTML",
    )
    await query.answer()


@router.callback_query(F.data == "clone_hint")
async def cb_clone_hint(query: CallbackQuery) -> None:
    await query.message.edit_text(
        "🐙 <b>Клонировать репозиторий</b>\n\n"
        "Отправь: <code>/clone user/repo</code>\n\n"
        "Поддерживаются формы:\n"
        "• <code>user/repo</code>\n"
        "• <code>https://github.com/user/repo</code>\n"
        "• <code>git@github.com:user/repo.git</code>\n\n"
        f"Клонируется в <code>{_PROJECTS_DEFAULT_DIR}/&lt;repo&gt;</code>",
        parse_mode="HTML",
    )
    await query.answer()


@router.callback_query(
    F.data.startswith("gh:pub_new:") | F.data.startswith("gh:priv_new:")
)
async def cb_gh_offer_create(query: CallbackQuery) -> None:
    """Создание GitHub-репо после локального /new."""
    data = query.data
    if data.startswith("gh:pub_new:"):
        name = data[len("gh:pub_new:") :]
        private = False
        label = "публичный"
    else:
        name = data[len("gh:priv_new:") :]
        private = True
        label = "приватный"

    path = (_PROJECTS_DEFAULT_DIR / name).resolve()
    if not path.is_dir():
        await query.answer("Проект не найден", show_alert=True)
        return

    await query.message.edit_text(
        f"⏺ Создаю {label} репо <b>{html_escape(name)}</b> на GitHub…",
        parse_mode="HTML",
    )
    await query.answer()

    try:
        html_url = await _gh_create_and_push(name, private=private, path=path)
    except Exception as e:
        logger.exception("gh offer create failed")
        await query.message.edit_text(
            f"⚠️ Локально проект есть, но на GitHub не удалось:\n"
            f"<code>{html_escape(str(e))}</code>\n\n"
            f"Можно повторить: <code>/ghnew {html_escape(name)}</code>",
            parse_mode="HTML",
        )
        return

    await query.message.edit_text(
        f"✅ Готово\n\n"
        f"📁 <code>{html_escape(str(path))}</code>\n"
        f"🌐 {html_escape(html_url)}",
        parse_mode="HTML",
    )
    await _create_session(query.message.chat.id, query.message, str(path))


@router.callback_query(F.data.startswith("gh:skip_new:"))
async def cb_gh_offer_skip(query: CallbackQuery) -> None:
    """Пропустить создание GitHub-репо — просто открыть сессию."""
    name = query.data[len("gh:skip_new:") :]
    path = (_PROJECTS_DEFAULT_DIR / name).resolve()
    if not path.is_dir():
        await query.answer("Проект не найден", show_alert=True)
        return
    await query.message.edit_text(
        f"✅ Проект создан: <code>{html_escape(str(path))}</code>\n"
        f"<i>Без GitHub-репозитория.</i>",
        parse_mode="HTML",
    )
    await query.answer("Пропущено")
    await _create_session(query.message.chat.id, query.message, str(path))
