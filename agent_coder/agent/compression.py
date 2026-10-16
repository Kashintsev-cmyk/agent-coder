"""Сжатие истории сессии через LLM.

Вызывается из loop.py, когда input_tokens превысил soft-limit
(min(model.context * 0.6, config.MAX_HISTORY_TOKENS)). Старые сообщения
заменяются на одно резюме от config.SUMMARY_MODEL.

Защиты от деградации:
  - предыдущее резюме не пересуммаризуется «в общей куче», а передаётся
    модели отдельно с инструкцией его обновить (сохранить факты);
  - если сжимаемая часть — малая доля истории (огромный recent-блок),
    сжатие пропускается: оно почти не сэкономит и запустит цикл
    «резюме от резюме»;
  - после повторных сбоев LLM — cooldown, чтобы не дёргать суммаризацию
    на каждой итерации.
"""
from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass
from typing import Any

from agent_coder.agent.session import content_to_text
from agent_coder.config import config

logger = logging.getLogger(__name__)

MIN_RECENT = 6   # сколько последних сообщений оставить как есть
MIN_TOTAL = 10   # минимум сообщений для запуска сжатия
# Если старая часть — меньше этой доли истории, сжатие неэффективно.
MIN_OLD_SHARE = 0.2

# Cooldown после неудачных вызовов суммаризации.
_COOLDOWN_SECONDS = 600.0
_MAX_FAIL_STREAK = 2
_fail_streak: dict[str, int] = {}
_fail_until: dict[str, float] = {}


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


def _is_summary(msg: dict[str, Any]) -> bool:
    """Резюме предыдущего сжатия (создаётся в compress())."""
    content = msg.get("content")
    return (
        msg.get("role") == "user"
        and isinstance(content, str)
        and content.startswith("[Резюме предыдущего контекста")
    )


def format_for_summary(messages: list[dict[str, Any]]) -> str:
    """Плоский транскрипт для суммаризации. Обрезает длинные куски.

    Предыдущие резюме пропускаются — они передаются отдельно (см. compress).
    """
    parts: list[str] = []
    for m in messages:
        if _is_summary(m):
            continue
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

_SUMMARY_UPDATE_SYSTEM = (
    "Ты — ассистент, сжимающий историю диалога в краткое резюме. "
    "Тебе дано ПРЕДЫДУЩЕЕ РЕЗЮМЕ и новые события диалога, произошедшие "
    "ПОСЛЕ него. Обнови резюме: сохрани из предыдущего всё ещё актуальное "
    "(задачу, решения, пути файлов, состояние), добавь новое, убери "
    "устаревшее и выполненное. Не теряй факты из предыдущего резюме, "
    "если они не отменены. Формат: маркированный список, 10–20 пунктов, "
    "русский язык."
)


def _cooldown_active(session_id: str) -> bool:
    until = _fail_until.get(session_id, 0.0)
    return time.monotonic() < until


def _register_failure(session_id: str) -> None:
    n = _fail_streak.get(session_id, 0) + 1
    _fail_streak[session_id] = n
    if n >= _MAX_FAIL_STREAK:
        _fail_until[session_id] = time.monotonic() + _COOLDOWN_SECONDS
        logger.warning(
            "compress: %d неудач подряд — cooldown %.0f с",
            n, _COOLDOWN_SECONDS,
        )


def _register_success(session_id: str) -> None:
    _fail_streak.pop(session_id, None)
    _fail_until.pop(session_id, None)


async def compress(session, client) -> CompressionResult | None:
    """Сжать историю сессии.

    Возвращает CompressionResult при успехе (для лога в UI) или None,
    если сжатие не выполнялось/не удалось.
    """
    if _cooldown_active(session.id):
        return None

    messages = session.messages
    cut = find_cut(messages)
    if cut is None or cut <= 1:
        return None

    old = messages[1:cut]
    recent = messages[cut:]
    if not messages or messages[0].get("role") != "system":
        logger.warning("compress: messages[0] не system — пропускаю")
        return None
    system_msg = messages[0]

    # Защита от «огромного recent-блока»: если сжимаемая часть — малая
    # доля истории, сжатие почти не сэкономит и запустит цикл
    # «резюме от резюме». Лучше подождать, пока старая часть подрастёт.
    old_tokens = estimate_tokens(old)
    total_tokens = estimate_tokens(messages)
    if total_tokens > 0 and old_tokens / total_tokens < MIN_OLD_SHARE:
        logger.info(
            "compress: старая часть ~%d из ~%d токенов (<%.0f%%) — пропускаю",
            old_tokens, total_tokens, MIN_OLD_SHARE * 100,
        )
        return None

    # Предыдущее резюме передаём отдельно, чтобы модель обновила его,
    # а не пересуммаризировала наравне с живым диалогом.
    prev_summary: str | None = None
    for m in old:
        if _is_summary(m):
            prev_summary = content_to_text(m.get("content")).strip()
            break

    transcript = format_for_summary(old)
    if not transcript.strip() and not prev_summary:
        return None

    if prev_summary:
        user_content = (
            f"ПРЕДЫДУЩЕЕ РЕЗЮМЕ:\n{prev_summary}\n\n"
            f"НОВЫЕ СОБЫТИЯ ДИАЛОГА:\n{transcript}"
        )
        system = _SUMMARY_UPDATE_SYSTEM
    else:
        user_content = transcript
        system = _SUMMARY_SYSTEM

    try:
        resp = await client.chat(
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user_content},
            ],
            model=config.SUMMARY_MODEL,
            reasoning_effort="none",
        )
    except Exception as e:
        logger.warning("compress: LLM вызов не удался: %s", e)
        _register_failure(session.id)
        return None

    text = (getattr(resp.message, "content", None) or "").strip()
    if not text:
        logger.warning("compress: пустой ответ модели — пропускаю")
        _register_failure(session.id)
        return None
    _register_success(session.id)

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
