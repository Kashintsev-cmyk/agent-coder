"""Инструмент: запись файла."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from agent_coder.tools._scope import in_scope
from agent_coder.tools.registry import (
    Tool,
    ToolContext,
    ToolExecutionResult,
    ToolStatus,
)


def _error(output: str) -> ToolExecutionResult:
    return ToolExecutionResult(output, ToolStatus.ERROR)


def _rejected(output: str) -> ToolExecutionResult:
    return ToolExecutionResult(output, ToolStatus.REJECTED)


async def _execute_result(
    args: dict[str, Any], ctx: ToolContext
) -> ToolExecutionResult:
    rel = args.get("path", "").strip()
    content = args.get("content", "")
    if not rel:
        return _error("Ошибка: не указан path")

    base = Path(ctx.workspace).resolve()
    full = (base / rel).resolve()
    try:
        full.relative_to(base)
    except ValueError:
        return _rejected(f"Ошибка: путь вне workspace: {rel}")

    # B1b (8f0cd848): scope-gate и для записи. `..`-bypass закрыт M2 в `in_scope`.
    if not in_scope(rel, ctx.audit_scopes):
        return _rejected(
            f"Ошибка: {rel!r} вне области аудита {ctx.audit_scopes}. "
            f"Пиши только в разрешённые пути."
        )

    existed = full.exists()
    try:
        before = full.read_bytes() if existed else None
        full.parent.mkdir(parents=True, exist_ok=True)
        full.write_text(content, encoding="utf-8")
    except Exception as e:
        return _error(f"Ошибка записи: {e}")

    lines = content.count("\n") + (1 if content else 0)
    verb = "перезаписан" if existed else "создан"
    output = f"Файл {verb}: {rel} ({lines} строк, {len(content)} байт)"
    changed = before != full.read_bytes()
    return ToolExecutionResult(
        output=output,
        status=(
            ToolStatus.SUCCESS_CHANGED if changed else ToolStatus.SUCCESS_NO_CHANGE
        ),
        changed_paths=(rel,) if changed else (),
    )


async def _execute(args: dict[str, Any], ctx: ToolContext) -> str | ToolExecutionResult:
    """Compatibility wrapper for callers expecting legacy error strings."""
    result = await _execute_result(args, ctx)
    if result.status in (ToolStatus.ERROR, ToolStatus.REJECTED):
        return result.output
    return result


write_file_tool = Tool(
    name="write_file",
    description=(
        "Создать новый файл или полностью перезаписать существующий. "
        "Для точечных правок используй edit_file (будет в следующей итерации)."
    ),
    parameters={
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "Путь относительно workspace"},
            "content": {"type": "string", "description": "Полное содержимое файла"},
        },
        "required": ["path", "content"],
    },
    executor=_execute_result,
    requires_approval=False,
    read_only=False,
)
