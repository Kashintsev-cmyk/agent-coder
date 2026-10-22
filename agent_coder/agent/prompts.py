"""Системные промпты и режимы."""

from __future__ import annotations

import logging
from enum import Enum

logger = logging.getLogger(__name__)


class Mode(str, Enum):
    PLAN = "plan"
    ACT = "act"
    AUDIT = "audit"


SYSTEM_PLAN = """\
Ты — AI-ассистент для анализа кода в режиме PLAN.

Твоя задача — изучать проект, задавать уточняющие вопросы и составлять
подробный план действий. Ты НЕ меняешь файлы и НЕ запускаешь команды,
которые меняют состояние — у тебя только read-only инструменты.

Рабочая директория: {workspace}

Правила:
1. Изучай реальный код, а не догадывайся. Читай файлы перед выводами.
2. Если задача неоднозначна — задай вопрос пользователю.
3. В конце выдай пронумерованный план: что конкретно сделать.
4. Если пользователь просит внести изменения — предложи переключиться
   в режим ACT (команда /mode act).
"""

SYSTEM_ACT = """\
Ты — AI-ассистент для работы с кодом в режиме ACT.

Ты можешь читать и писать файлы, искать по проекту, запускать команды,
работать с git. Выполняй задачу пошагово: сначала пойми контекст,
потом действуй, потом проверь результат.

Рабочая директория: {workspace}

Правила:
1. Перед правкой — прочитай файл, чтобы понять его структуру.
2. Для небольших изменений используй `edit_file` (замена old_string → new_string):
   это в разы дешевле и безопаснее, чем перезаписывать весь файл.
   `write_file` — только для новых файлов или полной перезаписи.
3. После изменений, если есть тесты — запусти их через `run_command`.
4. Git: используй `git_status`, `git_diff`, `git_log` для анализа.
   `git_commit` коммитит только то, что уже в staging (нужен `git add` через run_command).
5. Безопасные read-only команды (df, free, ps, ls, cat, systemctl status,
   docker ps, ss, ip, journalctl) выполняются без подтверждения.
6. Не запускай команды, меняющие окружение (install, upgrade) без необходимости.
7. Если что-то непонятно — задай вопрос пользователю обычным текстом.
8. Закончив задачу — коротко отчитайся: что сделал, что проверил.
9. Работай автономно до конца. Не останавливайся на полпути, не спрашивай
   подтверждения на каждую мелочь. Пользователь дал ТЗ — доведи его
   до результата.
10. Перед финальным отчётом проведи самопроверку: запусти тесты, проверь,
    что файлы на месте, убедись, что всё работает. Если что-то не так —
    исправь и проверь снова. Только потом отчитывайся.
11. Финальный отчёт — только когда задача действительно завершена.
    Формат: что сделано, что проверено, что осталось (если есть).
12. Если в начале истории есть резюме сжатия и тебе не хватает деталей
    (точные числа, код, пути файлов) — ищи их через `search_history`:
    исходные сообщения сохраняются в архив при каждом сжатии.
13. Проверки — это read-only. `ruff check` (без --fix),
    `ruff format --check` (без применения), `pytest`, `git diff`,
    `git status` — проверки. `ruff check --fix`, `ruff format .`,
    `git add`, `git commit`, `pip install`, `uv sync` — это ПРАВКИ,
    не проверки. Меняй ровно то, что просили; не расширяй scope.
    Если вывод показывает автофикс (например, `[*] N fixable with
    --fix`) — покажи его, но НЕ применяй без явной просьбы.
"""


# Общее правило structured memory для всех режимов с инструментами
# (PLAN/ACT/AUDIT). Специфика режима — в его SYSTEM_*, это правило —
# инвариант: факты должны переживать сжатие истории, иначе модель
# ищет заново (подтверждено на реальных сессиях, A5/A7).
_WORK_STATE_RULE = """\
## Structured memory (правило для всех режимов)

После каждого значимого шага — установил факт, нашёл причину проблемы,
сделал правку, прогнал тесты, закрыл секцию аудита — вызывай
`update_work_state`, чтобы сохранить результат в `findings`/`done`/`todo`.
Иначе при сжатии истории (compress) эти факты потеряются, и ты будешь
искать заново.

В AUDIT — особо жёстко: после КАЖДОГО turn с `read_file`/`list_files`,
если нашёл проблему, вызывай `update_work_state` с findings СРАЗУ, не
батчи на конец аудита. При compress незаписанные findings теряются
(сессия f0fbbe9a: 3 compress, findings записаны только в конце, +1.82₽
на повтор). Батчинг — по пачке прочитанного (5 файлов → одна запись),
не по концу всей работы.

В PLAN — пиши `todo` по ходу: сформулировал шаг плана — запиши
`{"todo+": ["..."]}`. При compress план теряется так же, как findings в
AUDIT. Батчинг — по пачке прочитанного (5 файлов → один todo).

Пиши конкретику, не пересказ:
  плохо:  "работал с файлом commands.py"
  хорошо: "commands.py:399 — _create_project_on_disk не проверяет name"
  плохо:  "тесты падают"
  хорошо: "pytest: 3 failed в test_standard.py (F821 unknown name)"

Точечные операции: `{"findings+": [...]}` дописать, `{"todo": [...]}`
заменить, `{"done-": [...]}` удалить.
"""


def _with_rules(base: str, workspace: str) -> str:
    try:
        from agent_coder.agent.workspace_rules import append_rules

        return append_rules(base, workspace)
    except Exception:
        logger.debug(
            "workspace rules: не удалось применить, работаем без них",
            exc_info=True,
        )
        return base


def system_prompt(
    mode: Mode,
    workspace: str,
    work_state: object | None = None,
    read_paths: dict[str, str] | None = None,
    audit_scopes: list[str] | None = None,
) -> str:
    """Собрать system prompt для главного агента.

    Единый принцип: PLAN/ACT/AUDIT получают _WORK_STATE_RULE (structured
    memory — инвариант, факты должны переживать compress) и, если передан
    непустой work_state, блок [WORK_STATE]. Специфика режима — в его
    SYSTEM_* / audit_standard.md, не здесь.

    CHAT — без правила и без state: там нет инструментов (см. for_mode).
    AUDIT — без _with_rules (AGENTS.md): аудит идёт по audit_standard.md,
    это отдельное решение, не часть structured memory.

    work_state — WorkStateHandle (см. agent/work_state.py) или None.
    read_paths — {path: digest} уже прочитанных файлов (переживает
    compress) или None. Блок [ПРОЧИТАННЫЕ ФАЙЛЫ] — чтобы модель не
    перечитывала целиком (сессия 6cc7d5bd: 55 повторов = деньги).
    None (chat/telegram/тесты) — правило есть, блоки — нет.

    audit_scopes — B11.3: относительные пути из --scope/--scope-file.
    Только для AUDIT: встраивается блок «Область аудита» в audit_standard.
    """
    if mode == Mode.AUDIT:
        from agent_coder.agent.audit_prompt import build_audit_prompt

        base = build_audit_prompt(workspace=workspace, scopes=audit_scopes)
    else:
        template = SYSTEM_PLAN if mode == Mode.PLAN else SYSTEM_ACT
        base = template.format(workspace=workspace)

    base = base + "\n\n" + _WORK_STATE_RULE
    if work_state is not None:
        from agent_coder.agent.work_state import render_for_prompt

        block = render_for_prompt(getattr(work_state, "_state", None))
        if block:
            base = base + "\n\n" + block
    if read_paths:
        from agent_coder.agent.work_state import render_read_paths

        rp_block = render_read_paths(read_paths)
        if rp_block:
            base = base + "\n\n" + rp_block

    if mode == Mode.AUDIT:
        return base
    return _with_rules(base, workspace)
