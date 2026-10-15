"""Маскирование секретов в логах.

RouterAI возвращает ошибки, в которых может быть URL с API-ключом
или сам ключ в тексте. Если писать их как есть — секреты утекут в
journalctl / логи CI. Модуль даёт:
- mask_secrets(text) — замена известных паттернов на <hidden>;
- SecretFilter — logging.Filter, применяемый к logger.
"""
from __future__ import annotations

import logging
import re

# Каждый паттерн: (имя, regex с группой-префиксом). Префикс остаётся,
# значение после него маскируется.
_SECRET_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    # env-стиль: NAME=value
    ("ROUTERAI_API_KEY", re.compile(r"(ROUTERAI_API_KEY\s*[=:]\s*)\S+")),
    ("TELEGRAM_TOKEN",   re.compile(r"(TELEGRAM_TOKEN\s*[=:]\s*)\S+")),
    ("OPENAI_API_KEY",   re.compile(r"(OPENAI_API_KEY\s*[=:]\s*)\S+")),
    # OpenAI-совместимые ключи: sk-XXXX...
    ("sk-key",           re.compile(r"(sk-)[A-Za-z0-9_\-]{16,}")),
    # Bearer-токены (короткие тоже маскируем — лучше перебдеть)
    ("bearer",           re.compile(r"(Bearer\s+)[A-Za-z0-9_.\-]{8,}")),
]


def mask_secrets(text: str) -> str:
    """Заменить секреты в тексте на <hidden>. Best-effort фильтр."""
    if not text:
        return text
    for _name, pat in _SECRET_PATTERNS:
        text = pat.sub(lambda m: m.group(1) + "<hidden>", text)
    return text




# Разделитель в теге DSML: ASCII-пайп `|` (U+007C) или FULLWIDTH VERTICAL
# LINE `｜` (U+FF5C) — DeepSeek отдаёт оба варианта, причём fullwidth
# встречается чаще (напр. `<｜｜DSML｜｜ calls>`). Раньше учитывался только
# ASCII-вариант, из-за чего разметка утекала в чат и историю сессии.
_DSML_MARK = r"[|\uFF5C]{2}DSML[|\uFF5C]{2}"

# Открывающий (или закрывающий) DSML-тег: `<||DSML||invoke`,
# `</｜｜DSML｜｜parameter` и т.п. Закрывающий `>` не обязателен — поток
# может быть обрезан на полуслове.
_DSML_TAG_RE = re.compile(rf"</?{_DSML_MARK}")

# Имена структурных тегов нативного формата (внутри блока вызова).
_DSML_BLOCK_TAGS = ("calls", "invoke", "parameter")


def _last_dsml_tag_end(text: str) -> int:
    """Индекс сразу после `>` последнего DSML-тега или -1, если `>` нет.

    Учитываются только те маркеры, после которых в тексте вообще есть `>`:
    у обрезанного потока (`<||DSML||invoke name="x"`) такого нет.
    """
    end = -1
    for m in _DSML_TAG_RE.finditer(text):
        gt = text.find(">", m.end())
        if gt != -1:
            end = max(end, gt + 1)
    return end


def strip_dsml(text: str) -> str:
    """Убрать нативные DSML-теги DeepSeek из content.

    DeepSeek иногда возвращает вызовы инструментов в нативном формате
    `<||DSML||invoke ...>...</||DSML||invoke>`, минуя стандартные
    OpenAI tool_calls. Это ломает UI и вводит в заблуждение пользователя.
    RouterAI обычно мапит их корректно, но в редких конфигурациях
    (например, урезанный набор tools) утечка случается.

    Формат нестабилен: разделителем бывает и ASCII-пайп `|` (U+007C), и
    fullwidth `｜` (U+FF5C). Раньше распознавался только ASCII-вариант,
    поэтому разметка утекала в Telegram и оставалась в истории сессии.

    Две стратегии:
    - если разметка тянется до конца сообщения (обычная утечка вызова
      инструмента, в т.ч. обрезанный поток) — отрезаем от первого маркера,
      сохраняя осмысленный текст перед ним;
    - если после разметки ещё есть текст (модель *упоминает* теги в прозе)
      — удаляем только цельные блоки `calls/invoke/parameter`, не калеча
      объяснение.
    """
    if not text or "DSML" not in text:
        return text
    first = _DSML_TAG_RE.search(text)
    if first is None:
        return text

    last_end = _last_dsml_tag_end(text)
    if last_end == -1 or not text[last_end:].strip():
        return text[: first.start()].rstrip()

    cleaned = text
    for tag in _DSML_BLOCK_TAGS:
        cleaned = re.sub(
            rf"<{_DSML_MARK}\s*{tag}\b.*?</{_DSML_MARK}\s*{tag}>",
            "",
            cleaned,
            flags=re.DOTALL,
        )
    return cleaned.strip()


class SecretFilter(logging.Filter):
    """Маскирует record.msg/args перед форматированием.

    Устанавливается на logger через .addFilter(SecretFilter()).
    Форматирует сообщение здесь же, чтобы гарантировать, что итоговый
    record.getMessage() вернёт уже очищенный текст.
    """

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            msg = record.getMessage()
        except Exception:
            return True
        masked = mask_secrets(msg)
        if masked != msg:
            # Переписываем record так, чтобы %-args больше не применялись
            record.msg = masked
            record.args = ()
        return True
