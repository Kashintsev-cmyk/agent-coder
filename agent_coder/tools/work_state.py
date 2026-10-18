"""Инструмент: точечное обновление work_state (structured working memory).

Модель вызывает update_work_state, чтобы зафиксировать факты, которые
должны пережить сжатие истории: что уже найдено/сделано, что осталось.
Patch — не перезапись, а точечные операции (см. WorkStateHandle.update):

  {"findings+": ["путь src/x.py", "тест падает: ..."]}   — дописать
  {"todo": ["починить x"]}                               — заменить поле
  {"done-": ["старый факт"]}                             — удалить
"""

from __future__ import annotations

from typing import Any

from agent_coder.tools.registry import Tool, ToolContext


def _report(before: dict[str, list[str]], after: dict[str, list[str]]) -> str:
    parts: list[str] = []
    for field_name, value in after.items():
        b = len(before.get(field_name, []))
        a = len(value)
        if a != b:
            delta = a - b
            sign = "+" if delta > 0 else ""
            parts.append(f"{field_name}: {b}->{a} ({sign}{delta})")
    if not parts:
        return "work_state без изменений (patch не затронул ни одного поля)"
    return "work_state обновлён: " + ", ".join(parts)


async def _execute(args: dict[str, Any], ctx: ToolContext) -> str:
    if ctx.work_state is None:
        return (
            "Ошибка: work_state недоступен в этом контексте (tool вызван вне AgentLoop)"
        )
    patch = args.get("patch")
    if not isinstance(patch, dict) or not patch:
        return (
            "Ошибка: patch должен быть непустым объектом вида "
            '{"findings+": ["..."], "todo": ["..."]}'
        )
    before = ctx.work_state.get()
    after = ctx.work_state.update(patch)
    return _report(before, after)


update_work_state_tool = Tool(
    name="update_work_state",
    description=(
        "Зафиксировать в структурированной рабочей памяти сессии то, что должно "
        "пережить сжатие истории: найденные факты (пути, ошибки, результаты "
        "тестов), просьбы пользователя, сделанное и оставшееся. Вызывай после "
        "каждого значимого шага (нашёл причину, починил, прогнал тесты). "
        "Patch — точечный: 'field' заменяет поле, 'field+' дописывает, "
        "'field-' удаляет. Поля: user_requests, findings, done, todo, "
        "open_questions."
    ),
    parameters={
        "type": "object",
        "properties": {
            "patch": {
                "type": "object",
                "description": (
                    "Точечный патч: {'field': [...]} заменяет, "
                    "{'field+': [...]} дописывает, {'field-': [...]} удаляет. "
                    "Поля: user_requests, findings, done, todo, open_questions."
                ),
                "additionalProperties": {
                    "type": "array",
                    "items": {"type": "string"},
                },
            }
        },
        "required": ["patch"],
    },
    executor=_execute,
    requires_approval=False,
    # read_only=True по конвенции проекта: tool не трогает workspace
    # (меняет только память сессии, обратимо через 'field-'), поэтому
    # доступен в PLAN/AUDIT — structured memory нужна планировщику.
    read_only=True,
)
