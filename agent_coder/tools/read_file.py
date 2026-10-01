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

    offset = int(args.get("offset") or 0)
    limit = args.get("limit")
    has_slice = bool(offset) or bool(limit)

    size = full.stat().st_size
    # Если пользователь передал offset/limit — это ОК для больших файлов.
    # Блокируем только «читать всё целиком» для слишком больших файлов.
    if not has_slice and size > MAX_BYTES:
        return (
            f"Файл слишком большой ({size} байт). "
            f"Укажи offset и limit — например, offset=0 limit=100, "
            f"или используй search."
        )

    # Слишком большой limit — тоже отклоним, чтобы не читать 200k строк за раз
    MAX_LINES = 2000
    if limit is not None and int(limit) > MAX_LINES:
        return f"limit слишком большой ({limit}). Максимум {MAX_LINES} строк за раз."

    try:
        text = full.read_text(encoding="utf-8", errors="replace")
    except Exception as e:
        return f"Ошибка чтения: {e}"

    lines = text.splitlines()

    if has_slice:
        chunk = lines[offset : offset + (int(limit) if limit else None)]
        if not chunk:
            return f"{rel}: пусто (offset={offset}, limit={limit})"
        numbered = "\n".join(
            f"{i + offset + 1:5d}│ {ln}" for i, ln in enumerate(chunk)
        )
        return (
            f"{rel} (строки {offset + 1}-{offset + len(chunk)} "
            f"из {len(lines)}):\n{numbered}"
        )

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