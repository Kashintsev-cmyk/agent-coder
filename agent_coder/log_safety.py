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




_DSML_RE = re.compile(
    r"<\|\|DSML\|\|.*?>.*?<\|\|DSML\|\|.*?>",
    re.DOTALL,
)
_DSML_SIMPLE_RE = re.compile(r"<\|\|DSML\|\|.*", re.DOTALL)


def strip_dsml(text: str) -> str:
    """Убрать нативные DSML-теги DeepSeek из content.

    DeepSeek иногда возвращает вызовы инструментов в нативном формате
    `<||DSML||invoke ...>...</||DSML||invoke>`, минуя стандартные
    OpenAI tool_calls. Это ломает UI и вводит в заблуждение пользователя.
    RouterAI обычно мапит их корректно, но в редких конфигурациях
    (например, урезанный набор tools) утечка случается.
    """
    if not text or "DSML" not in text:
        return text
    text = _DSML_RE.sub("", text)
    text = _DSML_SIMPLE_RE.sub("", text)
    return text.strip()


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
