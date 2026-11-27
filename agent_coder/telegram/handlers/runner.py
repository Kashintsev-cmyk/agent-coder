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
from contextlib import suppress

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
            on_reasoning=self._on_reasoning,
            on_tool_call=self._on_tool_call,
            on_tool_result=self._on_tool_result,
            on_usage=self._on_usage,
            on_error=self._on_error,
            request_approval=self._request_approval,
            on_compression=self._on_compression,
        )

    async def _on_reasoning(self, text: str) -> None:
        """Рассуждения модели — свёрнутый блок перед текстом ответа."""
        block = renderer.render_reasoning(text)
        if block:
            self.buffer.append(block)
            await self._schedule_edit()

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

    async def _on_compression(self, comp) -> None:
        """Явный лог сжатия истории: пользователь видит, когда оно сработало."""
        self.buffer.append(renderer.render_compression(comp))
        self.last_edit = 0.0  # событие редкое — показываем сразу, без троттлинга
        await self.flush()

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
                    self.chat_id,
                    part,
                    parse_mode="HTML",
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
            self._rendered_extras = self._rendered_extras[: len(self.extra_msg_ids)]

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
        except TimeoutError:
            if self.approval_msg_id is not None:
                with suppress(Exception):
                    await self.bot.edit_message_text(
                        chat_id=self.chat_id,
                        message_id=self.approval_msg_id,
                        text="⌛ Время ожидания истекло",
                        reply_markup=None,
                        parse_mode="HTML",
                    )
            return False
        finally:
            _pending.pop(request_id, None)


def _join_buffer(buffer: list[str]) -> str:
    """Склеить буфер в один текст БЕЗ обрезки.

    Обрезка больше не нужна — длинный текст разбивается через
    _split_for_telegram и отправляется несколькими сообщениями.
    """
    return "\n".join(buffer)


# Теги, которые порождает рендерер и понимает Telegram. Разрыв тега пополам
# даёт TelegramBadRequest «Can't find end tag» → сообщение теряется целиком.
_HTML_TAG = re.compile(r"<[^>]+>")
_TAG_NAME = re.compile(r"</?\s*([a-zA-Z][a-zA-Z0-9]*)")
# Одиночные теги: их нельзя «переоткрывать» в следующей части.
_VOID_TAGS = frozenset({"br", "hr", "img"})


def _update_tag_stack(
    segment: str, stack: list[tuple[str, str]]
) -> list[tuple[str, str]]:
    """Обновить стек открытых тегов по сегменту.

    `stack` — `(имя, открывающий_тег)` в порядке открытия. На входе может быть
    непустым: вызывающий переносит открытые теги из предыдущей части, чтобы
    закрыть их в конце текущей.
    """
    for match in _HTML_TAG.finditer(segment):
        tag = match.group(0)
        name_match = _TAG_NAME.match(tag)
        if name_match is None:
            continue
        name = name_match.group(1).lower()
        if tag.startswith("</"):
            for idx in range(len(stack) - 1, -1, -1):
                if stack[idx][0] == name:
                    del stack[idx]
                    break
        elif not tag.endswith("/>") and name not in _VOID_TAGS:
            stack.append((name, tag))
    return stack


def _cut_pos(segment: str, budget: int) -> int:
    """Где резать: перенос строки в пределах бюджета, но не внутри тега.

    Разрез внутри `<...>` породит битый тег в обеих частях (`<code class="x"`
    без `>` — Telegram отвергнет сообщение). Поэтому отступаем к началу тега.

    Если тег начинается с нулевой позиции, отступать некуда: тогда отдаём её
    целиком, даже если она чуть больше бюджета — иначе получился бы пустой
    head и следующий чанк с обрубком тега.
    """
    if budget >= len(segment):
        return len(segment)
    cut = segment.rfind("\n", 0, budget + 1)
    if cut <= 0:
        cut = budget
    last_open = segment.rfind("<", 0, cut)
    if last_open != -1:
        close = segment.find(">", last_open)
        if close == -1 or close >= cut:
            if last_open > 0:
                return last_open
            if close != -1:
                return close + 1
            return min(budget, len(segment))
    return cut if cut > 0 else min(budget, len(segment))


def _split_for_telegram(text: str, limit: int = 3900) -> list[str]:
    """Разбить текст на части ≤ limit символов, не разрывая HTML-теги.

    Рвём по границам строк (длинную строку без пробелов — по символам).
    Часть закрывается всеми тегами, открытыми в ней, а следующая начинается
    с их повторного открытия: Telegram отклоняет сообщение с незакрытым
    тегом целиком, и `_render_parts` на такой ошибке откатывается в plain
    text — один разорванный <b> на границе убивал разметку всего ответа.

    Раньше здесь стоял `_fix_block_boundaries`, который склеивал части с
    незакрытым блок-тегом, зная только pre/code/blockquote. <b>, <i> и <a>
    он не видел, поэтому 'a'*3895 + '<b>жирный</b>' давал часть с <b> без
    закрытия (регресс-тест в tests/test_split_telegram.py).
    """
    if not text:
        return [""]
    if limit < 1:
        raise ValueError("limit должен быть >= 1")

    parts: list[str] = []
    rest = text
    carry: list[tuple[str, str]] = []
    while True:
        prefix = "".join(tag for _, tag in carry)
        budget = limit - len(prefix)
        if budget < 1:
            # Патологический случай: открывающие теги сами не влезают в лимит.
            # Отдаём остаток одним куском, чтобы не зациклиться.
            parts.append(prefix + rest)
            return parts
        cut = _cut_pos(rest, budget)
        head, tail = rest[:cut], rest[cut:]
        stack = _update_tag_stack(head, list(carry))
        closing = "".join(f"</{name}>" for name, _ in reversed(stack))
        # Закрывающие теги тоже занимают место — ужимаем, пока не влезет.
        #
        # ОБЯЗАТЕЛЬНОЕ условие прогресса: каждый шаг обязан уменьшать cut.
        # Без него был бесконечный цикл: в вырожденном случае (тег с нулевой
        # позиции) `_cut_pos` возвращал конец тега, то есть значение БОЛЬШЕ
        # запрошенного бюджета, и `cut - 1` давало ту же границу снова.
        while len(prefix) + len(head) + len(closing) > limit and cut > 1:
            shrunk = _cut_pos(rest, cut - 1)
            if shrunk >= cut:
                # Дальше ужимать некуда: единственный тег без закрытия не
                # делится. Отдаём как есть — Telegram либо примет, либо
                # сработает plain-text fallback в `_render_parts`.
                break
            cut = shrunk
            head, tail = rest[:cut], rest[cut:]
            stack = _update_tag_stack(head, list(carry))
            closing = "".join(f"</{name}>" for name, _ in reversed(stack))
        parts.append(prefix + head + closing)
        carry = stack
        rest = tail.lstrip("\n")
        if not rest:
            return parts
