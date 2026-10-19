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
import re
import time
from dataclasses import dataclass
from typing import Any

from agent_coder.agent import tool_extract
from agent_coder.agent.session import content_to_text
from agent_coder.config import config

logger = logging.getLogger(__name__)

MIN_RECENT = 6  # сколько последних сообщений оставить как есть
MIN_TOTAL = 10  # минимум сообщений для запуска сжатия
# Если старая часть — меньше этой доли истории, сжатие неэффективно.
MIN_OLD_SHARE = 0.2

# Аварийный режим: если total >> soft_limit, а old всё равно мал (огромный
# recent-блок), обычная защита MIN_OLD_SHARE блокирует единственное спасение —
# old будет копиться, пока не упрёмся. Сжимаем принудительно, но только если
# в old есть что сжимать (>= EMERGENCY_MIN_OLD_TOKENS) — иначе «резюме от
# резюме» ради нулевой экономии.
EMERGENCY_SOFT_FACTOR = 2.0
EMERGENCY_MIN_OLD_TOKENS = 3000

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
    """Оценка токенов по длине текста с учётом алфавита.

    Точного tokenizer'а нет (модели разные, тащить его ради строки в
    логе не стоит). Оценка нужна для информационного сообщения и
    эвристики MIN_OLD_SHARE, не для принятия решений о сжатии.

    Кириллица токенизируется плотнее (~2 символа на токен у BPE-словарей
    OpenAI-подобных моделей), латиница/код — ~4 символа на токен.
    """
    chars = 0
    cyr = 0
    for m in messages:
        text = content_to_text(m.get("content"))
        chars += len(text)
        cyr += sum(1 for ch in text if "а" <= ch.lower() <= "я" or ch.lower() == "ё")
        for tc in m.get("tool_calls") or []:
            tc_json = json.dumps(tc, ensure_ascii=False)
            chars += len(tc_json)
            cyr += sum(
                1 for ch in tc_json if "а" <= ch.lower() <= "я" or ch.lower() == "ё"
            )
    latin = chars - cyr
    return max(0, round(cyr / 2 + latin / 4))


def soft_limit(session) -> int:
    """Порог input-токенов: min(model.context * 0.6, MAX_HISTORY_TOKENS)."""
    from agent_coder.model_catalog import catalog

    m = catalog.get(session.model)
    if m and m.context:
        return min(int(m.context * 0.6), config.MAX_HISTORY_TOKENS)
    return config.MAX_HISTORY_TOKENS


def find_cut(
    messages: list[dict[str, Any]], min_recent: int = MIN_RECENT
) -> int | None:
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
    """Плоский транскрипт для суммаризации.

    Tool-результаты прогоняются через tool_extract (per-tool, 2048) — то же
    сжатие, что и в session.messages, но с меньшим лимитом (резюме нужны
    факты, не полный вывод). Предыдущие резюме пропускаются — они
    передаются отдельно (см. compress).
    """
    tool_names: dict[str, str] = {}
    for m in messages:
        for tc in m.get("tool_calls") or []:
            tc_id = tc.get("id")
            fn = tc.get("function") or {}
            if tc_id and fn.get("name"):
                tool_names[tc_id] = fn["name"]
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
                    (tc.get("function") or {}).get("name", "?") for tc in tcs
                )
                parts.append(f"АССИСТЕНТ: {text}\n[вызвал: {names}]")
            elif text:
                parts.append(f"АССИСТЕНТ:\n{text}")
        elif role == "tool":
            name = tool_names.get(m.get("tool_call_id"), "")
            label = f"[TOOL {name}]" if name else "[TOOL]"
            extracted = tool_extract.extract(
                name or "", {}, content, 2048, for_summary=True
            )
            parts.append(f"{label}: {extracted}")
    return "\n\n".join(parts)


_SUMMARY_SYSTEM = """Ты — суммаризатор истории диалога агента-программиста. Сожми старые
сообщения в структурированное резюме.

ЖЁСТКИЕ ПРАВИЛА:
- Используй ТОЛЬКО факты из транскрипта ниже.
- Если чего-то нет в транскрипте — пиши "не было" или пропускай.
- НЕ додумывай имена файлов, SHA коммитов, пути, версии, результаты
  тестов. Копируй их буквально из транскрипта.
- Если факт не подтверждён транскриптом — не включай его.
- Не описывай код, которого нет в транскрипте.
- Не выдумывай номера строк, имена функций, названия коммитов.
- Если ниже передан блок [ИЗВЕСТНЫЕ ФАКТЫ (work_state)] — считай его
  подтверждённым. Используй его для секций "Сделано" и "Осталось",
  НЕ противоречь ему. Narrative пиши только из транскрипта.

ФОРМАТ (строго):
### Задача
<1-3 пункта: что просил пользователь>

### Сделано
<маркированный список: что агент сделал; пути файлов — из транскрипта>

### Проверено
<маркированный список: тесты, проверки; если не проверялось — "не проверялось">

### Осталось
<маркированный список: что не сделано; если всё сделано — "всё сделано">

Русский язык. 10-20 пунктов максимум. Без воды.
"""

_SUMMARY_UPDATE_SYSTEM = """Ты — суммаризатор истории диалога агента-программиста. Тебе дано
ПРЕДЫДУЩЕЕ РЕЗЮМЕ и новые события диалога, произошедшие ПОСЛЕ него.

ЖЁСТКИЕ ПРАВИЛА:
- Используй ТОЛЬКО факты из предыдущего резюме и нового транскрипта.
- НЕ додумывай имена файлов, SHA коммитов, пути, версии, результаты
  тестов. Копируй их буквально.
- Предыдущее резюме могло содержать неточности. Если факт из него
  НЕ подтверждён новым транскриптом — пометь "(не подтверждено)"
  или убери.
- Не копируй галлюцинации из предыдущего резюме.
- Если чего-то нет — пиши "не было" или пропускай.
- Если ниже передан блок [ИЗВЕСТНЫЕ ФАКТЫ (work_state)] — считай его
  подтверждённым. Используй его для секций "Сделано" и "Осталось",
  НЕ противоречь ему. Narrative пиши только из транскрипта.

ФОРМАТ (строго):
### Задача
<1-3 пункта>

### Сделано
<маркированный список; пути — из транскрипта>

### Проверено
<маркированный список; если не проверялось — "не проверялось">

### Осталось
<маркированный список; если всё сделано — "всё сделано">

Русский язык. 10-20 пунктов максимум. Без воды.
"""


_UNCONFIRMED_MARKER = "(не подтверждено)"

# SHA-подобные токены: 7-40 hex-символов, обязательно есть a-f/A-F
# (чтобы не ловить "1048576" — все цифры). \b с обеих сторон, чтобы не
# цеплять hex-куски внутри имён (_abc1234, superdeadbeef).
_SUMMARY_SHA_RE = re.compile(
    r"\b(?=[0-9a-fA-F]{7,40}\b)[0-9a-fA-F]*[a-fA-F][0-9a-fA-F]*\b"
)

# Пути: либо путь с одним и более слэшем (agent_coder/models.py,
# ./scripts/run.sh, /home/user/x.py), либо имя файла с известным
# расширением (pyproject.toml, README.md). Lookbehind/lookahead по
# «словоподобным» символам, чтобы не матчить хвосты строк.
_SUMMARY_PATH_RE = re.compile(
    r"(?<![\w.@+/-])"
    r"(?:"
    r"(?:\.{0,2}/|/)?(?:[A-Za-z0-9._@+-]+/)+[A-Za-z0-9._@+-]+"
    r"|"
    r"[A-Za-z0-9._@+-]+\.(?:py|md|toml|yml|yaml|sh|json|cfg|ini|rs|go"
    r"|js|ts|jsx|tsx|java|rb|php|cpp|hpp|cs|swift|kt|sql|html|css|scss"
    r"|xml|txt|lock|mk)"
    r")"
    r"(?![\w@+/-])"
)

# Чисто цифры/слэши/точки — это не пути, а даты (2024/10/18) и версии.
_PURE_DIGITS_PATH_RE = re.compile(r"^[\d/.\-]+$")


def _validate_summary(summary: str, transcript: str, extra_known: str = "") -> str:
    """Пометить (не подтверждено) выдуманные SHA и пути в резюме.

    Защита независимо от качества модели: SHA-коммиты и пути файлов
    копируются из транскрипта дословно, поэтому любой такой токен,
    которого нет в транскрипте, почти наверняка галлюцинация.

    Поведение:
      - токен, который есть в transcript как подстрока, не трогается;
      - токен, которого нет, получает суффикс (не подтверждено);
      - токен, уже помеченный (не подтверждено), не помечается повторно
        (идемпотентность — важно для повторных сжатий).

    Не удаляет: структура ### Задача/Сделано/Проверено/Осталось
    сохраняется, читатель видит, какие факты требуют перепроверки.

    В update-режиме transcript — только НОВЫЕ события (prev_summary
    передаётся модели отдельно и не входит в transcript). Это
    осознанная строгость: _SUMMARY_UPDATE_SYSTEM сам требует помечать
    факты из предыдущего резюме, не подтверждённые новым транскриптом;
    валидатор ловит то, что модель пропустила.

    extra_known — дополнительный эталон «известных фактов» (work_state
    и/или prev_summary). Токены, встречающиеся в extra_known, не
    помечаются: это не галлюцинация, а факт из structured memory.
    Без этого work_state-факты, которых нет в текущем transcript,
    получали бы ложное (не подтверждено).
    """
    if not summary or not transcript:
        return summary

    def _already_marked(text: str, end: int) -> bool:
        tail = text[end : end + 40].lstrip()
        return tail.startswith(_UNCONFIRMED_MARKER)

    def _is_path_noise(token: str) -> bool:
        return "/" in token and _PURE_DIGITS_PATH_RE.match(token) is not None

    known = transcript + ("\n" + extra_known if extra_known else "")

    def _repl(m: re.Match[str]) -> str:
        token = m.group(0)
        if token in known:
            return token
        if _is_path_noise(token):
            return token
        if _already_marked(m.string, m.end()):
            return token
        return f"{token} {_UNCONFIRMED_MARKER}"

    combined = re.compile(
        rf"(?:{_SUMMARY_PATH_RE.pattern})|(?:{_SUMMARY_SHA_RE.pattern})"
    )
    return combined.sub(_repl, summary)


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
            n,
            _COOLDOWN_SECONDS,
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
    share = old_tokens / total_tokens if total_tokens else 1.0
    soft = soft_limit(session)
    emergency = (
        total_tokens > soft * EMERGENCY_SOFT_FACTOR
        and old_tokens >= EMERGENCY_MIN_OLD_TOKENS
    )
    if share < MIN_OLD_SHARE and not emergency:
        logger.info(
            "compress: старая часть ~%d из ~%d токенов (<%.0f%%) — пропускаю",
            old_tokens,
            total_tokens,
            MIN_OLD_SHARE * 100,
        )
        return None
    if share < MIN_OLD_SHARE and emergency:
        logger.warning(
            "compress: аварийный режим — total ~%d > soft*%.1f (%d), "
            "old ~%d (%.0f%%) ниже %.0f%%, но >= %d — сжимаю принудительно",
            total_tokens,
            EMERGENCY_SOFT_FACTOR,
            int(soft * EMERGENCY_SOFT_FACTOR),
            old_tokens,
            share * 100,
            MIN_OLD_SHARE * 100,
            EMERGENCY_MIN_OLD_TOKENS,
        )

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

    # work_state — детерминированные факты, которые LLM не должна
    # потерять/пересуммаризировать. Передаём как «известные факты»,
    # чтобы narrative не противоречил state.
    from agent_coder.agent.work_state import render_for_prompt

    # getattr: тесты/моки могут не иметь поля; реальный Session — имеет.
    ws_block = render_for_prompt(getattr(session, "work_state", None))

    if prev_summary:
        user_content = (
            f"ПРЕДЫДУЩЕЕ РЕЗЮМЕ:\n{prev_summary}\n\n"
            f"НОВЫЕ СОБЫТИЯ ДИАЛОГА:\n{transcript}"
        )
        if ws_block:
            user_content += f"\n\n[ИЗВЕСТНЫЕ ФАКТЫ (work_state)]:\n{ws_block}"
        system = _SUMMARY_UPDATE_SYSTEM
    else:
        user_content = transcript
        if ws_block:
            user_content += f"\n\n[ИЗВЕСТНЫЕ ФАКТЫ (work_state)]:\n{ws_block}"
        system = _SUMMARY_SYSTEM

    try:
        resp = await client.chat(
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user_content},
            ],
            model=config.SUMMARY_MODEL,
            reasoning_effort="none",
            temperature=0.0,
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
    # extra_known: факты из prev_summary и work_state легитимны, даже если
    # их нет в текущем transcript (структурная память переживает сжатие).
    text = _validate_summary(
        text, transcript, extra_known=(prev_summary or "") + "\n" + ws_block
    )
    _register_success(session.id)

    # Детерминированный suffix: work_state не LLM-генерирован, поэтому
    # не проходит валидатор и не может быть «галлюцинацией». Гарантия,
    # что факты из state попадут в резюме, даже если суммаризатор их
    # проигнорировал.
    if ws_block:
        text = text + "\n\n" + ws_block

    # Оценку «до» снимаем, пока в messages ещё лежит несжатая история.
    tokens_before = estimate_tokens(messages)

    # RAG-lite: старая часть не выбрасывается, а уходит в архив —
    # агент сможет найти потерянные детали через search_history.
    try:
        from agent_coder.agent import history_archive

        archived = history_archive.append_messages(session.id, old)
        if archived:
            logger.info("compress: %d сообщений заархивировано", archived)
    except Exception as e:
        logger.warning("compress: архивация не удалась: %s", e)

    # Aux-вызов: добавляем usage, но не инкрементим request_count.
    session.total_usage = session.total_usage + resp.usage
    session.summary_count += 1
    summary_msg = {
        "role": "user",
        "content": (
            f"[Резюме предыдущего контекста, сжатие #{session.summary_count}]\n{text}"
        ),
    }
    session.messages = [system_msg, summary_msg] + recent
    session.last_input_tokens = 0

    tokens_after = estimate_tokens(session.messages)
    logger.info(
        "compress: %d сообщений → %d, ~%d → ~%d токенов (summary_count=%d)",
        len(messages),
        len(session.messages),
        tokens_before,
        tokens_after,
        session.summary_count,
    )
    return CompressionResult(
        messages_before=len(messages),
        messages_after=len(session.messages),
        tokens_before=tokens_before,
        tokens_after=tokens_after,
        summary_count=session.summary_count,
    )
