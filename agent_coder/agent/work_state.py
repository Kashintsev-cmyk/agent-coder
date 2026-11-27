"""Структурированная рабочая память сессии (work_state).

Проблема, которую решает: при LLM-сжатии истории (compression) детали
теряются — модель «забывает», что уже нашла/сделала, и ищет заново.
work_state — детерминированный (не пересуммаризуемый LLM) набор фактов,
который инжектится в динамический контекст запроса и служит скелетом
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
import re
from typing import Any

logger = logging.getLogger(__name__)

# Порядок важен: поля выводятся в динамический prompt и скелет резюме.
FIELDS: tuple[str, ...] = (
    "user_requests",
    "findings",
    "done",
    "todo",
    "open_questions",
)

# SSOT dynamic-context policy shared by PLAN/ACT/AUDIT. State stays complete
# in Session; these profiles only control its prompt projection and priority.
PROMPT_FIELDS_BY_MODE: dict[str, tuple[str, ...]] = {
    "plan": ("user_requests", "findings", "todo", "open_questions"),
    "act": FIELDS,
    "audit": ("user_requests", "findings", "open_questions", "todo"),
}
PROMPT_CONTEXT_MAX_CHARS = 12_000
PROMPT_CONTEXT_WORK_STATE_CHARS = int(PROMPT_CONTEXT_MAX_CHARS * 0.70)
PROMPT_CONTEXT_READ_PATHS_CHARS = (
    PROMPT_CONTEXT_MAX_CHARS - PROMPT_CONTEXT_WORK_STATE_CHARS - 2
)

# Разумные потолки, чтобы state не разросся и не съел весь контекст.
MAX_ITEMS_PER_FIELD = 50
MAX_ITEM_CHARS = 500
# R11: findings копятся на диске (findings.md), в state НЕ режем — иначе
# save() diff не видит вытесненных cap'ом и findings.md пухнет (60 vs 50).
# В prompt — только первые MAX_FINDINGS_IN_PROMPT.
MAX_FINDINGS_IN_PROMPT = 100

# B9.4: дедуп findings по «файл:строка». Модель пишет «services/pricing.py:13»
# и «pricing.py:13» — это один finding с разными префиксами; без дедупа
# state раздувается (f0fbbe9a: 25 findings с ~11 дублями). Префиксы
# strip'аются ТОЛЬКО для сравнения — в state хранится исходная строка.
_FILE_LINE_RE = re.compile(r"(\S+\.\w+):(\d+)")
_DEDUP_DIR_PREFIXES: tuple[str, ...] = ("services/", "app/", "src/", "lib/", "core/")
_SEVERITIES: tuple[str, ...] = ("BLOCKER", "MAJOR", "MINOR")


def _dedup_enabled() -> bool:
    """Флаг WORK_STATE_DEDUP (default True). Lazy import — без цикла.

    В agent_coder.config инстанс называется `config = Config()`
    (см. конец файла). Поля класса на модуле не видны — читаем инстанс.
    """
    try:
        from agent_coder.config import config as _cfg

        return bool(getattr(_cfg, "WORK_STATE_DEDUP", True))
    except Exception:
        logger.debug(
            "work_state: не удалось прочитать WORK_STATE_DEDUP",
            exc_info=True,
        )
        return True


def _extract_file_line(item: str) -> tuple[str, str] | None:
    """(нормализованный путь, номер строки) или None, если ключа нет."""
    m = _FILE_LINE_RE.search(item)
    if not m:
        return None
    path = m.group(1)
    for prefix in _DEDUP_DIR_PREFIXES:
        if path.startswith(prefix):
            path = path[len(prefix) :]
            break
    return (path, m.group(2))


def _has_severity(item: str) -> bool:
    return any(sev in item for sev in _SEVERITIES)


def _is_more_informative(a: str, b: str) -> bool:
    """a лучше b: severity > длина. При равенстве — a (стабильность)."""
    a_sev = _has_severity(a)
    b_sev = _has_severity(b)
    if a_sev != b_sev:
        return a_sev
    return len(a) >= len(b)


def _dedup_findings(items: list[str]) -> list[str]:
    """Схлопнуть findings по ключу (норм. путь, строка).

    Findings без «файл:строка» не трогаем — это общие замечания
    (ruff format, тесты), они не дублируют друг друга по определению.
    """
    result: list[str] = []
    by_key: dict[tuple[str, str], int] = {}
    for item in items:
        key = _extract_file_line(item)
        if key is None:
            result.append(item)
            continue
        idx = by_key.get(key)
        if idx is None:
            by_key[key] = len(result)
            result.append(item)
        elif _is_more_informative(item, result[idx]):
            result[idx] = item
    return result


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
        # R11: findings — без cap в state (см. MAX_FINDINGS_IN_PROMPT).
        limit = None if field_name == "findings" else MAX_ITEMS_PER_FIELD
        for it in raw:
            s = str(it).strip()
            if not s or s in seen:
                continue
            if len(s) > MAX_ITEM_CHARS:
                s = s[:MAX_ITEM_CHARS] + "…"
            items.append(s)
            seen.add(s)
            if limit is not None and len(items) >= limit:
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
        # B9.4: дополнительный дедуп findings по (файл:строка).
        if _dedup_enabled():
            normalized["findings"] = _dedup_findings(normalized["findings"])
        # Пишем в живой dict сессии (in-place, чтобы ссылка осталась валидной).
        self._state.clear()
        self._state.update(normalized)
        if ignored:
            logger.debug("work_state.update: проигнорированы ключи %r", ignored)
        return normalized


def render_for_prompt(
    state: dict[str, Any] | None,
    *,
    mode: object | None = None,
    max_chars: int | None = None,
) -> str:
    """Render work_state, optionally using a mode projection and size budget.

    No mode and no budget preserve the full canonical rendering used by
    compression, verification, and patch summaries.
    """
    mode_value = str(getattr(mode, "value", mode)) if mode is not None else ""
    fields = PROMPT_FIELDS_BY_MODE.get(mode_value, FIELDS)
    return _render_work_state_projection(state, fields, max_chars=max_chars)


MAX_READ_PATHS_IN_PROMPT = 60


def make_read_digest(path: str, result: str) -> str | None:
    """Краткий digest прочитанного файла: header + def/class/import/from.

    Возвращает None для ошибок (не записываем в read_paths). Ограничен
    ~200 символами: это подсказка «что в файле», не замена чтению.
    """
    if not result or not result.strip():
        return None
    first = result.splitlines()[0] if result.splitlines() else ""
    bad_prefixes = (
        "Ошибка",
        "Файл не найден",
        "Не файл",
        "Файл слишком большой",
        "Не найдено",
    )
    if first.startswith(bad_prefixes):
        return None
    defs: list[str] = []
    for line in result.splitlines()[1:]:
        idx = line.find("│ ")
        body = line[idx + 2 :] if idx >= 0 else line
        for prefix in ("def ", "class ", "import ", "from "):
            if body.startswith(prefix):
                defs.append(body.strip())
                break
        if len(defs) >= 8:
            break
    header = first if len(first) <= 120 else first[:117] + "…"
    digest = header + " | " + "; ".join(defs[:8]) if defs else header
    return digest[:200]


def render_prompt_context(
    state: dict[str, Any] | None,
    read_paths: dict[str, str] | None,
    mode: object = "act",
) -> str:
    """Render the canonical, mode-aware, bounded dynamic prompt context.

    This is the SSOT for field projection and prompt-size budgets. The stored
    work_state/read_paths remain complete; only this presentation is bounded.
    """
    state_budget = PROMPT_CONTEXT_WORK_STATE_CHARS
    paths_budget = PROMPT_CONTEXT_READ_PATHS_CHARS
    work_state = render_for_prompt(state, mode=mode, max_chars=state_budget)
    read_path_block = render_read_paths(read_paths, max_chars=paths_budget)

    if not work_state and read_path_block:
        read_path_block = render_read_paths(
            read_paths, max_chars=PROMPT_CONTEXT_MAX_CHARS
        )
    elif work_state and not read_path_block:
        work_state = render_for_prompt(
            state, mode=mode, max_chars=PROMPT_CONTEXT_MAX_CHARS
        )
    elif work_state and read_path_block:
        unused_state = max(0, state_budget - len(work_state))
        if unused_state:
            paths_budget += unused_state
            read_path_block = render_read_paths(read_paths, max_chars=paths_budget)
        unused_paths = max(0, PROMPT_CONTEXT_READ_PATHS_CHARS - len(read_path_block))
        if unused_paths:
            work_state = render_for_prompt(
                state,
                mode=mode,
                max_chars=min(
                    PROMPT_CONTEXT_MAX_CHARS - len(read_path_block) - 2,
                    state_budget + unused_paths,
                ),
            )

    parts = [part for part in (work_state, read_path_block) if part]
    return "\n\n".join(parts)


def _render_work_state_projection(
    state: dict[str, Any] | None,
    fields: tuple[str, ...],
    *,
    max_chars: int | None = None,
) -> str:
    norm = normalize(state)
    titles = {
        "user_requests": "Просьбы пользователя",
        "findings": "Установленные факты",
        "done": "Сделано",
        "todo": "Осталось",
        "open_questions": "Открытые вопросы",
    }
    sections: list[tuple[str, list[str]]] = []
    for field_name in fields:
        items = norm[field_name]
        if field_name == "findings" and _dedup_enabled():
            items = _dedup_findings(items)
        if not items:
            continue
        hidden = 0
        if field_name == "findings" and len(items) > MAX_FINDINGS_IN_PROMPT:
            hidden = len(items) - MAX_FINDINGS_IN_PROMPT
            items = items[:MAX_FINDINGS_IN_PROMPT]
        entries = [f"- {item}" for item in items]
        if hidden:
            entries.append(f"…ещё {hidden} скрыто (полный список в findings.md)")
        sections.append((titles[field_name], entries))
    if not sections:
        return ""

    header = "[WORK_STATE] — что уже установлено (обновляй через update_work_state):"

    def render(selected: list[list[str]]) -> str:
        lines = [header]
        for (title, _), entries in zip(sections, selected, strict=True):
            if entries:
                lines.append(f"\n{title}:")
                lines.extend(entries)
        return "\n".join(lines)

    complete = render([entries.copy() for _, entries in sections])
    if max_chars is None or len(complete) <= max_chars:
        return complete

    selected = [[entries[0]] for _, entries in sections]
    text = render(selected)
    marker = "…WORK_STATE сокращён по лимиту prompt"
    suffix = "\n" + marker
    if len(text) + len(suffix) > max_chars:
        return _limit_prompt_text(text, max_chars, marker)

    indexes = [1] * len(sections)
    while True:
        added = False
        for section_index, (_, entries) in enumerate(sections):
            item_index = indexes[section_index]
            if item_index >= len(entries):
                continue
            selected[section_index].append(entries[item_index])
            candidate = render(selected)
            if len(candidate) + len(suffix) <= max_chars:
                indexes[section_index] += 1
                added = True
            else:
                selected[section_index].pop()
                indexes[section_index] = len(entries)
        if not added:
            break

    return render(selected) + suffix


def render_read_paths(
    read_paths: dict[str, str] | None,
    *,
    max_chars: int | None = None,
) -> str:
    """Render recent file digests; bounded rendering retains newest paths."""
    if not read_paths:
        return ""
    items = list(read_paths.items())[-MAX_READ_PATHS_IN_PROMPT:]
    if not items:
        return ""
    header = (
        "[ПРОЧИТАННЫЕ ФАЙЛЫ] — уже читал в этой сессии. "
        "НЕ ПЕРЕЧИТЫВАЙ целиком: read_file по ним отвергается. "
        "Детали — read_file с offset/limit или search_history:"
    )
    hidden_by_count = len(read_paths) - len(items)
    lines = [header, *(f"- {path}: {digest}" for path, digest in items)]
    if hidden_by_count:
        lines.append(f"…ещё {hidden_by_count} файлов скрыто (см. search_history)")
    text = "\n".join(lines)
    if max_chars is None or len(text) <= max_chars:
        return text

    selected: list[str] = []
    for path, digest in reversed(items):
        line = f"- {path}: {digest}"
        omitted = len(read_paths) - len(selected) - 1
        marker = f"…ещё {omitted} файлов скрыто или сокращено (см. search_history)"
        candidate = "\n".join((header, *reversed([*selected, line]), marker))
        if len(candidate) > max_chars:
            break
        selected.append(line)

    if not selected:
        omitted = len(read_paths)
        marker = f"…ещё {omitted} файлов скрыто или сокращено (см. search_history)"
        room = max_chars - len(header) - len(marker) - 2
        if room > 1:
            path, digest = items[-1]
            selected = [f"- {path}: {digest}"[: room - 1].rstrip() + "…"]
    omitted = len(read_paths) - len(selected)
    marker = f"…ещё {omitted} файлов скрыто или сокращено (см. search_history)"
    bounded = "\n".join((header, *reversed(selected), marker))
    if len(bounded) <= max_chars:
        return bounded
    return _limit_prompt_text(
        bounded,
        max_chars,
        "… список прочитанных файлов сокращён (см. search_history)",
    )


def _limit_prompt_text(text: str, max_chars: int, marker: str) -> str:
    if len(text) <= max_chars:
        return text
    if max_chars <= len(marker):
        return marker[:max_chars]
    suffix = "\n" + marker
    prefix_limit = max_chars - len(suffix)
    prefix = text[:prefix_limit]
    if "\n" in prefix:
        prefix = prefix.rsplit("\n", 1)[0]
    return prefix.rstrip() + suffix
