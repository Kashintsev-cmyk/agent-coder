"""Инструмент: поиск по проекту через ripgrep."""

from __future__ import annotations

import asyncio
import shutil
from pathlib import Path
from typing import Any

from agent_coder.tools.registry import Tool, ToolContext

MAX_MATCHES = 200
TIMEOUT_SEC = 60


async def _execute(args: dict[str, Any], ctx: ToolContext) -> str:
    query = args.get("query", "").strip()
    if not query:
        return "Ошибка: пустой query"

    rg = shutil.which("rg")
    if not rg:
        return "Ошибка: ripgrep (rg) не найден на сервере"

    # Валидация path: только внутри workspace
    raw_path = (args.get("path") or ".").strip() or "."
    try:
        base = Path(ctx.workspace).resolve()
        target = (base / raw_path).resolve()
        target.relative_to(base)
    except (ValueError, OSError):
        return f"Ошибка: путь {raw_path!r} вне workspace"

    cmd = [
        rg,
        "--line-number",
        "--no-heading",
        "--color=never",
        "--max-count",
        str(MAX_MATCHES),
    ]
    if args.get("fixed_strings", True):
        cmd.append("--fixed-strings")
    glob = args.get("glob")
    if glob:
        cmd.extend(["--glob", glob])
    # `--` перед позиционными — защита от query/path, начинающихся с `-`
    cmd.extend(["--", query, str(target)])

    proc = await asyncio.create_subprocess_exec(
        *cmd,
        cwd=ctx.workspace,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        start_new_session=True,
    )
    try:
        out, err = await asyncio.wait_for(proc.communicate(), timeout=TIMEOUT_SEC)
    except TimeoutError:
        try:
            proc.kill()
            await proc.wait()
        except Exception:
            pass
        return f"search: таймаут {TIMEOUT_SEC}с (ripgrep не завершился)"
    stdout = out.decode("utf-8", errors="replace").strip()
    if not stdout:
        if proc.returncode == 1:
            return f"Ничего не найдено по запросу: {query!r}"
        return (
            f"rg завершился с кодом {proc.returncode}: {err.decode(errors='replace')}"
        )

    lines = stdout.splitlines()
    if len(lines) > MAX_MATCHES:
        lines = lines[:MAX_MATCHES] + [f"... (обрезано на {MAX_MATCHES})"]
    return "\n".join(lines)


search_tool = Tool(
    name="search",
    description=(
        "Поиск текста по проекту (ripgrep). Возвращает строки в формате "
        "'файл:строка:содержимое'. По умолчанию — поиск подстроки, не regex."
    ),
    parameters={
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "Что искать"},
            "path": {"type": "string", "description": "Где искать (по умолчанию '.')"},
            "glob": {
                "type": "string",
                "description": "Glob-фильтр файлов, напр. '*.py'",
            },
            "fixed_strings": {
                "type": "boolean",
                "description": "Считать query буквальной строкой (по умолчанию true)",
            },
        },
        "required": ["query"],
    },
    executor=_execute,
    read_only=True,
)
