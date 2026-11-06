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

# Retry при context-length (400 от провайдера на промпте суммаризатора):
# дропаем старейшие API-раунды, как Claude Code truncateHeadForPTLRetry.
# Assistant + его tool-результаты — атомарная группа, рвать нельзя
# (tool_call без tool_result = 400). До 3 попыток.
_PTL_MAX_RETRIES = 3
_PTL_DROP_FRACTION = 0.2  # 20% раундов за retry (fallback Claude Code)

_CONTEXT_LENGTH_MARKERS = (
    "context length",
    "context_length",
    "maximum context",
    "max context",
    "too many tokens",
    "prompt is too long",
    "input is too long",
    "exceeds the model",
    "reduce the length",
    "request too large",
)


def _is_context_length_error(exc: Exception) -> bool:
    """400 «слишком длинный промпт»? Провайдеры не дают структурированный код."""
    text = str(exc).lower()
    return any(m in text for m in _CONTEXT_LENGTH_MARKERS)


def _drop_oldest_api_round(
    old: list[dict[str, Any]],
) -> list[dict[str, Any]] | None:
    """Дропнуть старейшие API-раунды (assistant + tool-результаты).

    Группы атомарны: assistant с tool_calls + все его tool-результаты
    уходят вместе (иначе провайдер вернёт 400 «tool_call без ответа»).
    Возвращает новый список или None, если дропать нечего (<2 раундов
    — последний не трогаем, он recent-контекст).
    """
    if len(old) <= 1:
        return None
    rounds: list[list[dict[str, Any]]] = []
    current: list[dict[str, Any]] = []
    for m in old:
        if m.get("role") == "assistant" and current:
            rounds.append(current)
            current = [m]
        else:
            current.append(m)
    if current:
        rounds.append(current)
    # Требуем >=2 assistant-циклов: иначе после дропа останется один
    # «осиротевший» assistant(tool_calls)+tool без user-контекста задачи.
    assistant_rounds = sum(
        1 for r in rounds if any(m.get("role") == "assistant" for m in r)
    )
    if assistant_rounds < 2:
        return None
    drop_n = max(1, round(len(rounds) * _PTL_DROP_FRACTION))
    if drop_n >= len(rounds):
        return None
    kept = rounds[drop_n:]
    return [m for r in kept for m in r]


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

# Long-context тир: выше порога провайдер пересчитывает ВСЮ ставку за 1M
# (не только превышение). Haiku 5.5: >100K input → 52.63/263 ₽/1M (×5 к
# 10.53/52.63). В каталоге тиров нет (pricing — скаляры), поэтому порог
# держим здесь. 90K = 0.9 × TIER_THRESHOLD — запас под эвристику
# estimate_tokens (ratio провайдер/эвристика 0.31..1.85). См. 43ce75b.
TIER_THRESHOLD_TOKENS = 100_000
TIER_FACTOR = 5.0

# Зазор между окном суммаризатора и порогом сжатия. При <1.25 old-блок
# вплотную подходит к context сумаризатора: 400 context-length на живом
# сжатии (после успешного config.validate()) — падение в рантайме.
SUMMARY_MIN_CONTEXT_RATIO = 1.25


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
    # Фактические деньги за summary-вызов (usage.cost провайдера). Отдельно
    # от tokens_*: те — эвристика, эта — то, что списали.
    cost: float = 0.0


def model_price(model_id: str, tokens: int, *, output_tokens: int = 0) -> float:
    """Стоимость запроса в ₽ с учётом long-context тира.

    Порог — по СУММАРНОМУ input запроса, ставка пересчитывается ЦЕЛИКОМ
    (не только превышение). Модели без записи в каталоге или с нулевой
    ценой дают 0.0 — вызывающий не может вычислить, а не «бесплатно».
    """
    from agent_coder.model_catalog import catalog

    m = catalog.get(model_id)
    if m is None or m.input_price <= 0:
        return 0.0
    factor = TIER_FACTOR if tokens > TIER_THRESHOLD_TOKENS else 1.0
    return (tokens * m.input_price + output_tokens * m.output_price) / 1e6 * factor


def summary_model_problem(
    model_id: str | None = None, *, soft: int | None = None
) -> str | None:
    """Почему суммаризатор непригоден при текущем MAX_HISTORY_TOKENS.

    ``None`` — всё в порядке. Иначе — текст проблемы для config.validate()
    и лога. Проверяем то, что реально ломает сжатие в рантайме:
    неизвестная модель (нулевой context/цены → платное сжатие не считается
    и не диагностируется) и нехватка окна под сжимаемый блок.
    """
    from agent_coder.model_catalog import catalog

    mid = config.SUMMARY_MODEL if model_id is None else model_id
    if not mid:
        return "SUMMARY_MODEL пуст"
    m = catalog.get(mid)
    if m is None or not m.context or m.input_price <= 0:
        return (
            f"SUMMARY_MODEL={mid!r} нет в каталоге моделей или у неё нулевые "
            f"context/цена — сжатие истории работать не будет"
        )
    limit = soft if soft is not None else config.MAX_HISTORY_TOKENS
    # old при сжатии доходит до всего soft-лимита (recent может быть
    # маленьким). Нужен запас: ctx суммаризатора >= soft * SUMMARY_MIN_CONTEXT_RATIO.
    need = int(limit * SUMMARY_MIN_CONTEXT_RATIO)
    if m.context < need:
        return (
            f"SUMMARY_MODEL={mid!r}: context={m.context}, а MAX_HISTORY_TOKENS="
            f"{limit} требует минимум {need} "
            f"(×{SUMMARY_MIN_CONTEXT_RATIO}) — сжатие упрётся в context-length"
        )
    return None


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

    # work_state — детерминированные факты, которые LLM не должна
    # потерять/пересуммаризировать.
    from agent_coder.agent.work_state import render_for_prompt

    ws_block = render_for_prompt(getattr(session, "work_state", None))

    def _build_user_content(old_msgs: list[dict[str, Any]]) -> tuple[str, str, str]:
        """(system, user_content, transcript) из old_msgs."""
        tr = format_for_summary(old_msgs)
        if prev_summary:
            uc = f"ПРЕДЫДУЩЕЕ РЕЗЮМЕ:\n{prev_summary}\n\nНОВЫЕ СОБЫТИЯ ДИАЛОГА:\n{tr}"
            sys_msg = _SUMMARY_UPDATE_SYSTEM
        else:
            uc = tr
            sys_msg = _SUMMARY_SYSTEM
        if ws_block:
            uc += f"\n\n[ИЗВЕСТНЫЕ ФАКТЫ (work_state)]:\n{ws_block}"
        return sys_msg, uc, tr

    # Архив — ПОЛНЫЙ old (до возможного retry-дропа). Даже если LLM-сжатие
    # пришлось урезать, оригинал должен попасть в history_archive —
    # иначе search_history не найдёт выброшенное.
    archive_old = old

    system, user_content, transcript = _build_user_content(old)
    if not transcript.strip() and not prev_summary:
        return None

    ptl_retries = _PTL_MAX_RETRIES
    while True:
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
            break
        except Exception as e:
            if not _is_context_length_error(e):
                logger.warning("compress: LLM вызов не удался: %s", e)
                _register_failure(session.id)
                return None
            dropped = _drop_oldest_api_round(old)
            if dropped is None:
                logger.warning(
                    "compress: context-length, дропать нечего (old=%d) — сдаюсь",
                    len(old),
                )
                _register_failure(session.id)
                return None
            old = dropped
            system, user_content, transcript = _build_user_content(old)
            if not transcript.strip() and not prev_summary:
                logger.warning("compress: context-length, transcript пуст — сдаюсь")
                _register_failure(session.id)
                return None
            ptl_retries -= 1
            if ptl_retries <= 0:
                logger.warning("compress: context-length retry исчерпан — сдаюсь")
                _register_failure(session.id)
                return None
            logger.warning(
                "compress: context-length, дропнул старейшие раунды, retry (%d left)",
                ptl_retries,
            )

    text = (getattr(resp.message, "content", None) or "").strip()
    # B11.x: cost summary-вызова — инлайн (author intent: не инкрементим
    # request_count). Учитываем ДО early-return ниже — деньги потрачены
    # и на пустой ответ.
    session.total_usage = session.total_usage + resp.usage
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

    # Реальная цена summary-вызова. resp.usage.cost приходит от провайдера и
    # уже учитывает тир, но бывает 0.0 (не все прокси отдают cost) — тогда
    # считаем сами по каталогу, включая long-context тир.
    summary_input_tokens = estimate_tokens(
        [
            {"role": "system", "content": system},
            {"role": "user", "content": user_content},
        ]
    )
    if summary_input_tokens > TIER_THRESHOLD_TOKENS:
        logger.warning(
            "compress: вход суммаризатора ~%d токенов > %d — long-context тир "
            "(×%.0f к ставке), сжатие стоит ~%.2f₽",
            summary_input_tokens,
            TIER_THRESHOLD_TOKENS,
            TIER_FACTOR,
            model_price(config.SUMMARY_MODEL, summary_input_tokens),
        )
    summary_cost = getattr(resp.usage, "cost", 0.0) or 0.0
    if summary_cost <= 0:
        summary_cost = model_price(
            config.SUMMARY_MODEL,
            summary_input_tokens,
            output_tokens=estimate_tokens([{"role": "assistant", "content": text}]),
        )

    # RAG-lite: старая часть не выбрасывается, а уходит в архив —
    # агент сможет найти потерянные детали через search_history.
    try:
        from agent_coder.agent import history_archive

        archived = history_archive.append_messages(session.id, archive_old)
        if archived:
            logger.info("compress: %d сообщений заархивировано", archived)
    except Exception as e:
        logger.warning("compress: архивация не удалась: %s", e)

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
        "compress: %d сообщений → %d, ~%d → ~%d токенов (summary_count=%d, %.4f₽)",
        len(messages),
        len(session.messages),
        tokens_before,
        tokens_after,
        session.summary_count,
        summary_cost,
    )
    return CompressionResult(
        messages_before=len(messages),
        messages_after=len(session.messages),
        tokens_before=tokens_before,
        tokens_after=tokens_after,
        summary_count=session.summary_count,
        cost=summary_cost,
    )
