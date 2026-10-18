"""Простые команды и callbacks UI: /start, /help, /projects, /sessions,
/current, /cost, /mode, /cancel, /new, /delete, /history, /model, /reasoning,
/balance.

Задачи и approval — в task.py / approval.py.
"""

from __future__ import annotations

import logging
from pathlib import Path

from aiogram import Bot, F
from aiogram.filters import Command, CommandStart
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)

from agent_coder.agent.prompts import Mode
from agent_coder.agent.session import Session, content_to_text
from agent_coder.config import config
from agent_coder.llm.client import LLMClient
from agent_coder.llm.types import format_cost
from agent_coder.telegram import keyboards
from agent_coder.telegram import state as tg_state
from agent_coder.telegram.renderer import html_escape

from .project_utils import (
    _PROJECTS_DEFAULT_DIR,
    _create_project_on_disk,
    _create_session,
    _validate_project_name,
)
from .shared import (
    _audit_reasoning_warning,
    _fmt_cost,
    _get_active_session,
    _list_projects,
    _running,
    _session_summary,
    _should_preserve_history,
    router,
)

logger = logging.getLogger(__name__)


# ---------- /start, /help ----------


@router.message(CommandStart())
async def cmd_start(message: Message) -> None:
    projects = _list_projects()
    text = (
        "👋 <b>agent-coder</b>\n\n"
        "AI-агент для работы с кодом на сервере.\n\n"
        "📝 <b>Задача:</b>\n"
        "  Отправь текст, файл (.txt/.md) или фото.\n"
        "  Сообщение с <code>?</code> в конце — быстрый вопрос без изменений.\n"
        "\n"
        "🚀 <b>Основное:</b>\n"
        "  /projects — список проектов\n"
        "  /new &lt;name&gt; — создать/открыть проект в <code>~/bots/</code>\n"
        "  /chat — обсуждение без проекта\n"
        "  /sessions — все сессии\n"
        "  /current — текущая сессия\n"
        "\n"
        "🐙 <b>GitHub:</b>\n"
        "  /clone user/repo — клонировать репо\n"
        "  /ghnew &lt;name&gt; — создать репо на GitHub + push\n"
        "\n"
        "⚙️ <b>Настройки сессии:</b>\n"
        "  /model — выбрать модель\n"
        "  /reasoning — уровень thinking\n"
        "  /mode plan | act | audit | chat — режим\n"
        "  /yolo [on|off] — без подтверждений (⚡ осторожно)\n"
        "\n"
        "💰 <b>Информация:</b>\n"
        "  /cost — стоимость сессии\n"
        "  /balance — баланс RouterAI\n"
        "  /server — диагностика сервера\n"
        "  /audit — аудит проекта\n"
        "  /history — история сессии\n"
        "  /delete — удалить сессию\n"
        "\n"
        "🛑 <b>Управление:</b>\n"
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


@router.message(Command("chat"))
async def cmd_chat(message: Message) -> None:
    """Открыть chat-сессию для обсуждений без проекта.

    Если уже есть активная chat-сессия — переключается на неё.
    Иначе создаёт новую в config.DEFAULT_WORKSPACE.
    """
    chat_id = message.chat.id
    current = _get_active_session(chat_id)

    # Уже в chat-режиме — просто напомнить
    if current is not None and current.mode == Mode.CHAT:
        await message.answer(
            f"💬 <b>Chat-сессия уже открыта</b>\n"
            f"   <code>{current.id}</code> · {current.workspace}\n\n"
            f"Просто пиши сообщение — отвечу без изменений в файлах.",
            reply_markup=keyboards.session_menu_keyboard(current),
            parse_mode="HTML",
        )
        return

    # Найти существующую chat-сессию в DEFAULT_WORKSPACE или создать
    workspace = config.DEFAULT_WORKSPACE
    if not Path(workspace).is_dir():
        await message.answer(
            f"❌ Рабочая директория не найдена: <code>{html_escape(workspace)}</code>",
            parse_mode="HTML",
        )
        return

    # Ищем среди всех сессий — активную chat-сессию в этой workspace
    for s in Session.list_all():
        if s.mode == Mode.CHAT and s.workspace == workspace:
            tg_state.set_active_session(chat_id, s.id)
            # Перезагружаем полную сессию (list_all возвращает лёгкую)
            full = Session.load(s.id) or s
            await message.answer(
                f"💬 <b>Chat-сессия восстановлена</b>\n"
                f"   <code>{full.id}</code> · {full.title}\n\n"
                f"Просто пиши сообщение.",
                reply_markup=keyboards.session_menu_keyboard(full),
                parse_mode="HTML",
            )
            return

    # Создаём новую
    await _create_session(
        chat_id,
        message,
        workspace,
        mode=Mode.CHAT,
        title="Обсуждение",
    )


@router.message(Command("new"))
async def cmd_new(message: Message) -> None:
    """Создать или открыть проект.

    Формат:
      /new <name>     → ~/bots/<name> (создаст, если нет)
      /new /abs/path  → открыть существующий по пути
    """
    parts = (message.text or "").split(maxsplit=1)
    if len(parts) < 2:
        await message.answer(
            "Укажи имя проекта: <code>/new weather-bot</code>\n\n"
            f"Проект создастся в <code>{_PROJECTS_DEFAULT_DIR}/</code>\n"
            "Стандарт: scaffold (pyproject · AGENTS.md · CI · tests · git init).\n\n"
            "Или полный путь, если проект уже есть:\n"
            "<code>/new /home/botuser/projects/existing</code>",
            parse_mode="HTML",
        )
        return

    arg = parts[1].strip()

    if arg.startswith(("/", "./", "../")):
        path = Path(arg).resolve()
        if path.is_dir():
            await _create_session(message.chat.id, message, str(path))
            return
        await message.answer(
            f"❌ Директория не найдена: <code>{html_escape(str(path))}</code>\n"
            f"По явным путям проект не создаётся. Используй короткое имя:\n"
            f"<code>/new my-project</code>",
            parse_mode="HTML",
        )
        return

    err = _validate_project_name(arg)
    if err:
        await message.answer(
            f"❌ Имя проекта: {html_escape(err)}",
            parse_mode="HTML",
        )
        return

    target = (_PROJECTS_DEFAULT_DIR / arg).resolve()
    if target.is_dir():
        await _create_session(message.chat.id, message, str(target))
        return

    await message.answer(
        f"📁 Проекта <b>{html_escape(arg)}</b> ещё нет.\n"
        f"Создать <code>{html_escape(str(target))}</code>?\n\n"
        f"<i>Будет создан: pyproject.toml · AGENTS.md · CI · tests · git init.</i>",
        reply_markup=keyboards.confirm_create_project_keyboard(arg),
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
        reply_markup=keyboards.session_menu_keyboard(session),
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
    valid = {
        "plan": Mode.PLAN,
        "act": Mode.ACT,
        "audit": Mode.AUDIT,
        "chat": Mode.CHAT,
    }
    if len(parts) < 2 or parts[1].strip() not in valid:
        await message.answer(
            f"Текущий режим: <b>{session.mode.value}</b>\n\n"
            f"Сменить: /mode plan | /mode act | /mode audit | /mode chat\n\n"
            f"• <b>plan</b> — планирование (только чтение)\n"
            f"• <b>act</b> — работа с кодом (правки, команды)\n"
            f"• <b>audit</b> — строгий аудит (только отчёт)\n"
            f"• <b>chat</b> — обсуждение без правок (read-only tools)",
            parse_mode="HTML",
        )
        return
    new_mode = valid[parts[1].strip()]
    if session.mode != new_mode:
        old_mode = session.mode
        session.mode = new_mode
        if not _should_preserve_history(old_mode, new_mode):
            session.messages = []
        session.save()
    extra = _audit_reasoning_warning(session) if new_mode == Mode.AUDIT else ""
    await message.answer(
        f"Режим: <b>{new_mode.value}</b>{extra}",
        parse_mode="HTML",
    )


# ---------- /cancel ----------


@router.message(Command("yolo"))
async def cmd_yolo(message: Message) -> None:
    """Переключить режим без подтверждений (per-session).

    /yolo         — toggle
    /yolo on|off  — явно
    """
    session = _get_active_session(message.chat.id)
    if session is None:
        await message.answer("Нет активной сессии. /projects — выбрать проект.")
        return

    parts = (message.text or "").split(maxsplit=1)
    if len(parts) >= 2:
        val = parts[1].strip().lower()
        if val not in ("on", "off"):
            await message.answer(
                "Использование: <code>/yolo</code> (toggle) или "
                "<code>/yolo on|off</code>",
                parse_mode="HTML",
            )
            return
        session.yolo = val == "on"
    else:
        session.yolo = not session.yolo

    session.save()

    if session.yolo:
        await message.answer(
            "⚡ <b>YOLO включён</b>\n\n"
            "Опасные операции (run_command с rm/kill/sudo/...) больше не "
            "запрашивают подтверждение. Работает только в этой сессии.\n\n"
            "Отключить: <code>/yolo off</code>",
            reply_markup=keyboards.session_menu_keyboard(session),
            parse_mode="HTML",
        )
    else:
        await message.answer(
            "🔒 <b>YOLO выключен</b>\n\n"
            "Опасные операции снова запрашивают подтверждение.",
            reply_markup=keyboards.session_menu_keyboard(session),
            parse_mode="HTML",
        )


@router.message(Command("cancel"))
async def cmd_cancel(message: Message) -> None:
    task = _running.get(message.chat.id)
    if task and not task.done():
        task.cancel()
        await message.answer("⏹ Задача отменена.")
    else:
        await message.answer("Нечего отменять.")


# ---------- Callback: выбор проекта / сессии / режима ----------


@router.callback_query(F.data.startswith("pc:yes:"))
async def cb_project_create_yes(query: CallbackQuery, bot: Bot) -> None:
    """Подтверждение создания проекта."""
    name = query.data.split(":", 2)[2]
    err = _validate_project_name(name)
    if err:
        await query.answer(f"Некорректное имя: {err}", show_alert=True)
        return

    await query.message.edit_text(
        f"⏺ Создаю <b>{html_escape(name)}</b>…",
        parse_mode="HTML",
    )
    try:
        path = await _create_project_on_disk(name)
    except Exception as e:
        logger.exception("create project failed")
        await query.message.edit_text(
            f"❌ Не удалось создать: <code>{html_escape(str(e))}</code>",
            parse_mode="HTML",
        )
        await query.answer("Ошибка")
        return

    # Если GITHUB_TOKEN настроен — предложить создать репо на GitHub
    if config.GITHUB_TOKEN:
        await query.message.edit_text(
            f"✅ Проект создан: <code>{html_escape(str(path))}</code>\n"
            f"<i>pyproject.toml · AGENTS.md · CI · tests · git init</i>\n\n"
            f"🐙 Создать репозиторий на GitHub?",
            reply_markup=keyboards.gh_offer_keyboard(name),
            parse_mode="HTML",
        )
        await query.answer("Проект создан")
        return

    await query.message.edit_text(
        f"✅ Проект создан: <code>{html_escape(str(path))}</code>\n"
        f"<i>pyproject.toml · AGENTS.md · CI · tests · git init</i>",
        parse_mode="HTML",
    )
    await query.answer("Проект создан")
    await _create_session(query.message.chat.id, query.message, str(path))


@router.callback_query(F.data == "yolo_toggle")
async def cb_yolo_toggle(query: CallbackQuery) -> None:
    """Toggle YOLO-режима из меню сессии."""
    session = _get_active_session(query.message.chat.id)
    if session is None:
        await query.answer("Нет сессии", show_alert=True)
        return
    session.yolo = not session.yolo
    session.save()
    try:
        await query.message.edit_reply_markup(
            reply_markup=keyboards.session_menu_keyboard(session),
        )
    except Exception:
        pass
    await query.answer("YOLO: " + ("вкл ⚡" if session.yolo else "выкл 🔒"))


@router.callback_query(F.data == "pc:no")
async def cb_project_create_no(query: CallbackQuery) -> None:
    await query.message.edit_text("❌ Создание отменено.")
    await query.answer()


@router.callback_query(F.data == "new_project")
async def cb_new_project_hint(query: CallbackQuery) -> None:
    """Кнопка ➕ Новый проект — подсказка по команде."""
    await query.message.edit_text(
        "➕ <b>Новый проект</b>\n\n"
        "Отправь: <code>/new my-project</code>\n\n"
        f"• Имя = папка в <code>{_PROJECTS_DEFAULT_DIR}/</code>\n"
        "• Создаётся git-репозиторий с README.md и .gitignore\n"
        "• Если проект уже существует — просто откроется сессия",
        parse_mode="HTML",
    )
    await query.answer()


@router.callback_query(F.data.startswith("new:"))
async def cb_new(query: CallbackQuery, bot: Bot) -> None:
    raw = query.data.split(":", 1)[1]
    try:
        idx = int(raw)
    except ValueError:
        await query.answer(
            "Некорректная кнопка, обнови список: /projects", show_alert=True
        )
        return

    projects = _list_projects()
    if idx < 0 or idx >= len(projects):
        await query.answer("Проект больше недоступен, обнови список.", show_alert=True)
        return

    path = projects[idx]
    if not path.is_dir():
        await query.answer("Проект больше недоступен, обнови список.", show_alert=True)
        return

    await query.message.edit_reply_markup(reply_markup=None)
    await _create_session(query.message.chat.id, query.message, str(path))
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
        reply_markup=keyboards.session_menu_keyboard(session),
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
    mode_map = {
        "plan": Mode.PLAN,
        "act": Mode.ACT,
        "audit": Mode.AUDIT,
        "chat": Mode.CHAT,
    }
    new_mode = mode_map.get(mode_str)
    if new_mode is None:
        await query.answer("Неизвестный режим", show_alert=True)
        return
    if session.mode != new_mode:
        old_mode = session.mode
        session.mode = new_mode
        if not _should_preserve_history(old_mode, new_mode):
            session.messages = []
        session.save()
    extra = _audit_reasoning_warning(session) if new_mode == Mode.AUDIT else ""
    await query.message.edit_text(
        f"Режим: <b>{new_mode.value}</b>{extra}\n{_session_summary(session)}",
        reply_markup=keyboards.session_menu_keyboard(session),
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


# ---------- Команды /model и /reasoning ----------


@router.message(Command("reasoning"))
async def cmd_reasoning(message: Message) -> None:
    session = _get_active_session(message.chat.id)
    if not session:
        await message.answer("Нет активной сессии.")
        return
    from agent_coder.model_catalog import catalog

    m = catalog.get(session.model)
    if not m or not m.reasoning:
        name = m.name if m else session.model
        await message.answer(f"Модель {name} не поддерживает reasoning.")
        return
    await message.answer(
        f"💭 <b>Reasoning</b>\n\nТекущий: <b>{session.reasoning_effort}</b>",
        reply_markup=keyboards.reasoning_keyboard(session.reasoning_effort),
        parse_mode="HTML",
    )


@router.message(Command("balance"))
async def cmd_balance(message: Message) -> None:
    """Показать баланс routerai."""
    from agent_coder.agent.session import Session as _Session

    client = LLMClient()
    await client.start()
    try:
        data = await client.get_balance()
    except Exception as e:
        await message.answer(
            f"❌ Не удалось получить баланс: <code>{html_escape(str(e))}</code>",
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
            f"Ответ API:\n<pre>{html_escape(str(data)[:500])}</pre>",
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
            f"Сессия не найдена: <code>{html_escape(sid)}</code>",
            parse_mode="HTML",
        )
        return

    await message.answer(
        f"🗑 <b>Удалить сессию?</b>\n\n"
        f"ID: <code>{session.id}</code>\n"
        f"Заголовок: <b>{html_escape(session.title)}</b>\n"
        f"Запросов: {session.request_count}\n"
        f"Стоимость: {_fmt_cost(session.total_usage.cost)}",
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="🗑 Удалить", callback_data=f"del:{session.id}"
                    ),
                    InlineKeyboardButton(text="❌ Отмена", callback_data="del_cancel"),
                ]
            ]
        ),
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
        content = content_to_text(msg.get("content")).strip()
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
    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="📥 Скачать полную (.md)",
                    callback_data=f"hist_md:{session.id}",
                )
            ],
            [
                InlineKeyboardButton(
                    text="⬅️ Назад",
                    callback_data=f"open:{session.id}",
                )
            ],
        ]
    )

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
        content = content_to_text(msg.get("content")).strip()
        if not content:
            continue
        if role == "user":
            lines.append(f"## 👤 Пользователь\n\n{content}\n")
        elif role == "assistant":
            lines.append(f"## 🤖 Ассистент\n\n{content}\n")
        elif role == "tool":
            # Первые 200 символов каждого tool-результата
            brief = content[:200] + ("…" if len(content) > 200 else "")
            lines.append(
                f"<details><summary>🔧 tool</summary>\n\n```\n{brief}\n```\n</details>\n"
            )
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
        f"<code>{session.model}</code>\n\n"
        f"<i>Выбор модели вручную — приоритет над автороутером.</i>",
        reply_markup=keyboards.models_keyboard(session.model),
        parse_mode="HTML",
    )
