"""Сжатие истории сессии через LLM.

Вызывается из loop.py, когда input_tokens превысил soft-limit
(min(model.context * 0.6, config.MAX_HISTORY_TOKENS)). Старые сообщения
заменяются на одно резюме от config.SUMMARY_MODEL.
"""
from __future__ import annotations

import logging
from typing import Any

from agent_coder.config import config

logger = logging.getLogger(__name__)

MIN_RECENT = 6   # сколько последних сообщений оставить как есть
MIN_TOTAL = 10   # минимум сообщений для запуска сжатия


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
        content = (m.get("content") or "").strip()
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


async def compress(session, client) -> bool:
    """Сжать историю сессии. True — если сжатие выполнено."""
    messages = session.messages
    cut = find_cut(messages)
    if cut is None or cut <= 1:
        return False

    old = messages[1:cut]
    recent = messages[cut:]
    system_msg = messages[0]
    transcript = format_for_summary(old)
    if not transcript.strip():
        return False

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
        return False

    text = (getattr(resp.message, "content", None) or "").strip()
    if not text:
        logger.warning("compress: пустой ответ модели — пропускаю")
        return False

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
    logger.info(
        "compress: %d сообщений → %d (summary_count=%d)",
        len(messages), len(session.messages), session.summary_count,
    )
    return True
