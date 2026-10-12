"""Раннер задачи: сбор событий агента и обновление одного сообщения.

Содержит _TaskRunner (буферизация вывода с троттлингом), split/join
хелперы для длинных сообщений.
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
import time
import uuid

from aiogram import Bot
from aiogram.exceptions import TelegramBadRequest

from agent_coder.agent.loop import LoopEvents
from agent_coder.config import config
from agent_coder.telegram import keyboards, renderer
from agent_coder.telegram.renderer import html_escape

from .shared import _pending

logger = logging.getLogger(__name__)


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
        # Кэш реально отрисованного: сравнение → skip edit, если не менялось.
        self._rendered_main: str = ""
        self._rendered_extras: list[str] = []

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
        self.buffer.append(f"✗ <b>{html_escape(msg)}</b>")
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

        Оптимизация: сравниваем каждую часть с тем, что уже реально
        отправлено (_rendered_main / _rendered_extras) и пропускаем edit,
        если не менялось. На длинной задаче это даёт O(1) сетевых вызовов
        на flush вместо O(N) частей.

        Продолжения переиспользуются (edit) и удаляются при сокращении
        текста, чтобы не копить в чате устаревшие «хвосты».
        """
        # --- Основное сообщение (main) ---
        main_text = parts[0]
        if len(parts) > 1:
            main_text += "\n\n<i>(продолжение ниже)</i>"

        if main_text != self._rendered_main:
            try:
                await self.bot.edit_message_text(
                    chat_id=self.chat_id,
                    message_id=self.message_id,
                    text=main_text,
                    parse_mode="HTML",
                )
            except TelegramBadRequest as e:
                if "message is not modified" not in str(e).lower():
                    raise
            self._rendered_main = main_text

        # --- Продолжения (extras) ---
        wanted = parts[1:]
        for i, part in enumerate(wanted):
            already_sent = (
                i < len(self.extra_msg_ids)
                and i < len(self._rendered_extras)
                and self._rendered_extras[i] == part
            )
            if already_sent:
                continue

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

            # Синхронизировать кэш
            while len(self._rendered_extras) <= i:
                self._rendered_extras.append("")
            self._rendered_extras[i] = part

        # --- Удалить лишние хвосты, если текст сократился ---
        while len(self.extra_msg_ids) > len(wanted):
            mid = self.extra_msg_ids.pop()
            try:
                await self.bot.delete_message(self.chat_id, mid)
            except Exception as e:
                logger.warning("delete continuation failed: %s", e)
        # синхронизировать кэш extras с текущим числом сообщений
        if len(self._rendered_extras) > len(self.extra_msg_ids):
            self._rendered_extras = self._rendered_extras[:len(self.extra_msg_ids)]


    async def _request_approval(self, tc) -> bool:
        request_id = uuid.uuid4().hex[:8]
        future: asyncio.Future[bool] = asyncio.get_running_loop().create_future()
        _pending[request_id] = future

        args_text = json.dumps(tc.arguments, ensure_ascii=False, indent=2)
        if len(args_text) > 800:
            args_text = args_text[:800] + "…"

        text = (
            f"⚠️ <b>Подтверждение</b>\n\n"
            f"<code>{html_escape(tc.name)}</code>\n"
            f"<pre>{html_escape(args_text)}</pre>"
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


# Теги, внутри которых Telegram HTML «строгий»: разрыв пополам →
# TelegramBadRequest «Can't find end tag» → сообщение теряется.
_BLOCK_TAGS = ("pre", "code", "blockquote")

# Открывающий/закрывающий тег этих блоков (без учёта атрибутов —
# у нас они всё равно не используются).
def _open_block_tag(text: str) -> str | None:
    """Имя незакрытого блочного тега (самого внутреннего) или None.

    Счёт по стеку: открыли <pre>, встретили </pre> — закрыли.
    Возвращает имя тега, если в конце текста осталась открытая пара.
    """
    stack: list[str] = []
    pattern = rf"<(/?)({'|'.join(_BLOCK_TAGS)})>"
    for m in re.finditer(pattern, text):
        closing, name = m.group(1), m.group(2)
        if closing:
            if stack and stack[-1] == name:
                stack.pop()
        else:
            stack.append(name)
    return stack[-1] if stack else None


def _split_for_telegram(text: str, limit: int = 3900) -> list[str]:
    """Разбить текст на части ≤ limit символов, не разрывая блочные теги.

    Рвём по границам строк. Если очередная часть обрывается внутри
    <pre>/<code>/<blockquote>, сдвигаем точку реза до открывающего тега.
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
        # Очень длинную строку (например, base64) режем вынужденно.
        # Если внутри блочного тега — не режем, шлём как есть (Telegram
        # словит по общему лимиту 4096, но лучше так, чем рвать тег).
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

        # Если после добавления строки в current остался открытый блок-
        # тег и следующие строки могут его закрыть — не флашим сейчас,
        # но на границе текущего чака проверим.
        candidate = "\n".join(current)
        if current_len >= limit * 0.9 and len(candidate) >= limit:
            _flush()

    _flush()

    # Пост-обработка: если часть обрывается внутри блочного тега —
    # сдвигаем границу. Делаем это итеративно, чтобы не рвать стыки.
    result = _fix_block_boundaries(parts, limit)
    return result


def _fix_block_boundaries(parts: list[str], limit: int) -> list[str]:
    """Склеить части, которые обрывают блочный тег, и снова порезать.

    Простой подход: если в части открыт блок-тег, склеиваем её со следующей
    и пробуем порезать ещё раз с меньшим limit'ом — на границе </...>.
    Если и это не помогает — оставляем как есть (Telegram отклонит, будет
    fallback на plain).
    """
    if len(parts) <= 1:
        return parts

    out: list[str] = []
    buf = parts[0]
    i = 1
    while i < len(parts):
        if _open_block_tag(buf) is not None:
            # незакрытый блок → приклеиваем следующую часть
            buf = buf + "\n" + parts[i]
            i += 1
            if len(buf) > 4096:
                # вынужденно отправляем как есть — не можем закрыть тег
                out.append(buf)
                buf = ""
                # продолжим с этого места
                if i < len(parts):
                    buf = parts[i]
                    i += 1
            continue
        out.append(buf)
        buf = parts[i]
        i += 1
    if buf:
        out.append(buf)
    return [p for p in out if p]
