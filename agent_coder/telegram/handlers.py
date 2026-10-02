"""Telegram-хендлеры: команды, задачи, callback-кнопки."""
from __future__ import annotations

import asyncio
import json
import logging
import re
import tempfile
import time
import uuid
from pathlib import Path

from aiogram import Bot, F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command, CommandStart
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)

from agent_coder.agent.loop import AgentLoop, LoopEvents
from agent_coder.agent.prompts import Mode
from agent_coder.agent.session import Session
from agent_coder.config import config
from agent_coder.llm.client import LLMClient
from agent_coder.telegram import keyboards, renderer
from agent_coder.telegram import state as tg_state
from agent_coder.tools import build_registry

logger = logging.getLogger(__name__)

router = Router()

# Ограничиваем доступ одним пользователем
router.message.filter(F.from_user.id == config.TELEGRAM_USER_ID)
router.callback_query.filter(F.from_user.id == config.TELEGRAM_USER_ID)

# Активные задачи: {chat_id: asyncio.Task}
_running: dict[int, asyncio.Task] = {}
# Ожидания approval: {request_id: asyncio.Future}
_pending: dict[str, asyncio.Future] = {}
# Идёт ли корректная остановка (меняет тексты уведомлений)
_shutting_down = False


async def shutdown(bot: Bot) -> None:
    """Корректная остановка: уведомить и прервать активные задачи.

    Вызывается при получении SIGTERM/SIGINT перед выходом процесса.
    """
    global _shutting_down
    _shutting_down = True

    # Висящие подтверждения сразу отклоняем, чтобы циклы не ждали таймаут
    for request_id, future in list(_pending.items()):
        _pending.pop(request_id, None)
        if not future.done():
            future.set_result(False)

    active = [(cid, t) for cid, t in _running.items() if not t.done()]
    if not active:
        return

    for cid, _ in active:
        try:
            await bot.send_message(cid, "🔄 Бот перезагружается, задача прервана.")
        except Exception as e:
            logger.warning("shutdown notify failed: %s", e)

    for _, task in active:
        task.cancel()
    await asyncio.gather(*(t for _, t in active), return_exceptions=True)


# ---------- Утилиты ----------

def _list_projects() -> list[Path]:
    projects: list[Path] = []
    for root in config.TELEGRAM_PROJECTS_DIRS:
        if not root.exists():
            continue
        try:
            for child in sorted(root.iterdir()):
                if child.is_dir() and not child.name.startswith("."):
                    projects.append(child)
        except PermissionError:
            continue
    return projects[:30]


def _fmt_cost(value: float) -> str:
    from agent_coder.llm.types import format_cost
    return format_cost(value)


def _session_summary(s: Session) -> str:
    icon = "🟡" if s.mode.value == "plan" else "🟢"
    return (
        f"{icon} <b>{s.title or '(без названия)'}</b>\n"
        f"   <code>{s.id}</code> · {s.mode.value} · "
        f"{_fmt_cost(s.total_usage.cost)} · {s.age_str()}"
    )


# ---------- /start, /help ----------

@router.message(CommandStart())
async def cmd_start(message: Message) -> None:
    projects = _list_projects()
    text = (
        "👋 <b>agent-coder</b>\n\n"
        "AI-агент для работы с кодом на сервере.\n\n"
        "<b>Задача:</b>\n"
        "  Отправь текст или приложи файл (.txt/.md) — для длинных промтов.\n"
        "\n"
        "<b>Основное:</b>\n"
        "  /projects — выбрать проект\n"
        "  /sessions — список сессий\n"
        "  /new &lt;путь&gt; — новая сессия\n"
        "  /current — текущая сессия\n"
        "\n"
        "<b>Настройки сессии:</b>\n"
        "  /model — выбрать модель\n"
        "  /reasoning — уровень reasoning\n"
        "  /mode plan | act | audit — режим\n"
        "\n"
        "<b>Информация:</b>\n"
        "  /cost — стоимость сессии\n"
        "  /balance — баланс RouterAI\n"
        "  /server — диагностика сервера\n"
        "  /audit — аудит проекта\n"
        "\n"
        "<b>Управление:</b>\n"
        "  /cancel — прервать задачу\n"
        "  /help — эта справка\n"
    )
    await message.answer(
        text,
        reply_markup=keyboards.projects_keyboard(projects),
        parse_mode="HTML",
    )


@router.message(Command("help"))
async def cmd_help(message: Message) -> None:
    await cmd_start(message)


# ---------- /projects ----------

@router.message(Command("projects"))
async def cmd_projects(message: Message) -> None:
    projects = _list_projects()
    if not projects:
        await message.answer("Не нашёл проектов в настроенных папках.")
        return
    await message.answer(
        "📂 <b>Проекты:</b>\n\nВыбери проект:",
        reply_markup=keyboards.projects_keyboard(projects),
        parse_mode="HTML",
    )


# ---------- /new ----------

@router.message(Command("new"))
async def cmd_new(message: Message) -> None:
    parts = (message.text or "").split(maxsplit=1)
    if len(parts) < 2:
        await message.answer(
            "Укажи путь: <code>/new /home/botuser/projects/weather-bot</code>",
            parse_mode="HTML",
        )
        return
    await _create_session(message.chat.id, message, parts[1].strip())


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
        reply_markup=keyboards.session_menu_keyboard(session.id, session.model, session.reasoning_effort),
        parse_mode="HTML",
    )


# ---------- /sessions ----------

@router.message(Command("sessions"))
async def cmd_sessions(message: Message) -> None:
    sessions = Session.list_all()
    if not sessions:
        await message.answer("Сессий пока нет. /projects — выбрать проект.")
        return
    await message.answer(
        "📚 <b>Последние сессии:</b>\n\nВыбери:",
        reply_markup=keyboards.sessions_keyboard(sessions),
        parse_mode="HTML",
    )


# ---------- /current, /cost, /mode ----------

@router.message(Command("current"))
async def cmd_current(message: Message) -> None:
    session = _get_active_session(message.chat.id)
    if session is None:
        await message.answer("Нет активной сессии. /projects — выбрать проект.")
        return
    await message.answer(
        f"Текущая сессия:\n{_session_summary(session)}",
        reply_markup=keyboards.session_menu_keyboard(session.id, session.model, session.reasoning_effort),
        parse_mode="HTML",
    )


@router.message(Command("cost"))
async def cmd_cost(message: Message) -> None:
    session = _get_active_session(message.chat.id)
    if session is None:
        await message.answer("Нет активной сессии.")
        return
    u = session.total_usage
    await message.answer(
        f"💰 <b>Сессия {session.id}</b>\n"
        f"Запросов: {session.request_count}\n"
        f"Tokens in: {u.input_tokens} (cache {u.cache_read_tokens})\n"
        f"Tokens out: {u.output_tokens}\n"
        f"Стоимость: <b>{_fmt_cost(u.cost)}</b>",
        parse_mode="HTML",
    )


@router.message(Command("mode"))
async def cmd_mode(message: Message) -> None:
    session = _get_active_session(message.chat.id)
    if session is None:
        await message.answer("Нет активной сессии.")
        return
    parts = (message.text or "").split(maxsplit=1)
    valid = {"plan": Mode.PLAN, "act": Mode.ACT, "audit": Mode.AUDIT}
    if len(parts) < 2 or parts[1].strip() not in valid:
        await message.answer(
            f"Текущий режим: <b>{session.mode.value}</b>\n\n"
            f"Сменить: /mode plan | /mode act | /mode audit\n\n"
            f"• <b>plan</b> — планирование (только чтение)\n"
            f"• <b>act</b> — работа с кодом (правки, команды)\n"
            f"• <b>audit</b> — строгий аудит (только отчёт)",
            parse_mode="HTML",
        )
        return
    new_mode = valid[parts[1].strip()]
    if session.mode != new_mode:
        session.mode = new_mode
        session.messages = []
        session.save()
    await message.answer(
        f"Режим: <b>{new_mode.value}</b>",
        parse_mode="HTML",
    )


# ---------- /cancel ----------

@router.message(Command("cancel"))
async def cmd_cancel(message: Message) -> None:
    task = _running.get(message.chat.id)
    if task and not task.done():
        task.cancel()
        await message.answer("⏹ Задача отменена.")
    else:
        await message.answer("Нечего отменять.")


# ---------- Основной обработчик задачи ----------

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
            f"⚠️ Неизвестный тип файла: <code>{_html_escape(name)}</code>\n"
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
            f"❌ Не удалось скачать файл: <code>{_html_escape(str(e))}</code>",
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
                      source_note=f"файл <code>{_html_escape(name)}</code>")


async def _start_task(
    bot: Bot, message: Message, task_text: str, source_note: str = "",
) -> None:
    """Общая точка запуска задачи (из текста или из файла-промта)."""
    chat_id = message.chat.id
    session = _get_active_session(chat_id)
    if session is None:
        await message.answer(
            "Сначала выбери проект: /projects",
        )
        return

    if not task_text.strip():
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

    task = asyncio.create_task(_run_task(bot, chat_id, session, task_text))
    _running[chat_id] = task


def _get_active_session(chat_id: int) -> Session | None:
    sid = tg_state.get_active_session(chat_id)
    if not sid:
        return None
    return Session.load(sid)


# ---------- Callback: выбор проекта / сессии / режима ----------

@router.callback_query(F.data.startswith("new:"))
async def cb_new(query: CallbackQuery, bot: Bot) -> None:
    path = query.data.split(":", 1)[1]
    await query.message.edit_reply_markup(reply_markup=None)
    await _create_session(query.message.chat.id, query.message, path)
    await query.answer()


@router.callback_query(F.data == "sessions")
async def cb_sessions(query: CallbackQuery) -> None:
    sessions = Session.list_all()
    if not sessions:
        await query.answer("Сессий нет", show_alert=True)
        return
    await query.message.edit_text(
        "📚 <b>Последние сессии:</b>\n\nВыбери:",
        reply_markup=keyboards.sessions_keyboard(sessions),
        parse_mode="HTML",
    )
    await query.answer()


@router.callback_query(F.data.startswith("open:"))
async def cb_open(query: CallbackQuery) -> None:
    sid = query.data.split(":", 1)[1]
    session = Session.load(sid)
    if session is None:
        await query.answer("Сессия не найдена", show_alert=True)
        return
    tg_state.set_active_session(query.message.chat.id, sid)
    await query.message.edit_text(
        f"✅ Активная сессия:\n{_session_summary(session)}",
        reply_markup=keyboards.session_menu_keyboard(sid, session.model, session.reasoning_effort),
        parse_mode="HTML",
    )
    await query.answer()


@router.callback_query(F.data.startswith("mode:"))
async def cb_mode(query: CallbackQuery) -> None:
    _, mode_str, sid = query.data.split(":", 2)
    session = Session.load(sid)
    if session is None:
        await query.answer("Сессия не найдена", show_alert=True)
        return
    if mode_str == "plan":
        new_mode = Mode.PLAN
    elif mode_str == "audit":
        new_mode = Mode.AUDIT
    else:
        new_mode = Mode.ACT
    if session.mode != new_mode:
        session.mode = new_mode
        session.messages = []
        session.save()
    await query.message.edit_text(
        f"Режим: <b>{new_mode.value}</b>\n{_session_summary(session)}",
        reply_markup=keyboards.session_menu_keyboard(sid, session.model, session.reasoning_effort),
        parse_mode="HTML",
    )
    await query.answer(f"Режим: {new_mode.value}")


@router.callback_query(F.data.startswith("cost:"))
async def cb_cost(query: CallbackQuery) -> None:
    sid = query.data.split(":", 1)[1]
    session = Session.load(sid)
    if session is None:
        await query.answer("Сессия не найдена", show_alert=True)
        return
    u = session.total_usage
    await query.answer(
        f"{_fmt_cost(u.cost)} · {session.request_count} запр.",
        show_alert=True,
    )


@router.callback_query(F.data == "back")
async def cb_back(query: CallbackQuery) -> None:
    projects = _list_projects()
    await query.message.edit_text(
        "📂 <b>Проекты:</b>\n\nВыбери проект:",
        reply_markup=keyboards.projects_keyboard(projects),
        parse_mode="HTML",
    )
    await query.answer()


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

async def _run_task(bot: Bot, chat_id: int, session: Session, task_text: str) -> None:
    status_msg = await bot.send_message(chat_id, "⏺ Начинаю...")
    runner = _TaskRunner(bot, chat_id, status_msg.message_id)

    client = LLMClient()
    await client.start()
    try:
        loop = AgentLoop(
            client=client,
            registry=build_registry(),
            session=session,
            events=runner.events(),
        )
        await loop.run(task_text)
        session.save()
        await runner.flush()

        # Если это был Audit-режим — генерируем отчёт
        if session.mode == Mode.AUDIT:
            await _generate_and_send_audit(bot, chat_id, session)

    except asyncio.CancelledError:
        if not _shutting_down:
            await bot.send_message(chat_id, "⏹ Отменено")
        raise
    except Exception as e:
        logger.exception("task failed")
        await bot.send_message(chat_id, f"❌ Ошибка: {e}")
    finally:
        await client.close()
        _running.pop(chat_id, None)


async def _generate_and_send_audit(bot: Bot, chat_id: int, session: Session) -> None:
    """Генерирует .md/.pdf отчёт и отправляет PDF в чат."""
    from aiogram.types import FSInputFile

    from agent_coder.llm.types import format_cost
    from agent_coder.reports.audit_saver import save_audit_report

    try:
        md_path, pdf_path = save_audit_report(session, also_in_workspace=True)
    except Exception as e:
        logger.exception("audit report generation failed")
        await bot.send_message(
            chat_id,
            f"⚠️ Не удалось сохранить отчёт: <code>{_html_escape(str(e))}</code>",
            parse_mode="HTML",
        )
        return

    size_kb = max(1, pdf_path.stat().st_size // 1024)
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
        f"📄 PDF: {size_kb} KB\n"
        f"📁 <code>{md_path}</code>\n"
        f"📁 <code>{pdf_path}</code>\n\n"
        f"📋 Дублировано в проект: <code>AUDIT_REPORT.md</code>\n\n"
        f"{block}"
    )
    await bot.send_message(chat_id, text, parse_mode="HTML")

    try:
        await bot.send_document(
            chat_id,
            FSInputFile(str(pdf_path)),
            caption=f"🔒 {session.title[:80]}",
        )
    except Exception as e:
        logger.warning("send_document failed: %s", e)
        await bot.send_message(chat_id, f"⚠️ PDF не отправился: {e}")


class _TaskRunner:
    """Собирает события агента и обновляет одно сообщение в Telegram."""

    def __init__(self, bot: Bot, chat_id: int, message_id: int) -> None:
        self.bot = bot
        self.chat_id = chat_id
        self.message_id = message_id
        self.buffer: list[str] = []
        self.last_text: str = ""
        self.last_edit: float = 0.0
        self.approval_msg_id: int | None = None
        self.extra_msg_ids: list[int] = []

    def events(self) -> LoopEvents:
        return LoopEvents(
            on_assistant_text=self._on_text,
            on_tool_call=self._on_tool_call,
            on_tool_result=self._on_tool_result,
            on_usage=self._on_usage,
            on_error=self._on_error,
            request_approval=self._request_approval,
        )

    async def _on_text(self, text: str) -> None:
        self.buffer.append(renderer.render_assistant_text(text))
        await self._schedule_edit()

    async def _on_tool_call(self, tc) -> None:
        self.buffer.append(renderer.render_tool_call(tc))
        await self._schedule_edit()

    async def _on_tool_result(self, tc, result: str) -> None:
        self.buffer.append(renderer.render_tool_result(tc, result))
        await self._schedule_edit()

    async def _on_usage(self, usage) -> None:
        self.buffer.append(renderer.render_usage(usage))
        await self._schedule_edit()

    async def _on_error(self, msg: str) -> None:
        self.buffer.append(f"✗ <b>{_html_escape(msg)}</b>")
        await self._schedule_edit()

    async def _schedule_edit(self) -> None:
        now = time.monotonic()
        if now - self.last_edit < 1.5:
            return
        self.last_edit = now
        await self.flush()

    async def flush(self) -> None:
        text = _join_buffer(self.buffer) if self.buffer else "⏺ Работаю..."
        if text == self.last_text:
            return
        self.last_text = text

        parts = _split_for_telegram(text)
        try:
            await self._render_parts(parts)
        except TelegramBadRequest as e:
            if "message is not modified" in str(e).lower():
                return
            logger.warning("flush failed (html): %s", e)
            try:
                plain = re.sub(r"<[^>]+>", "", parts[0])
                await self.bot.edit_message_text(
                    chat_id=self.chat_id,
                    message_id=self.message_id,
                    text=plain,
                )
            except Exception as e2:
                logger.warning("flush failed (plain): %s", e2)
        except Exception as e:
            logger.warning("flush failed: %s", e)

    async def _render_parts(self, parts: list[str]) -> None:
        """Вывести части в основное сообщение + управляемые продолжения.

        Продолжения переиспользуются (edit) и удаляются при сокращении
        текста, чтобы не копить в чате устаревшие «хвосты».
        """
        main_text = parts[0]
        if len(parts) > 1:
            main_text += "\n\n<i>(продолжение ниже)</i>"
        try:
            await self.bot.edit_message_text(
                chat_id=self.chat_id,
                message_id=self.message_id,
                text=main_text,
                parse_mode="HTML",
            )
        except TelegramBadRequest as e:
            # Основное сообщение могло не измениться (растут только
            # продолжения) — это не повод пропускать их обновление.
            if "message is not modified" not in str(e).lower():
                raise

        wanted = parts[1:]
        for i, part in enumerate(wanted):
            if i < len(self.extra_msg_ids):
                try:
                    await self.bot.edit_message_text(
                        chat_id=self.chat_id,
                        message_id=self.extra_msg_ids[i],
                        text=part,
                        parse_mode="HTML",
                    )
                except TelegramBadRequest as e:
                    if "message is not modified" not in str(e).lower():
                        raise
            else:
                msg = await self.bot.send_message(
                    self.chat_id, part, parse_mode="HTML",
                )
                self.extra_msg_ids.append(msg.message_id)

        while len(self.extra_msg_ids) > len(wanted):
            mid = self.extra_msg_ids.pop()
            try:
                await self.bot.delete_message(self.chat_id, mid)
            except Exception as e:
                logger.warning("delete continuation failed: %s", e)


    async def _request_approval(self, tc) -> bool:
        request_id = uuid.uuid4().hex[:8]
        future: asyncio.Future[bool] = asyncio.get_running_loop().create_future()
        _pending[request_id] = future

        args_text = json.dumps(tc.arguments, ensure_ascii=False, indent=2)
        if len(args_text) > 800:
            args_text = args_text[:800] + "…"

        text = (
            f"⚠️ <b>Подтверждение</b>\n\n"
            f"<code>{_html_escape(tc.name)}</code>\n"
            f"<pre>{_html_escape(args_text)}</pre>"
        )

        # Сбрасываем накопленный вывод, чтобы сообщение с кнопками оказалось
        # самым нижним (а не выше свежих «продолжений» лога).
        await self.flush()

        # Отправляем НОВОЕ сообщение, а не редактируем предыдущее: так кнопки
        # видны под актуальным (последним) выводом. Но чтобы в чате не копилась
        # стопка подтверждений, сначала удаляем предыдущее сообщение-запрос.
        # Итог: одновременно существует ровно одна запись с кнопками.
        if self.approval_msg_id is not None:
            try:
                await self.bot.delete_message(self.chat_id, self.approval_msg_id)
            except Exception as e:
                logger.warning("old approval delete failed: %s", e)
            self.approval_msg_id = None

        try:
            msg = await self.bot.send_message(
                self.chat_id,
                text,
                reply_markup=keyboards.approval_keyboard(request_id),
                parse_mode="HTML",
            )
            self.approval_msg_id = msg.message_id
        except Exception as e:
            logger.error("approval send failed: %s", e)
            _pending.pop(request_id, None)
            return False

        try:
            return await asyncio.wait_for(future, timeout=config.APPROVAL_TIMEOUT)
        except asyncio.TimeoutError:
            if self.approval_msg_id is not None:
                try:
                    await self.bot.edit_message_text(
                        chat_id=self.chat_id,
                        message_id=self.approval_msg_id,
                        text="⌛ Время ожидания истекло",
                        reply_markup=None,
                        parse_mode="HTML",
                    )
                except Exception:
                    pass
            return False
        finally:
            _pending.pop(request_id, None)


def _join_buffer(buffer: list[str]) -> str:
    """Склеить буфер в один текст БЕЗ обрезки.

    Обрезка больше не нужна — длинный текст разбивается через
    _split_for_telegram и отправляется несколькими сообщениями.
    """
    return "\n".join(buffer)


def _split_for_telegram(text: str, limit: int = 3900) -> list[str]:
    """Разбить текст на части ≤ limit символов по границам строк.

    Строку длиннее limit режем по пробелу, чтобы не порвать HTML-теги.
    """
    if len(text) <= limit:
        return [text]

    parts: list[str] = []
    current: list[str] = []
    current_len = 0

    def _flush() -> None:
        nonlocal current, current_len
        if current:
            parts.append("\n".join(current))
            current = []
            current_len = 0

    for line in text.split("\n"):
        while len(line) > limit:
            _flush()
            cut = line.rfind(" ", 0, limit)
            cut = cut if cut > 0 else limit
            parts.append(line[:cut])
            line = line[cut:].lstrip()
        added = len(line) + (1 if current else 0)
        if current and current_len + added > limit:
            _flush()
            added = len(line)
        current.append(line)
        current_len += added
    _flush()
    return parts


def _html_escape(s: str) -> str:
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")

# ---------- Меню сессии: модель, reasoning ----------

@router.callback_query(F.data == "menu_model")
async def cb_menu_model(query: CallbackQuery) -> None:
    session = _get_active_session(query.message.chat.id)
    if not session:
        await query.answer("Нет активной сессии", show_alert=True)
        return
    await query.message.edit_text(
        f"🧠 <b>Выбор модели</b>\n\nТекущая: <code>{session.model}</code>",
        reply_markup=keyboards.models_keyboard(session.model),
        parse_mode="HTML",
    )
    await query.answer()


@router.callback_query(F.data.startswith("setmodel:"))
async def cb_setmodel(query: CallbackQuery) -> None:
    model_id = query.data.split(":", 1)[1]
    session = _get_active_session(query.message.chat.id)
    if not session:
        await query.answer("Нет сессии", show_alert=True)
        return
    session.model = model_id
    session.messages = []  # сбрасываем историю — модель поменялась
    session.save()
    from agent_coder.models import get_model_name
    await query.message.edit_text(
        f"✅ Модель: <b>{get_model_name(model_id)}</b>\n"
        f"<code>{model_id}</code>\n\n"
        f"История сообщений очищена.",
        reply_markup=keyboards.session_menu_keyboard(session.id, session.model, session.reasoning_effort),
        parse_mode="HTML",
    )
    await query.answer(f"→ {get_model_name(model_id)}")


@router.callback_query(F.data == "menu_reasoning")
async def cb_menu_reasoning(query: CallbackQuery) -> None:
    session = _get_active_session(query.message.chat.id)
    if not session:
        await query.answer("Нет сессии", show_alert=True)
        return
    from agent_coder.models import find_model
    m = find_model(session.model)
    if m and not m.reasoning:
        await query.answer("Модель не поддерживает reasoning", show_alert=True)
        return
    await query.message.edit_text(
        f"💭 <b>Уровень reasoning</b>\n\nТекущий: <b>{session.reasoning_effort}</b>",
        reply_markup=keyboards.reasoning_keyboard(session.reasoning_effort),
        parse_mode="HTML",
    )
    await query.answer()


@router.callback_query(F.data.startswith("setreason:"))
async def cb_setreason(query: CallbackQuery) -> None:
    level = query.data.split(":", 1)[1]
    session = _get_active_session(query.message.chat.id)
    if not session:
        await query.answer("Нет сессии", show_alert=True)
        return
    session.reasoning_effort = level
    session.save()
    await query.message.edit_text(
        f"✅ Reasoning: <b>{level}</b>",
        reply_markup=keyboards.session_menu_keyboard(session.id, session.model, session.reasoning_effort),
        parse_mode="HTML",
    )
    await query.answer(f"→ {level}")


@router.callback_query(F.data == "session_back")
async def cb_session_back(query: CallbackQuery) -> None:
    session = _get_active_session(query.message.chat.id)
    if not session:
        await query.answer("Нет сессии", show_alert=True)
        return
    await query.message.edit_text(
        f"Сессия: <b>{session.title}</b>\n"
        f"<code>{session.id}</code> · {session.mode.value}\n"
        f"Стоимость: {_fmt_cost(session.total_usage.cost)}",
        reply_markup=keyboards.session_menu_keyboard(session.id, session.model, session.reasoning_effort),
        parse_mode="HTML",
    )
    await query.answer()


# ---------- Команды /model и /reasoning ----------

@router.message(Command("reasoning"))
async def cmd_reasoning(message: Message) -> None:
    session = _get_active_session(message.chat.id)
    if not session:
        await message.answer("Нет активной сессии.")
        return
    await message.answer(
        f"💭 <b>Reasoning</b>\n\nТекущий: <b>{session.reasoning_effort}</b>",
        reply_markup=keyboards.reasoning_keyboard(session.reasoning_effort),
        parse_mode="HTML",
    )


# ---------- Server menu ----------

@router.message(Command("server"))
async def cmd_server(message: Message) -> None:
    """Открывает меню серверной диагностики."""
    await message.answer(
        "🖥 <b>Сервер</b>\n\n"
        "Быстрая диагностика. Команды выполняются мгновенно, "
        "без обращения к модели и без подтверждений.",
        reply_markup=keyboards.server_menu_keyboard(),
        parse_mode="HTML",
    )


@router.callback_query(F.data.startswith("srv:"))
async def cb_server_op(query: CallbackQuery) -> None:
    import asyncio

    from agent_coder.server_ops import find_op

    op_id = query.data.split(":", 1)[1]
    op = find_op(op_id)
    if op is None:
        await query.answer("Неизвестная команда", show_alert=True)
        return

    await query.answer(f"Выполняю: {op.label}")
    await query.message.edit_text(
        f"🖥 <b>{op.label}</b> — {op.description}\n\n"
        f"<code>$ {op.cmd}</code>\n\n"
        f"⏳ Выполняю...",
        parse_mode="HTML",
    )

    # Выполняем напрямую, минуя LLM
    try:
        proc = await asyncio.create_subprocess_shell(
            op.cmd,
            cwd=tempfile.gettempdir(),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout_b, stderr_b = await asyncio.wait_for(proc.communicate(), timeout=30)
        stdout = stdout_b.decode("utf-8", errors="replace").strip()
        stderr = stderr_b.decode("utf-8", errors="replace").strip()
    except asyncio.TimeoutError:
        proc.kill()
        await proc.wait()
        await query.message.edit_text(
            f"🖥 <b>{op.label}</b>\n\n⏱ Таймаут 30 секунд",
            reply_markup=keyboards.server_menu_keyboard(),
            parse_mode="HTML",
        )
        return
    except Exception as e:
        await query.message.edit_text(
            f"🖥 <b>{op.label}</b>\n\n❌ Ошибка: <code>{_html_escape(str(e))}</code>",
            reply_markup=keyboards.server_menu_keyboard(),
            parse_mode="HTML",
        )
        return

    output = stdout or stderr or "(пусто)"
    if len(output) > 3500:
        output = output[:3500] + "\n... (обрезано)"

    await query.message.edit_text(
        f"🖥 <b>{op.label}</b> — {op.description}\n\n"
        f"<pre>{_html_escape(output)}</pre>",
        reply_markup=keyboards.server_menu_keyboard(),
        parse_mode="HTML",
    )


# ---------- Audit menu ----------

@router.message(Command("audit"))
async def cmd_audit(message: Message) -> None:
    session = _get_active_session(message.chat.id)
    if not session:
        await message.answer("Сначала выбери проект: /projects")
        return
    await message.answer(
        f"🔒 <b>Аудит проекта</b>\n\n"
        f"<code>{session.workspace}</code>\n\n"
        f"Бесплатные проверки — без обращения к модели.",
        reply_markup=keyboards.audit_menu_keyboard(),
        parse_mode="HTML",
    )


@router.callback_query(F.data.startswith("aud:"))
async def cb_audit_op(query: CallbackQuery) -> None:
    import asyncio

    from agent_coder.audit_ops import find_op

    session = _get_active_session(query.message.chat.id)
    if not session:
        await query.answer("Нет сессии", show_alert=True)
        return

    op_id = query.data.split(":", 1)[1]
    op = find_op(op_id)
    if op is None:
        await query.answer("Неизвестная команда", show_alert=True)
        return

    cmd = op.cmd.format(workspace=session.workspace)

    await query.answer(f"Выполняю: {op.label}")
    await query.message.edit_text(
        f"🔒 <b>{op.label}</b> — {op.description}\n\n"
        f"⏳ Выполняю...",
        parse_mode="HTML",
    )

    try:
        proc = await asyncio.create_subprocess_shell(
            cmd,
            cwd=session.workspace,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout_b, stderr_b = await asyncio.wait_for(proc.communicate(), timeout=180)
        stdout = stdout_b.decode("utf-8", errors="replace").strip()
        stderr = stderr_b.decode("utf-8", errors="replace").strip()
    except asyncio.TimeoutError:
        proc.kill()
        await proc.wait()
        await query.message.edit_text(
            f"🔒 <b>{op.label}</b>\n\n⏱ Таймаут 60 секунд",
            reply_markup=keyboards.audit_menu_keyboard(),
            parse_mode="HTML",
        )
        return
    except Exception as e:
        await query.message.edit_text(
            f"🔒 <b>{op.label}</b>\n\n❌ Ошибка: <code>{_html_escape(str(e))}</code>",
            reply_markup=keyboards.audit_menu_keyboard(),
            parse_mode="HTML",
        )
        return

    output = stdout or stderr or "(пусто)"
    if len(output) > 3500:
        output = output[:3500] + "\n... (обрезано)"

    await query.message.edit_text(
        f"🔒 <b>{op.label}</b> — {op.description}\n\n"
        f"<pre>{_html_escape(output)}</pre>",
        reply_markup=keyboards.audit_menu_keyboard(),
        parse_mode="HTML",
    )

@router.message(Command("balance"))
async def cmd_balance(message: Message) -> None:
    """Показать баланс routerai."""
    from agent_coder.agent.session import Session as _Session
    from agent_coder.llm.client import LLMClient
    from agent_coder.llm.types import format_cost

    client = LLMClient()
    await client.start()
    try:
        data = await client.get_balance()
    except Exception as e:
        await message.answer(
            f"❌ Не удалось получить баланс: <code>{_html_escape(str(e))}</code>",
            parse_mode="HTML",
        )
        return
    finally:
        await client.close()

    # RouterAI отдаёт: {"data": {"credits": <float>}}
    # Разбираем любую структуру — плоскую или вложенную
    credits: float | None = None

    def _find_credits(obj) -> float | None:
        if isinstance(obj, dict):
            for key in ("credits", "balance", "credit"):
                v = obj.get(key)
                if isinstance(v, (int, float)):
                    return float(v)
            for v in obj.values():
                found = _find_credits(v)
                if found is not None:
                    return found
        return None

    credits = _find_credits(data)

    if credits is None:
        await message.answer(
            f"⚠️ Не удалось разобрать баланс.\n"
            f"Ответ API:\n<pre>{_html_escape(str(data)[:500])}</pre>",
            parse_mode="HTML",
        )
        return

    lines = [
        "💳 <b>Баланс RouterAI</b>",
        "",
        f"<b>{format_cost(credits)}</b>",
    ]

    # Активная сессия — сколько уже потрачено в ней
    active_id = tg_state.get_active_session(message.chat.id)
    if active_id:
        session = _Session.load(active_id)
        if session and session.total_usage.cost > 0:
            lines.append("")
            lines.append(
                f"<blockquote>"
                f"📊 Текущая сессия <code>{session.id}</code>\n"
                f"Потрачено: <b>{format_cost(session.total_usage.cost)}</b>"
                f"</blockquote>"
            )

    await message.answer("\n".join(lines), parse_mode="HTML")

# ---------- /delete ----------

@router.message(Command("delete"))
async def cmd_delete(message: Message) -> None:
    parts = (message.text or "").split(maxsplit=1)
    sid = parts[1].strip() if len(parts) > 1 else None
    if not sid:
        sid = tg_state.get_active_session(message.chat.id)
        if not sid:
            await message.answer("Нет активной сессии.")
            return

    session = Session.load(sid)
    if session is None:
        await message.answer(
            f"Сессия не найдена: <code>{_html_escape(sid)}</code>",
            parse_mode="HTML",
        )
        return

    await message.answer(
        f"🗑 <b>Удалить сессию?</b>\n\n"
        f"ID: <code>{session.id}</code>\n"
        f"Заголовок: <b>{_html_escape(session.title)}</b>\n"
        f"Запросов: {session.request_count}\n"
        f"Стоимость: {_fmt_cost(session.total_usage.cost)}",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[[
            InlineKeyboardButton(text="🗑 Удалить", callback_data=f"del:{session.id}"),
            InlineKeyboardButton(text="❌ Отмена", callback_data="del_cancel"),
        ]]),
        parse_mode="HTML",
    )


@router.callback_query(F.data.startswith("del:"))
async def cb_delete_confirm(query: CallbackQuery) -> None:
    sid = query.data.split(":", 1)[1]
    session = Session.load(sid)
    if session is None:
        await query.message.edit_text("Сессия уже удалена или не найдена.")
        await query.answer()
        return

    removed = session.delete()
    active_id = tg_state.get_active_session(query.message.chat.id)
    if active_id == sid:
        tg_state.clear_active_session(query.message.chat.id)

    projects = _list_projects()
    await query.message.edit_text(
        f"✅ <b>Сессия удалена</b>\n\n"
        f"ID: <code>{sid}</code>\n"
        f"Файлов удалено: <b>{removed}</b>\n\n"
        f"Выбери новый проект:",
        reply_markup=keyboards.projects_keyboard(projects),
        parse_mode="HTML",
    )
    await query.answer("🗑 Удалено")


@router.callback_query(F.data == "del_cancel")
async def cb_delete_cancel(query: CallbackQuery) -> None:
    await query.message.edit_text("❌ Удаление отменено.")
    await query.answer()




# ---------- История сессии ----------

@router.callback_query(F.data.startswith("history:"))
async def cb_history(query: CallbackQuery) -> None:

    sid = query.data.split(":", 1)[1]
    session = Session.load(sid)
    if session is None:
        await query.answer("Сессия не найдена", show_alert=True)
        return

    await _send_history_preview(query, session)


@router.message(Command("history"))
async def cmd_history(message: Message) -> None:
    """Показать историю активной сессии."""
    sid = tg_state.get_active_session(message.chat.id)
    if not sid:
        await message.answer("Нет активной сессии.")
        return
    session = Session.load(sid)
    if session is None:
        await message.answer("Сессия не найдена.")
        return
    await _send_history_preview(message, session)


async def _send_history_preview(source, session: Session) -> None:
    """Компактный вывод последних N сообщений + кнопка экспорта."""
    import html as _html

    def esc(s: str) -> str:
        return _html.escape(s, quote=False)

    # Последние 10 пар (user + assistant)
    messages = session.messages
    # Отфильтруем служебные (tool, system)
    pairs = []
    last_user = None
    for msg in messages:
        role = msg.get("role")
        content = (msg.get("content") or "").strip()
        if role == "system":
            continue
        if role == "user":
            last_user = content
        elif role == "assistant" and content:
            pairs.append((last_user or "(без запроса)", content))
            last_user = None

    if not pairs:
        text = f"📜 <b>История</b> · сессия <code>{session.id}</code>\n\n(пусто)"
    else:
        recent = pairs[-5:]
        lines = [
            f"📜 <b>История</b> · <code>{session.id}</code>",
            f"<i>Всего пар: {len(pairs)} · показаны последние {len(recent)}</i>",
            "",
        ]
        for user_msg, assistant_msg in recent:
            u = esc(user_msg[:120]) + ("…" if len(user_msg) > 120 else "")
            a = esc(assistant_msg[:200]) + ("…" if len(assistant_msg) > 200 else "")
            lines.append(f"👤 {u}")
            lines.append(f"🤖 {a}")
            lines.append("")
        text = "\n".join(lines)

    # Кнопки: экспорт + назад
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(
            text="📥 Скачать полную (.md)",
            callback_data=f"hist_md:{session.id}",
        )],
        [InlineKeyboardButton(
            text="⬅️ Назад",
            callback_data=f"open:{session.id}",
        )],
    ])

    if hasattr(source, "message"):  # callback
        await source.message.edit_text(text, parse_mode="HTML", reply_markup=kb)
        await source.answer()
    else:  # message
        await source.answer(text, parse_mode="HTML", reply_markup=kb)


@router.callback_query(F.data.startswith("hist_md:"))
async def cb_history_md(query: CallbackQuery) -> None:
    """Отправить полную историю как .md файл."""
    import time as _time

    from aiogram.types import FSInputFile

    sid = query.data.split(":", 1)[1]
    session = Session.load(sid)
    if session is None:
        await query.answer("Сессия не найдена", show_alert=True)
        return

    # Собираем markdown
    lines = [
        f"# История сессии {session.id}",
        "",
        f"- **Проект:** `{session.workspace}`",
        f"- **Модель:** `{session.model}`",
        f"- **Режим:** `{session.mode.value}`",
        f"- **Reasoning:** `{session.reasoning_effort}`",
        f"- **Запросов:** {session.request_count}",
        f"- **Стоимость:** {session.total_usage.cost:.4f} ₽",
        f"- **Создана:** {_time.strftime('%Y-%m-%d %H:%M', _time.localtime(session.created_at))}",
        "",
        "---",
        "",
    ]
    for msg in session.messages:
        role = msg.get("role")
        content = (msg.get("content") or "").strip()
        if not content:
            continue
        if role == "user":
            lines.append(f"## 👤 Пользователь\n\n{content}\n")
        elif role == "assistant":
            lines.append(f"## 🤖 Ассистент\n\n{content}\n")
        elif role == "tool":
            # Первые 200 символов каждого tool-результата
            brief = content[:200] + ("…" if len(content) > 200 else "")
            lines.append(f"<details><summary>🔧 tool</summary>\n\n```\n{brief}\n```\n</details>\n")
        elif role == "system":
            # Пропускаем (это огромный промпт)
            continue

    md_text = "\n".join(lines)

    # Сохраняем в sessions/history/
    hist_dir = config.SESSIONS_DIR / "history"
    hist_dir.mkdir(parents=True, exist_ok=True)
    ts = _time.strftime("%Y%m%d_%H%M%S")
    md_path = hist_dir / f"{session.id}_{ts}.md"
    md_path.write_text(md_text, encoding="utf-8")

    await query.message.answer_document(
        FSInputFile(str(md_path)),
        caption=f"📜 История сессии {session.id} · {len(md_text)} символов",
    )
    await query.answer("📥 Отправлено")


@router.message(Command("model"))
async def cmd_model(message: Message) -> None:
    """Показать меню выбора модели."""
    session = _get_active_session(message.chat.id)
    if not session:
        await message.answer("Нет активной сессии. /projects — выбрать проект.")
        return
    from agent_coder.models import get_model_name
    await message.answer(
        f"🧠 <b>Модель</b>\n\n"
        f"Текущая: <b>{get_model_name(session.model)}</b>\n"
        f"<code>{session.model}</code>",
        reply_markup=keyboards.models_keyboard(session.model),
        parse_mode="HTML",
    )