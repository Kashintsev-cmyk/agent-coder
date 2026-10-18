"""Структурированная рабочая память сессии (work_state).

Проблема, которую решает: при LLM-сжатии истории (compression) детали
теряются — модель «забывает», что уже нашла/сделала, и ищет заново.
work_state — детерминированный (не пересуммаризуемый LLM) набор фактов,
который инжектится в system prompt каждый запрос и служит скелетом
резюме при compress().

Поля (все — списки строк, чтобы модель дописывала, не перезаписывала):
- findings: что уже установлено (пути, ошибки, результаты тестов);
- user_requests: явные просьбы пользователя;
- done: что уже сделано;
- todo: что осталось;
- open_questions: что не выяснено.

WorkStateHandle — узкий контракт для tool'а update_work_state:
  get() -> dict                     — копия (tool не мутирует напрямую)
  update(patch: dict) -> dict       — точечное обновление, возвращает новый state

Формат patch (совместим с Anthropic/Claude Code todo-паттерном):
  {"findings": [...]}    — заменить поле целиком;
  {"findings+": [...]}   — дописать в конец (дедуп);
  {"findings-": [...]}   — удалить указанные строки;
  несколько полей в одном patch — применяются последовательно.
Неизвестные поля — игнорируются с записью в ignored (не падаем).
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)

# Порядок важен: так поля выводятся в system prompt и в скелет резюме.
FIELDS: tuple[str, ...] = (
    "user_requests",
    "findings",
    "done",
    "todo",
    "open_questions",
)

# Разумные потолки, чтобы state не разросся и не съел весь контекст.
MAX_ITEMS_PER_FIELD = 50
MAX_ITEM_CHARS = 500


def normalize(state: dict[str, Any] | None) -> dict[str, list[str]]:
    """Привести произвольный dict к каноническому виду {field: list[str]}.

    - отсутствующие поля → [];
    - не-list значения → [str(value)] (на случай ручных правок JSON);
    - строки обрезаются до MAX_ITEM_CHARS, список — до MAX_ITEMS_PER_FIELD.
    """
    out: dict[str, list[str]] = {}
    src = state or {}
    for field_name in FIELDS:
        raw = src.get(field_name) or []
        if isinstance(raw, str):
            raw = [raw]
        items: list[str] = []
        seen: set[str] = set()
        for it in raw:
            s = str(it).strip()
            if not s or s in seen:
                continue
            if len(s) > MAX_ITEM_CHARS:
                s = s[:MAX_ITEM_CHARS] + "…"
            items.append(s)
            seen.add(s)
            if len(items) >= MAX_ITEMS_PER_FIELD:
                break
        out[field_name] = items
    return out


class WorkStateHandle:
    """Узкий handle над живым session.work_state (не копия).

    Tool видит только work_state, не всю Session (по образцу Cline TaskState /
    Codex ToolInvocation). Loop держит один экземпляр на итерацию и передаёт
    его и в ToolContext, и в system_prompt().
    """

    def __init__(self, state: dict[str, Any]) -> None:
        # Ссылка на живой dict сессии — изменения видны сразу.
        self._state = state

    def get(self) -> dict[str, list[str]]:
        """Копия канонического state (tool не должен мутировать напрямую)."""
        return normalize(self._state)

    def is_empty(self) -> bool:
        return not any(self.get().values())

    def update(self, patch: dict[str, Any]) -> dict[str, list[str]]:
        """Точечное обновление. Возвращает новый канонический state.

        Ключи patch:
          "<field>"  — заменить поле;
          "<field>+" — дописать (дедуп сохраняется);
          "<field>-" — удалить перечисленные строки.
        """
        if not isinstance(patch, dict):
            logger.warning("work_state.update: patch не dict (%r)", type(patch))
            return self.get()

        current = normalize(self._state)
        ignored: list[str] = []

        for key, value in patch.items():
            if not isinstance(key, str):
                ignored.append(repr(key))
                continue
            op = "set"
            field_name = key
            if key.endswith("+"):
                op, field_name = "add", key[:-1]
            elif key.endswith("-"):
                op, field_name = "remove", key[:-1]
            if field_name not in FIELDS:
                ignored.append(key)
                continue
            raw = value if isinstance(value, list) else [value]
            new_items = [str(v).strip() for v in raw if str(v).strip()]
            if op == "set":
                current[field_name] = new_items
            elif op == "add":
                current[field_name] = current[field_name] + new_items
            else:  # remove
                drop = set(new_items)
                current[field_name] = [
                    it for it in current[field_name] if it not in drop
                ]

        # Финальная нормализация: дедуп/лимиты/обрезка.
        normalized = normalize(current)
        # Пишем в живой dict сессии (in-place, чтобы ссылка осталась валидной).
        self._state.clear()
        self._state.update(normalized)
        if ignored:
            logger.debug("work_state.update: проигнорированы ключи %r", ignored)
        return normalized


def render_for_prompt(state: dict[str, Any] | None) -> str:
    """Блок [WORK_STATE] для system prompt. Пустой state → пустая строка.

    Пустой блок не выводим: нечего модели видеть, лишние токены.
    """
    norm = normalize(state)
    if not any(norm.values()):
        return ""
    lines = ["[WORK_STATE] — что уже установлено (обновляй через update_work_state):"]
    titles = {
        "user_requests": "Просьбы пользователя",
        "findings": "Установленные факты",
        "done": "Сделано",
        "todo": "Осталось",
        "open_questions": "Открытые вопросы",
    }
    for field_name in FIELDS:
        items = norm[field_name]
        if not items:
            continue
        lines.append(f"\n{titles[field_name]}:")
        for it in items:
            lines.append(f"- {it}")
    return "\n".join(lines)
