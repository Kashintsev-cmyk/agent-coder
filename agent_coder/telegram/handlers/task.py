"""Обработчики задач агента: текст, файл, фото, вопрос.

Также содержит запуск задачи (_run_task) и генерацию audit-отчёта.
"""

from __future__ import annotations

import asyncio
import logging
from contextlib import suppress
from dataclasses import dataclass, field
from pathlib import Path

from aiogram import Bot, F
from aiogram.types import CallbackQuery, Message

from agent_coder.agent.loop import AgentLoop
from agent_coder.agent.prompts import Mode
from agent_coder.agent.session import Session
from agent_coder.config import config
from agent_coder.llm.client import LLMClient
from agent_coder.telegram import keyboards
from agent_coder.telegram.renderer import html_escape
from agent_coder.tools import build_registry

from .runner import _TaskRunner
from .shared import (
    _get_active_session,
    _running,
    _shutting_down,
    router,
)

logger = logging.getLogger(__name__)


# ---------- Основной обработчик задачи ----------

_PHOTO_MAX_BYTES = 5 * 1024 * 1024


# ---------- Пакетная обработка (альбомы) ----------
#
# Telegram присылает альбом (media_group) как НЕСКОЛЬКО отдельных Message с
# общим `media_group_id` — по одному на элемент. Без склейки первое фото
# стартовало задачу, а остальные получали «Другая задача уже выполняется»:
# юзер отправлял 5 скриншотов и модель видела один.
#
# Склейка: копим элементы по media_group_id и запускаем ОДНУ задачу через
# _ALBUM_DEBOUNCE_SEC после последнего пришедшего. Задержка нужна потому, что
# Telegram не сообщает «альбом закончился» — только отдельные сообщения.
_ALBUM_DEBOUNCE_SEC = 1.2
# Больше в один запрос не влезет по контексту: 10 изображений ≈ 10 × ~1.5K
# токенов только на картинки, плюс история сессии.
_ALBUM_MAX_ITEMS = 10


@dataclass
class _AlbumBuffer:
    """Накопитель элементов одного альбома.

    Изображения и текстовые документы копятся раздельно, но уезжают в одной
    задаче: юзер прислал пачку скриншотов/логов — это одна проблема, а не N.
    """

    chat_id: int
    session_id: str
    items: list[Path] = field(default_factory=list)  # изображения (пути)
    texts: list[tuple[str, str]] = field(default_factory=list)  # (имя, содержимое)
    caption: str = ""
    timer: asyncio.Task | None = None

    @property
    def total(self) -> int:
        return len(self.items) + len(self.texts)


_albums: dict[str, _AlbumBuffer] = {}


def _album_key(message: Message) -> str | None:
    """Ключ альбома: ``chat_id:media_group_id``. None — не альбом."""
    gid = getattr(message, "media_group_id", None)
    if not gid:
        return None
    return f"{message.chat.id}:{gid}"


def _album_task_text(buf: _AlbumBuffer) -> str:
    """Текст задачи для альбома: подпись юзера + все текстовые файлы."""
    parts: list[str] = []
    if buf.caption.strip():
        parts.append(buf.caption.strip())
    if buf.texts:
        # Заголовок на каждый файл: модель должна понимать, где чей кусок.
        for name, content in buf.texts:
            parts.append(f"=== {name} ===\n{content}")
    if parts:
        return "\n\n".join(parts)
    n = len(buf.items)
    return (
        f"Посмотри на {n} изображений и учти их все в задаче. "
        f"Если они относятся к одной проблеме — опиши общее и различия."
    )


def _album_source_note(buf: _AlbumBuffer) -> str:
    """Короткое описание вложения для статус-сообщения."""
    bits: list[str] = []
    if buf.items:
        bits.append(f"{len(buf.items)} изображений")
    if buf.texts:
        bits.append(f"{len(buf.texts)} файлов")
    return " + ".join(bits) if bits else "альбом"


async def _flush_album(key: str, bot: Bot, message: Message) -> None:
    """Запустить задачу по накопленному альбому и очистить буфер."""
    buf = _albums.pop(key, None)
    if buf is None or buf.total == 0:
        return
    session = _get_active_session(buf.chat_id)
    if session is None or session.id != buf.session_id:
        # Сессию успели сменить/закрыть, пока копился альбом.
        return
    await _start_task(
        bot,
        message,
        _album_task_text(buf),
        source_note=_album_source_note(buf),
        images=list(buf.items) or None,
    )


async def _collect_album_item(
    key: str,
    message: Message,
    bot: Bot,
    session: Session,
    path: Path,
    *,
    text: tuple[str, str] | None = None,
) -> bool:
    """Добавить элемент в альбом и (пере)запустить дебаунс.

    ``path`` — сохранённое изображение (для фото). Для документов передаётся
    ``text=(имя, содержимое)``, а ``path`` игнорируется.

    Возвращает True, если элемент ушёл в альбом (задачу запустит таймер),
    False — альбом переполнен, элемент отброшен.
    """
    buf = _albums.get(key)
    if buf is None:
        buf = _AlbumBuffer(chat_id=message.chat.id, session_id=session.id)
        _albums[key] = buf
    if buf.total >= _ALBUM_MAX_ITEMS:
        return False
    if text is not None:
        buf.texts.append(text)
    else:
        buf.items.append(path)
    caption = (message.caption or "").strip()
    if caption and not buf.caption:
        buf.caption = caption
    if buf.timer is not None:
        buf.timer.cancel()

    async def _debounced() -> None:
        # Отмена таймера (пришёл следующий элемент альбома) — штатный путь:
        # CancelledError пробрасывается из sleep, и flush не выполняется.
        await asyncio.sleep(_ALBUM_DEBOUNCE_SEC)
        try:
            await _flush_album(key, bot, message)
        except Exception:
            # Фоновая задача: исключение здесь никто не увидит (task-объект
            # теряется), а юзер остался бы без ответа — логируем.
            logger.exception("album flush failed (key=%s)", key)

    buf.timer = asyncio.create_task(_debounced())
    return True


@router.message(F.photo)
async def handle_task_photo(message: Message, bot: Bot) -> None:
    """Приём фото: одиночное — сразу задача, альбом — склейка."""
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

    key = _album_key(message)
    if key is not None:
        if await _collect_album_item(key, message, bot, session, path):
            return  # запустит дебаунс-таймер
        await message.answer(
            f"⚠️ В альбоме больше {_ALBUM_MAX_ITEMS} файлов — "
            f"лишние пропущены. Отправь остальные отдельным сообщением."
        )
        return

    caption = (message.caption or "").strip()
    task_text = caption or "Посмотри на изображение и учти его в задаче."

    await _start_task(
        bot,
        message,
        task_text,
        source_note=f"фото <code>{path.name}</code>",
        images=[path],
    )


@router.callback_query(F.data.startswith("continue_task:"))
async def cb_continue_task(query: CallbackQuery, bot: Bot) -> None:
    """Кнопка «▶️ Продолжить» после достижения лимита итераций."""
    from agent_coder.telegram import state as tg_state

    sid = query.data.split(":", 1)[1]
    session = Session.load(sid)
    if session is None:
        await query.answer("Сессия не найдена", show_alert=True)
        return

    chat_id = query.message.chat.id
    if _running.get(chat_id) and not _running[chat_id].done():
        await query.answer("Другая задача уже выполняется", show_alert=True)
        return

    tg_state.set_active_session(chat_id, sid)

    # Снимаем кнопки, чтобы не нажали дважды
    with suppress(Exception):
        await query.message.edit_reply_markup(reply_markup=None)
    await query.answer("Продолжаю…")

    # Запускаем «продолжи» как обычную задачу
    task = asyncio.create_task(
        _run_task(bot, chat_id, session, "продолжи", images=None)
    )
    _running[chat_id] = task


@router.message(F.text & ~F.text.startswith("/"))
async def handle_task(message: Message, bot: Bot) -> None:
    await _start_task(bot, message, message.text or "")


# Лимит размера файла-промта (байт). Telegram Bot API и сам отдаёт файлы
# до 20 МБ, но промт такого размера бессмыслен — ограничиваемся 1 МБ.
_PROMPT_FILE_MAX_BYTES = 1024 * 1024

_PROMPT_FILE_EXT = {
    ".txt",
    ".md",
    ".markdown",
    ".rst",
    ".csv",
    ".json",
    ".yaml",
    ".yml",
    ".py",
    ".js",
    ".ts",
    ".sh",
}


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

    session = _get_active_session(message.chat.id)
    if session is None:
        await message.answer("Сначала выбери проект: /projects")
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

    # Альбом документов: копим и запускаем ОДНУ задачу со всеми файлами —
    # юзер прислал пачку логов/конфигов, это одна проблема, а не N задач.
    album_key = _album_key(message)
    if album_key is not None:
        if await _collect_album_item(
            album_key, message, bot, session, Path(), text=(name, content)
        ):
            return  # задачу запустит дебаунс-таймер
        await message.answer(
            f"⚠️ В альбоме больше {_ALBUM_MAX_ITEMS} файлов — "
            f"лишние пропущены. Отправь остальные отдельным сообщением."
        )
        return

    task_text = f"{caption}\n\n{content}" if caption else content

    from agent_coder.agent.compression import would_exceed_soft_limit

    if would_exceed_soft_limit(session, task_text):
        await message.answer(
            "⚠️ Документ вместе с историей сессии превышает безопасный порог "
            "контекста для выбранной модели. Раздели файл на несколько частей "
            "или сократи/суммируй его и отправь нужный фрагмент. Автоматическая "
            "суммаризация файла не запускается."
        )
        return

    await _start_task(
        bot, message, task_text, source_note=f"файл <code>{html_escape(name)}</code>"
    )


async def _start_task(
    bot: Bot,
    message: Message,
    task_text: str,
    source_note: str = "",
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
        await message.answer("⚠️ Другая задача уже выполняется. /cancel чтобы прервать.")
        return

    if source_note:
        await message.answer(
            f"📄 Принят {source_note} · {len(task_text)} символов",
            parse_mode="HTML",
        )

    task = asyncio.create_task(
        _run_task(bot, chat_id, session, task_text, images=images)
    )
    _running[chat_id] = task


# ---------- Запуск задачи ----------


async def _run_task(
    bot: Bot,
    chat_id: int,
    session: Session,
    task_text: str,
    images: list[Path] | None = None,
) -> None:
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
            yolo=session.yolo,
        )

        # Авто-продолжение: если агент упёрся в лимит итераций, но задача
        # не завершена — сами шлём «продолжи» до MAX_AUTO_CONTINUE раз.
        # После — показываем кнопку «▶️ Продолжить».
        auto_continue = 0
        current_task = task_text
        current_images = images

        while True:
            result = await loop.run(current_task, images=current_images)
            session.save()
            await runner.flush()

            # Watchdog уже сообщил через events.on_error — выходим
            if result.watchdog_triggered:
                return

            # Провайдер недоступен / бюджет исчерпан / нет прогресса —
            # events.on_error уже отправил текст, автопродолжение НЕ делаем
            # (иначе снова жжём токены на заведомо проблемной задаче).
            if result.provider_error or result.cost_limit_reached or result.no_progress:
                return

            # Задача завершена нормально
            if not result.limit_reached:
                if session.mode == Mode.AUDIT:
                    await _generate_and_send_audit(bot, chat_id, session)
                return

            # Лимит достигнут, но автопродолжений ещё есть
            if auto_continue < config.MAX_AUTO_CONTINUE:
                auto_continue += 1
                logger.info(
                    "auto-continue %d/%d для сессии %s (итераций: %d)",
                    auto_continue,
                    config.MAX_AUTO_CONTINUE,
                    session.id,
                    result.iterations_used,
                )
                current_task = "продолжи"
                current_images = None  # фото только на первом круге
                continue

            # Автопродолжения закончились — даём кнопку пользователю
            try:
                await bot.send_message(
                    chat_id,
                    f"⏸ Лимит итераций ({result.iterations_used}). "
                    f"Автопродолжений: {auto_continue}/{config.MAX_AUTO_CONTINUE}.\n\n"
                    f"Задача не завершена — нажми, чтобы продолжить.",
                    reply_markup=keyboards.continue_keyboard(session.id),
                )
            except Exception as e:
                logger.warning("continue message failed: %s", e)
            return

    except asyncio.CancelledError:
        if not _shutting_down:
            with suppress(Exception):
                await bot.send_message(chat_id, "⏹ Отменено")
        raise
    except Exception as e:
        logger.exception("task failed")
        with suppress(Exception):
            await bot.send_message(chat_id, f"❌ Ошибка: {e}")
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
