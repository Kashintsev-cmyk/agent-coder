"""Инструмент: список файлов."""
from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

from agent_coder.tools.registry import Tool, ToolContext

SKIP_DIRS = {".git", "node_modules", "__pycache__", ".venv", "venv", ".next", "dist", "build"}
MAX_ENTRIES = 500


async def _execute(args: dict[str, Any], ctx: ToolContext) -> str:
    base = Path(ctx.workspace).resolve()
    sub = (args.get("path") or ".").strip()
    target = (base / sub).resolve()
    try:
        target.relative_to(base)
    except ValueError:
        return f"Ошибка: путь вне workspace: {sub}"

    if not target.exists():
        return f"Не найдено: {sub}"
    if not target.is_dir():
        return f"Не директория: {sub}"

    pattern = args.get("pattern") or "*"
    entries = await asyncio.to_thread(
        _collect_entries, target, base, pattern,
    )

    if not entries:
        return f"Пусто: {sub}"
    return "\n".join(entries)


def _collect_entries(target: Path, base: Path, pattern: str) -> list[str]:
    """Синхронный сбор (в отдельном потоке, чтобы не блокировать loop)."""
    entries: list[str] = []
    for p in sorted(target.rglob(pattern)):
        if any(part in SKIP_DIRS for part in p.relative_to(target).parts):
            continue
        rel = p.relative_to(base)
        suffix = "/" if p.is_dir() else ""
        entries.append(f"{rel}{suffix}")
        if len(entries) >= MAX_ENTRIES:
            entries.append(f"... (обрезано на {MAX_ENTRIES})")
            break
    return entries


list_files_tool = Tool(
    name="list_files",
    description=(
        "Показать файлы и папки в рабочей директории рекурсивно. "
        "Игнорирует .git, node_modules, __pycache__, .venv и т.п."
    ),
    parameters={
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "Подпапка (по умолчанию '.')"},
            "pattern": {"type": "string", "description": "Glob-шаблон (например '*.py')"},
        },
    },
    executor=_execute,
    read_only=True,
)