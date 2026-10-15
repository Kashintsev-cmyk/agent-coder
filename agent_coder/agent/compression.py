"""Сжатие истории сессии через LLM.

Вызывается из loop.py, когда input_tokens превысил soft-limit
(min(model.context * 0.6, config.MAX_HISTORY_TOKENS)). Старые сообщения
заменяются на одно резюме от config.SUMMARY_MODEL.
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import Any

from agent_coder.agent.session import content_to_text
from agent_coder.config import config

logger = logging.getLogger(__name__)

MIN_RECENT = 6   # сколько последних сообщений оставить как есть
MIN_TOTAL = 10   # минимум сообщений для запуска сжатия


@dataclass
class CompressionResult:
    """Итог сжатия — для лога в UI.

    tokens_before/tokens_after — оценка (см. estimate_tokens): точной
    токенизации у нас нет, а после сжатия реальный usage ещё не измерен
    (следующий запрос к LLM только предстоит). Для сообщения в Telegram
    такой точности достаточно.
    """
    messages_before: int
    messages_after: int
    tokens_before: int
    tokens_after: int
    summary_count: int


def estimate_tokens(messages: list[dict[str, Any]]) -> int:
    """Грубая оценка токенов по длине текста (≈4 символа на токен).

    Точного tokenizer'а нет (модели разные, тащить его ради строки в
    логе не стоит). Оценка нужна только для информационного сообщения,
    не для принятия решений о сжатии.
    """
    chars = 0
    for m in messages:
        chars += len(content_to_text(m.get("content")))
        for tc in (m.get("tool_calls") or []):
            chars += len(json.dumps(tc, ensure_ascii=False))
    return max(0, round(chars / 4))


def soft_limit(session) -> int:
    """Порог input-токенов: min(model.context * 0.6, MAX_HISTORY_TOKENS)."""
    from agent_coder.model_catalog import catalog

    m = catalog.get(session.model)
    if m and m.context:
        return min(int(m.context * 0.6), config.MAX_HISTORY_TOKENS)
    return config.MAX_HISTORY_TOKENS


def find_cut(messages: list[dict[str, Any]], min_recent: int = MIN_RECENT) -> int | None:
    """Найти индекс начала «recent»-блока.

    Безопасная точка разреза — сообщение с role 'user' или 'assistant'
    (не 'tool', т.к. tool ссылается на предыдущий assistant.tool_calls).
    Возвращает None, если безопасной точки нет.
    """
    total = len(messages)
    if total < MIN_TOTAL:
        return None
    for i in range(total - min_recent, 0, -1):
        role = messages[i].get("role")
        if role in ("user", "assistant"):
            return i
    return None


def format_for_summary(messages: list[dict[str, Any]]) -> str:
    """Плоский транскрипт для суммаризации. Обрезает длинные куски."""
    parts: list[str] = []
    for m in messages:
        role = m.get("role")
        content = content_to_text(m.get("content")).strip()
        if role == "user":
            parts.append(f"ПОЛЬЗОВАТЕЛЬ:\n{content[:3000]}")
        elif role == "assistant":
            tcs = m.get("tool_calls") or []
            text = content[:2000]
            if tcs:
                names = ", ".join(
                    (tc.get("function") or {}).get("name", "?")
                    for tc in tcs
                )
                parts.append(f"АССИСТЕНТ: {text}\n[вызвал: {names}]")
            elif text:
                parts.append(f"АССИСТЕНТ:\n{text}")
        elif role == "tool":
            parts.append(f"[TOOL]: {content[:500]}")
    return "\n\n".join(parts)


_SUMMARY_SYSTEM = (
    "Ты — ассистент, сжимающий историю диалога в краткое резюме. "
    "Сохрани: суть задачи, ключевые решения, изменённые файлы и пути, "
    "текущее состояние, следующий шаг. Убери: tool-дампы, повторы, "
    "несущественные детали. Формат: маркированный список, 10–20 пунктов, "
    "русский язык."
)


async def compress(session, client) -> CompressionResult | None:
    """Сжать историю сессии.

    Возвращает CompressionResult при успехе (для лога в UI) или None,
    если сжатие не выполнялось/не удалось.
    """
    messages = session.messages
    cut = find_cut(messages)
    if cut is None or cut <= 1:
        return None

    old = messages[1:cut]
    recent = messages[cut:]
    system_msg = messages[0]
    transcript = format_for_summary(old)
    if not transcript.strip():
        return None

    try:
        resp = await client.chat(
            messages=[
                {"role": "system", "content": _SUMMARY_SYSTEM},
                {"role": "user", "content": transcript},
            ],
            model=config.SUMMARY_MODEL,
            reasoning_effort="none",
        )
    except Exception as e:
        logger.warning("compress: LLM вызов не удался: %s", e)
        return None

    text = (getattr(resp.message, "content", None) or "").strip()
    if not text:
        logger.warning("compress: пустой ответ модели — пропускаю")
        return None

    # Оценку «до» снимаем, пока в messages ещё лежит несжатая история.
    tokens_before = estimate_tokens(messages)

    # Aux-вызов: добавляем usage, но не инкрементим request_count.
    session.total_usage = session.total_usage + resp.usage
    session.summary_count += 1
    summary_msg = {
        "role": "user",
        "content": (
            f"[Резюме предыдущего контекста, сжатие #{session.summary_count}]\n"
            f"{text}"
        ),
    }
    session.messages = [system_msg, summary_msg] + recent
    session.last_input_tokens = 0

    tokens_after = estimate_tokens(session.messages)
    logger.info(
        "compress: %d сообщений → %d, ~%d → ~%d токенов (summary_count=%d)",
        len(messages), len(session.messages),
        tokens_before, tokens_after, session.summary_count,
    )
    return CompressionResult(
        messages_before=len(messages),
        messages_after=len(session.messages),
        tokens_before=tokens_before,
        tokens_after=tokens_after,
        summary_count=session.summary_count,
    )
