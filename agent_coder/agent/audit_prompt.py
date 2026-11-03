"""Сборка system prompt для режима AUDIT.

Текст аудиторского промта живёт в agent_coder/rules/audit_standard.md
(редактируется как обычный markdown, ревьюится в PR). Здесь — только
загрузка и подстановка плейсхолдеров.
"""

from __future__ import annotations

from agent_coder.rules import load_audit_standard


def _fill(template: str, **kwargs: str) -> str:
    """Подставить {name} через str.replace, а не str.format.

    str.format падает на любых других { } в тексте (например, в
    примерах кода внутри markdown). replace безопасен и предсказуем.
    """
    out = template
    for key, value in kwargs.items():
        out = out.replace("{" + key + "}", value)
    return out


_AUDIT_SCOPE_RULE = """

## Ограничения области аудита (жёсткие, не обходить)

1. **Только относительные пути от workspace.** Абсолютные пути
   (`/home/...`, `/tmp/...`, `~/...`) в `read_file`, `list_files`,
   `search`, `run_command` будут отклонены. Если нужен файл — пиши
   путь относительно workspace (`app/bot.py`, не
   `/home/botuser/bots/weather-bot/app/bot.py`).

2. **Не читай `.venv/`, `site-packages/`, `.git/`, `__pycache__/`,
   `.mypy_cache/`.** Это внешние пакеты и служебные каталоги — они
   вне области аудита, даже если finding ссылается на их код. Если
   для подтверждения нужна внешняя библиотека (httpx, aiogram) —
   помечай finding как `requires external check: <lib>` и двигайся
   дальше, НЕ пытайся открыть исходники пакета.

3. **Если инструмент вернул ошибку «вне области аудита» или
   «недоступен в audit-режиме» — НЕ повторяй ту же команду** с
   другими флагами (`-A`, `-B`, `-n`, другим путём). Ошибка
   означает, что путь/инструмент **запрещён**, а не что команда
   неверна. Переформулируй через относительный путь от workspace
   или пропусти.

4. **5 ошибок подряд → аудит останавливается** (circuit breaker,
   `MAX_CONSECUTIVE_ERRORS=5`). Каждая ошибка — расход бюджета.
   Одна blocked-команда, повторённая 3 раза с разными флагами, —
   это уже 3 из 5.
"""


def build_audit_prompt(
    workspace: str,
    project_name: str = "",
    entrypoint: str = "—",
    python_version: str = "—",
    dependencies: str = "—",
    domain: str = "—",
    requirements: str = "—",
    priorities: str = "—",
    scopes: list[str] | None = None,
) -> str:
    template = load_audit_standard()
    result = _fill(
        template,
        workspace=workspace,
        project_name=project_name or workspace.rsplit("/", 1)[-1],
        entrypoint=entrypoint,
        python_version=python_version,
        dependencies=dependencies,
        domain=domain,
        requirements=requirements,
        priorities=priorities,
    )
    if scopes:
        block = (
            "\n\n## Область аудита (B11.3)\n\n"
            "Аудируй **только** перечисленные ниже пути (относительно workspace). "
            "Не читай файлы вне списка; если находка ссылается на внешний файл — "
            "помечай как `[вне области]` и переходи дальше.\n\n"
            + "\n".join(f"- `{s}`" for s in scopes)
        )
        result = result + block
    # Контракт test_build_audit_prompt_empty_template: пустой шаблон → пустой
    # промт. Правило безопасности добавляем только к непустому промту —
    # без audit_standard.md аудит всё равно не запустится (caller проверит).
    if result:
        result = result + _AUDIT_SCOPE_RULE
    return result
