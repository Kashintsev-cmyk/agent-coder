"""Telegram-хендлеры: команды, задачи, callback-кнопки."""
from __future__ import annotations

import asyncio
import logging
from pathlib import Path

from aiogram import Bot, F
from aiogram.types import CallbackQuery, Message

from agent_coder.agent.loop import AgentLoop
from agent_coder.agent.prompts import Mode
from agent_coder.agent.session import Session
from agent_coder.config import config as config
from agent_coder.llm.client import LLMClient
from agent_coder.telegram.renderer import html_escape
from agent_coder.tools import build_registry

logger = logging.getLogger(__name__)

# Роутер, утилиты и глобальное состояние — в shared.py (единая точка).
# Импорт подмодулей — регистрация хендлеров на router (side-effect).
# Импорт обязателен: без него хендлеры не активируются.
from . import commands, github, ops, session_menu  # noqa: F401
from .commands import (
    _send_history_preview as _send_history_preview,
)
from .commands import (
    cb_back as cb_back,
)
from .commands import (
    cb_cost as cb_cost,
)
from .commands import (
    cb_delete_cancel as cb_delete_cancel,
)
from .commands import (
    cb_delete_confirm as cb_delete_confirm,
)
from .commands import (
    cb_history as cb_history,
)
from .commands import (
    cb_history_md as cb_history_md,
)
from .commands import (
    cb_mode as cb_mode,
)
from .commands import (
    cb_new as cb_new,
)
from .commands import (
    cb_new_project_hint as cb_new_project_hint,
)
from .commands import (
    cb_open as cb_open,
)
from .commands import (
    cb_project_create_no as cb_project_create_no,
)
from .commands import (
    cb_project_create_yes as cb_project_create_yes,
)
from .commands import (
    cb_sessions as cb_sessions,
)
from .commands import (
    cmd_balance as cmd_balance,
)
from .commands import (
    cmd_cancel as cmd_cancel,
)
from .commands import (
    cmd_cost as cmd_cost,
)
from .commands import (
    cmd_current as cmd_current,
)
from .commands import (
    cmd_delete as cmd_delete,
)
from .commands import (
    cmd_help as cmd_help,
)
from .commands import (
    cmd_history as cmd_history,
)
from .commands import (
    cmd_mode as cmd_mode,
)
from .commands import (
    cmd_model as cmd_model,
)
from .commands import (
    cmd_new as cmd_new,
)
from .commands import (
    cmd_projects as cmd_projects,
)
from .commands import (
    cmd_reasoning as cmd_reasoning,
)
from .commands import (
    cmd_sessions as cmd_sessions,
)
from .commands import (
    cmd_start as cmd_start,
)
from .github import (
    _clone_repo as _clone_repo,
)
from .github import (
    _normalize_github_url as _normalize_github_url,
)
from .project_utils import (
    _PROJECTS_DEFAULT_DIR as _PROJECTS_DEFAULT_DIR,
)
from .project_utils import (
    _create_project_on_disk as _create_project_on_disk,
)
from .project_utils import (
    _create_session as _create_session,
)
from .project_utils import (
    _validate_project_name as _validate_project_name,
)
from .runner import (
    _join_buffer as _join_buffer,
)
from .runner import (
    _split_for_telegram as _split_for_telegram,
)
from .runner import (
    _TaskRunner as _TaskRunner,
)
from .shared import (
    _audit_reasoning_warning as _audit_reasoning_warning,
)
from .shared import (
    _get_active_session as _get_active_session,
)
from .shared import (
    _pending as _pending,
)
from .shared import (
    _running as _running,
)
from .shared import (
    _should_preserve_history as _should_preserve_history,
)
from .shared import (
    _shutting_down as _shutting_down,
)
from .shared import (
    router as router,
)
from .shared import (
    shutdown as shutdown,
)

# ---------- Основной обработчик задачи ----------

_PHOTO_MAX_BYTES = 5 * 1024 * 1024


@router.message(F.photo)
async def handle_task_photo(message: Message, bot: Bot) -> None:
    """Приём фото: сохраняем, прикладываем к задаче как image_url."""
    session = _get_active_session(message.chat.id)
    if session is None:
        await message.answer("Сначала выбери проект: /projects")
        return

    # Проверка: модель должна поддерживать vision
    from agent_coder.model_catalog import catalog
    m = catalog.get(session.model)
    if not (m and m.vision):
        name = m.name if m else session.model
        await message.answer(
            f"⚠️ Модель <b>{html_escape(name)}</b> не принимает изображения.\n"
            f"Смени модель на vision-способную (бейдж 👁).",
            parse_mode="HTML",
        )
        return

    # Берём максимальное разрешение — последний элемент в message.photo
    photo = message.photo[-1] if message.photo else None
    if photo is None:
        await message.answer("⚠️ Не удалось прочитать фото.")
        return

    if photo.file_size and photo.file_size > _PHOTO_MAX_BYTES:
        await message.answer(
            f"⚠️ Фото слишком большое: {photo.file_size // 1024} КБ "
            f"(макс. {_PHOTO_MAX_BYTES // 1024} КБ)."
        )
        return

    try:
        buf = await bot.download(photo)
        data = buf.read() if buf is not None else b""
    except Exception as e:
        logger.exception("photo download failed")
        await message.answer(
            f"❌ Не удалось скачать фото: <code>{html_escape(str(e))}</code>",
            parse_mode="HTML",
        )
        return

    if not data:
        await message.answer("⚠️ Пустое фото.")
        return

    path = session.save_media(data, ext="jpg")
    caption = (message.caption or "").strip()
    task_text = caption or "Посмотри на изображение и учти его в задаче."

    await _start_task(
        bot, message, task_text,
        source_note=f"фото <code>{path.name}</code>",
        images=[path],
    )


@router.message(F.text & ~F.text.startswith("/"))
async def handle_task(message: Message, bot: Bot) -> None:
    await _start_task(bot, message, message.text or "")


# Лимит размера файла-промта (байт). Telegram Bot API и сам отдаёт файлы
# до 20 МБ, но промт такого размера бессмыслен — ограничиваемся 1 МБ.
_PROMPT_FILE_MAX_BYTES = 1024 * 1024

_PROMPT_FILE_EXT = {".txt", ".md", ".markdown", ".rst", ".csv", ".json",
                    ".yaml", ".yml", ".py", ".js", ".ts", ".sh"}


@router.message(F.document)
async def handle_task_document(message: Message, bot: Bot) -> None:
    """Приём длинного промта файлом (.txt/.md и т.п.).

    Telegram ограничивает текст сообщения 4096 символами, поэтому длинные
    промты удобнее отправлять документом. Подпись к файлу (caption), если
    есть, добавляется перед содержимым как инструкция.
    """
    doc = message.document
    if doc is None:
        return

    name = doc.file_name or "prompt.txt"
    ext = Path(name).suffix.lower()
    if ext not in _PROMPT_FILE_EXT and ext != "":
        await message.answer(
            f"⚠️ Неизвестный тип файла: <code>{html_escape(name)}</code>\n"
            f"Поддерживаются текстовые форматы: "
            f"<code>{', '.join(sorted(_PROMPT_FILE_EXT))}</code>",
            parse_mode="HTML",
        )
        return

    if doc.file_size and doc.file_size > _PROMPT_FILE_MAX_BYTES:
        await message.answer(
            f"⚠️ Файл слишком большой: {doc.file_size // 1024} КБ "
            f"(макс. {_PROMPT_FILE_MAX_BYTES // 1024} КБ).",
        )
        return

    if not (doc.mime_type or "").startswith("text/") and ext == "":
        await message.answer("⚠️ Пришли промт текстовым файлом (.txt/.md).")
        return

    try:
        buf = await bot.download(doc)
        raw = buf.read() if buf is not None else b""
        content = raw.decode("utf-8", errors="replace")
    except Exception as e:
        logger.exception("prompt file download failed")
        await message.answer(
            f"❌ Не удалось скачать файл: <code>{html_escape(str(e))}</code>",
            parse_mode="HTML",
        )
        return

    content = content.strip()
    if not content:
        await message.answer("⚠️ Файл пустой.")
        return

    caption = (message.caption or "").strip()
    task_text = f"{caption}\n\n{content}" if caption else content

    await _start_task(bot, message, task_text,
                      source_note=f"файл <code>{html_escape(name)}</code>")


async def _start_task(
    bot: Bot, message: Message, task_text: str, source_note: str = "",
    images: list[Path] | None = None,
) -> None:
    """Общая точка запуска задачи (из текста или из файла-промта)."""
    chat_id = message.chat.id
    session = _get_active_session(chat_id)
    if session is None:
        await message.answer(
            "Сначала выбери проект: /projects",
        )
        return

    if not task_text.strip() and not images:
        await message.answer("⚠️ Пустой запрос.")
        return

    running = _running.get(chat_id)
    if running and not running.done():
        await message.answer(
            "⚠️ Другая задача уже выполняется. /cancel чтобы прервать."
        )
        return

    if source_note:
        await message.answer(
            f"📄 Принят {source_note} · {len(task_text)} символов",
            parse_mode="HTML",
        )

    # Вопрос (заканчивается на ?) — быстрый ответ без агентного цикла
    if images is None and _is_question(task_text):
        task = asyncio.create_task(
            _answer_question(bot, chat_id, session, task_text)
        )
        _running[chat_id] = task
        return

    task = asyncio.create_task(_run_task(bot, chat_id, session, task_text, images=images))
    _running[chat_id] = task


# ---------- Callback: approval ----------

@router.callback_query(F.data.startswith("approve:"))
async def cb_approve(query: CallbackQuery) -> None:
    rid = query.data.split(":", 1)[1]
    fut = _pending.get(rid)
    if fut and not fut.done():
        fut.set_result(True)
        try:
            await query.message.edit_text(
                "✅ <b>Разрешено</b>",
                reply_markup=None,
                parse_mode="HTML",
            )
        except Exception:
            pass
    else:
        await query.answer("Устарело", show_alert=True)
    await query.answer()


@router.callback_query(F.data.startswith("deny:"))
async def cb_deny(query: CallbackQuery) -> None:
    rid = query.data.split(":", 1)[1]
    fut = _pending.get(rid)
    if fut and not fut.done():
        fut.set_result(False)
        try:
            await query.message.edit_text(
                "❌ <b>Отклонено</b>",
                reply_markup=None,
                parse_mode="HTML",
            )
        except Exception:
            pass
    else:
        await query.answer("Устарело", show_alert=True)
    await query.answer()


# ---------- Запуск задачи ----------

def _is_question(text: str) -> bool:
    """Сообщение — вопрос? (заканчивается на ? и не слишком длинное).

    Длинные сообщения с вопросом в конце всё равно считаются задачей.
    """
    t = text.rstrip()
    return t.endswith("?") and len(t) <= 200


async def _answer_question(
    bot: Bot, chat_id: int, session: Session, question: str,
) -> None:
    """Быстрый ответ на вопрос: один LLM-запрос с CHAT-промптом, без tools.

    session.mode НЕ меняется. Ответ добавляется в историю сессии.
    """
    from agent_coder.agent.prompts import Mode, system_prompt
    from agent_coder.config import config as _cfg
    from agent_coder.llm.client import LLMClient

    client = LLMClient()
    await client.start()
    try:
        chat_system = system_prompt(Mode.CHAT, session.workspace)
        # system — от CHAT, остальное — история как есть (без старого system)
        msgs = [{"role": "system", "content": chat_system}]
        msgs += [m for m in session.messages if m.get("role") != "system"]
        msgs.append({"role": "user", "content": question})

        resp = await client.chat(
            messages=msgs,
            model=session.model,
            temperature=_cfg.TEMPERATURE,
            reasoning_effort=session.reasoning_effort,
            web_search=session.web_search,
            service_tier=session.service_tier or _cfg.SERVICE_TIER,
            verbosity=session.verbosity or None,
        )
        session.add_usage(resp.usage)
        session.add_message({"role": "user", "content": question})
        session.add_message({
            "role": "assistant",
            "content": resp.message.content or "",
        })
        session.save()

        await bot.send_message(
            chat_id, resp.message.content or "(пустой ответ)",
        )
    except Exception as e:
        logger.exception("question answer failed")
        await bot.send_message(
            chat_id, f"❌ Ошибка: {html_escape(str(e))}",
        )
    finally:
        try:
            await client.close()
        except Exception:
            pass


async def _run_task(bot: Bot, chat_id: int, session: Session, task_text: str,
                    images: list[Path] | None = None) -> None:
    """Точка входа задачи.

    ВАЖНО: даже если не удалось отправить статус-сообщение или поднять
    LLMClient, _running[chat_id] должен быть очищен — иначе чат навсегда
    получит «Другая задача уже выполняется» и придётся рестартовать бота.
    """
    client: LLMClient | None = None
    runner: _TaskRunner | None = None
    try:
        status_msg = await bot.send_message(chat_id, "⏺ Начинаю...")
        runner = _TaskRunner(bot, chat_id, status_msg.message_id)

        client = LLMClient()
        await client.start()

        loop = AgentLoop(
            client=client,
            registry=build_registry(),
            session=session,
            events=runner.events(),
        )
        await loop.run(task_text, images=images)
        session.save()
        await runner.flush()

        # Если это был Audit-режим — генерируем отчёт
        if session.mode == Mode.AUDIT:
            await _generate_and_send_audit(bot, chat_id, session)

    except asyncio.CancelledError:
        if not _shutting_down:
            try:
                await bot.send_message(chat_id, "⏹ Отменено")
            except Exception:
                pass
        raise
    except Exception as e:
        logger.exception("task failed")
        try:
            await bot.send_message(chat_id, f"❌ Ошибка: {e}")
        except Exception:
            pass
    finally:
        if client is not None:
            try:
                await client.close()
            except Exception as e:
                logger.warning("client.close() failed: %s", e)
        _running.pop(chat_id, None)


async def _generate_and_send_audit(bot: Bot, chat_id: int, session: Session) -> None:
    """Генерирует .md отчёт и отправляет его в чат."""
    from aiogram.types import FSInputFile

    from agent_coder.llm.types import format_cost
    from agent_coder.reports.audit_saver import save_audit_report

    try:
        md_path = save_audit_report(session, also_in_workspace=True)
    except Exception as e:
        logger.exception("audit report generation failed")
        await bot.send_message(
            chat_id,
            f"⚠️ Не удалось сохранить отчёт: <code>{html_escape(str(e))}</code>",
            parse_mode="HTML",
        )
        return

    size_kb = max(1, md_path.stat().st_size // 1024)
    u = session.total_usage

    # Серая плашка со статистикой
    block = (
        "<blockquote>"
        f"⏱ {session.request_count} запросов · 💰 {format_cost(u.cost)}\n"
        f"🔢 {u.input_tokens} in / {u.output_tokens} out"
        + (f" · 💾 {u.cache_read_tokens} кэш" if u.cache_read_tokens else "")
        + "</blockquote>"
    )

    text = (
        f"✅ <b>Аудит завершён</b>\n\n"
        f"📄 MD: {size_kb} KB\n"
        f"📁 <code>{md_path}</code>\n\n"
        f"📋 Дублировано в проект: <code>AUDIT_REPORT.md</code>\n\n"
        f"{block}"
    )
    await bot.send_message(chat_id, text, parse_mode="HTML")

    try:
        await bot.send_document(
            chat_id,
            FSInputFile(str(md_path)),
            caption=f"🔒 {session.title[:80]}",
        )
    except Exception as e:
        logger.warning("send_document failed: %s", e)
        await bot.send_message(chat_id, f"⚠️ MD не отправился: {e}")
