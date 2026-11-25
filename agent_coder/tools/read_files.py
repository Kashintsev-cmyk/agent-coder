"""read_files: пакетное чтение нескольких файлов за один вызов инструмента.

Не новая логика чтения: каждый путь проходит через read_file._execute, поэтому
scope аудита, отказ от полного перечитывания и лимиты строк/байт наследуются.
Экономия — один round-trip модели вместо N ходов.
"""

from __future__ import annotations

from typing import Any

from agent_coder.agent.work_state import make_read_digest
from agent_coder.tools.read_file import _execute as _read_one
from agent_coder.tools.registry import Tool, ToolContext

# Максимум путей за один вызов.
MAX_BATCH_FILES = 10
# Общий кап ответа на вызов: сверх него оставшиеся пути не читаются.
MAX_TOTAL_BYTES = 120_000


async def _execute(args: dict[str, Any], ctx: ToolContext) -> str:
    raw = args.get("paths")
    if not isinstance(raw, list):
        return "Ошибка: paths должен быть списком путей"
    paths = [str(p).strip() for p in raw if str(p).strip()]
    if not paths:
        return "Ошибка: не указан ни один путь в paths"
    if len(paths) > MAX_BATCH_FILES:
        return (
            f"Слишком много путей ({len(paths)}). "
            f"Максимум {MAX_BATCH_FILES} за вызов — разбей на несколько вызовов."
        )

    parts: list[str] = []
    used = 0
    for i, rel in enumerate(paths):
        result = await _read_one({"path": rel}, ctx)
        size = len(result.encode("utf-8", errors="replace"))
        if used + size > MAX_TOTAL_BYTES:
            skipped = ", ".join(paths[i:])
            parts.append(
                f"… лимит {MAX_TOTAL_BYTES} байт на вызов: не прочитаны {skipped}. "
                f"Запроси их отдельным вызовом."
            )
            break
        used += size
        parts.append(result)
        # Digest пишем сами: loop фиксирует read_paths только для read_file.
        # Только для успешного чтения: отказы (scope, «уже прочитан») не пишем.
        if ctx.read_paths is not None and result.startswith(f"{rel} (строки"):
            digest = make_read_digest(rel, result)
            if digest:
                ctx.read_paths[rel] = digest
    return "\n\n".join(parts)


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
    executor=_execute,
    read_only=True,
)
