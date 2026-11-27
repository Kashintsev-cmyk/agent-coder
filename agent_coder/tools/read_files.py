"""read_files: пакетное чтение нескольких файлов за один вызов инструмента.

Не новая логика чтения: каждый путь проходит через read_file._execute, поэтому
scope аудита, отказ от полного перечитывания и лимиты строк/байт наследуются.
Экономия — один round-trip модели вместо N ходов.
"""

from __future__ import annotations

from typing import Any

from agent_coder.agent.work_state import make_read_digest
from agent_coder.tools.read_file import _execute_result as _read_one
from agent_coder.tools.registry import (
    Tool,
    ToolContext,
    ToolExecutionResult,
    ToolStatus,
)

# Максимум путей за один вызов.
MAX_BATCH_FILES = 10
# Общий кап ответа на вызов: сверх него оставшиеся пути не читаются.
MAX_TOTAL_BYTES = 120_000


async def _execute_result(
    args: dict[str, Any], ctx: ToolContext
) -> ToolExecutionResult:
    raw = args.get("paths")
    if not isinstance(raw, list):
        return ToolExecutionResult(
            "Ошибка: paths должен быть списком путей", ToolStatus.ERROR
        )
    paths = [str(p).strip() for p in raw if str(p).strip()]
    if not paths:
        return ToolExecutionResult(
            "Ошибка: не указан ни один путь в paths", ToolStatus.ERROR
        )
    if len(paths) > MAX_BATCH_FILES:
        return ToolExecutionResult(
            f"Слишком много путей ({len(paths)}). "
            f"Максимум {MAX_BATCH_FILES} за вызов — разбей на несколько вызовов.",
            ToolStatus.ERROR,
        )

    parts: list[str] = []
    used = 0
    status = ToolStatus.SUCCESS_NO_CHANGE
    for i, rel in enumerate(paths):
        result = await _read_one({"path": rel}, ctx)
        output = result.output
        size = len(output.encode("utf-8", errors="replace"))
        if used + size > MAX_TOTAL_BYTES:
            skipped = ", ".join(paths[i:])
            parts.append(
                f"… лимит {MAX_TOTAL_BYTES} байт на вызов: не прочитаны {skipped}. "
                f"Запроси их отдельным вызовом."
            )
            break
        used += size
        parts.append(output)
        if result.status == ToolStatus.ERROR:
            status = ToolStatus.ERROR
        elif result.status == ToolStatus.REJECTED and status != ToolStatus.ERROR:
            status = ToolStatus.REJECTED
        # Digest пишем только для успешных результатов, не по текстовому префиксу.
        if ctx.read_paths is not None and result.status == ToolStatus.SUCCESS_NO_CHANGE:
            digest = make_read_digest(rel, output)
            if digest:
                ctx.read_paths[rel] = digest
    return ToolExecutionResult("\n\n".join(parts), status)


async def _execute(args: dict[str, Any], ctx: ToolContext) -> str:
    """String-output compatibility wrapper for direct callers and tests."""
    return (await _execute_result(args, ctx)).output


read_files_tool = Tool(
    name="read_files",
    description=(
        "Прочитать несколько файлов из рабочей директории за один вызов. "
        "Каждый путь — как у read_file (с нумерацией строк). "
        f"Не более {MAX_BATCH_FILES} путей за вызов. Для больших файлов "
        "используй read_file с offset/limit."
    ),
    parameters={
        "type": "object",
        "properties": {
            "paths": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Пути относительно workspace",
            },
        },
        "required": ["paths"],
    },
    executor=_execute_result,
    read_only=True,
)
