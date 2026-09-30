"""Инструмент: чтение файла."""
from __future__ import annotations

from pathlib import Path
from typing import Any

from agent_coder.tools.registry import Tool, ToolContext

MAX_BYTES = 200_000


async def _execute(args: dict[str, Any], ctx: ToolContext) -> str:
    rel = args.get("path", "").strip()
    if not rel:
        return "Ошибка: не указан path"

    full = _resolve(ctx.workspace, rel)
    if full is None:
        return f"Ошибка: путь вне рабочей директории: {rel}"

    if not full.exists():
        return f"Файл не найден: {rel}"
    if not full.is_file():
        return f"Не файл: {rel}"

    size = full.stat().st_size
    if size > MAX_BYTES:
        return (
            f"Файл слишком большой ({size} байт). "
            f"Прочитай частями через offset/limit или используй search."
        )

    try:
        text = full.read_text(encoding="utf-8", errors="replace")
    except Exception as e:
        return f"Ошибка чтения: {e}"

    lines = text.splitlines()
    offset = int(args.get("offset") or 0)
    limit = args.get("limit")
    if offset or limit:
        chunk = lines[offset : offset + limit if limit else None]
        numbered = "\n".join(f"{i + offset + 1:5d}│ {ln}" for i, ln in enumerate(chunk))
        return f"{rel} (строки {offset + 1}-{offset + len(chunk)}):\n{numbered}"

    numbered = "\n".join(f"{i + 1:5d}│ {ln}" for i, ln in enumerate(lines))
    return f"{rel} ({len(lines)} строк):\n{numbered}"


def _resolve(workspace: str, rel: str) -> Path | None:
    base = Path(workspace).resolve()
    target = (base / rel).resolve()
    try:
        target.relative_to(base)
    except ValueError:
        return None
    return target


read_file_tool = Tool(
    name="read_file",
    description=(
        "Прочитать файл из рабочей директории. Возвращает содержимое "
        "с нумерацией строк. Для больших файлов можно указать offset и limit."
    ),
    parameters={
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "Путь относительно workspace"},
            "offset": {"type": "integer", "description": "Начальная строка (0-based)"},
            "limit": {"type": "integer", "description": "Сколько строк прочитать"},
        },
        "required": ["path"],
    },
    executor=_execute,
    read_only=True,
)