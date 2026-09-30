"""Инструмент: запись файла."""
from __future__ import annotations

from pathlib import Path
from typing import Any

from agent_coder.tools.registry import Tool, ToolContext


async def _execute(args: dict[str, Any], ctx: ToolContext) -> str:
    rel = args.get("path", "").strip()
    content = args.get("content", "")
    if not rel:
        return "Ошибка: не указан path"

    base = Path(ctx.workspace).resolve()
    full = (base / rel).resolve()
    try:
        full.relative_to(base)
    except ValueError:
        return f"Ошибка: путь вне workspace: {rel}"

    existed = full.exists()
    try:
        full.parent.mkdir(parents=True, exist_ok=True)
        full.write_text(content, encoding="utf-8")
    except Exception as e:
        return f"Ошибка записи: {e}"

    lines = content.count("\n") + (1 if content else 0)
    verb = "перезаписан" if existed else "создан"
    return f"Файл {verb}: {rel} ({lines} строк, {len(content)} байт)"


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
    executor=_execute,
    requires_approval=False,
    read_only=False,
)