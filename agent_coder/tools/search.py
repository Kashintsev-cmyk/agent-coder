"""Инструмент: поиск по проекту через ripgrep."""

from __future__ import annotations

import asyncio
import shutil
from pathlib import Path
from typing import Any

from agent_coder.tools._scope import in_scope
from agent_coder.tools.list_files import SKIP_DIRS
from agent_coder.tools.registry import (
    Tool,
    ToolContext,
    ToolExecutionResult,
    ToolStatus,
)

MAX_MATCHES = 200
TIMEOUT_SEC = 60


def _error(output: str) -> ToolExecutionResult:
    return ToolExecutionResult(output, ToolStatus.ERROR)


def _ok(output: str) -> ToolExecutionResult:
    return ToolExecutionResult(output, ToolStatus.SUCCESS_NO_CHANGE)


async def _execute_result(
    args: dict[str, Any], ctx: ToolContext
) -> ToolExecutionResult:
    query = args.get("query", "").strip()
    if not query:
        return _error("Ошибка: пустой query")

    rg = shutil.which("rg")
    if not rg:
        return _error("Ошибка: ripgrep (rg) не найден на сервере")

    # Валидация path: только внутри workspace
    raw_path = (args.get("path") or ".").strip() or "."
    if ctx.audit_scopes:
        if raw_path in (".", "./"):
            raw_path = ctx.audit_scopes[0]
        elif not in_scope(raw_path, ctx.audit_scopes):
            return _error(
                f"Ошибка: путь {raw_path!r} вне области аудита {ctx.audit_scopes}"
            )
    try:
        base = Path(ctx.workspace).resolve()
        target = (base / raw_path).resolve()
        target.relative_to(base)
    except (ValueError, OSError):
        return _error(f"Ошибка: путь {raw_path!r} вне workspace")

    cmd = [
        rg,
        "--line-number",
        "--no-heading",
        "--color=never",
        "--max-count",
        str(MAX_MATCHES),
    ]
    # B9.5: не искать в кешах/venv — 3e2513be: rg отдавал .mypy_cache/
    # .venv310/ (модель жгла токены на мусор).
    for skip in sorted(SKIP_DIRS):
        cmd.extend(["--glob", f"!{skip}/**"])
    cmd.extend(["--glob", "!*.egg-info/**"])
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
        except Exception:  # nosec B110
            pass
        return _error(f"search: таймаут {TIMEOUT_SEC}с (ripgrep не завершился)")
    stdout = out.decode("utf-8", errors="replace").strip()
    if not stdout:
        if proc.returncode == 1:
            return _ok(f"Ничего не найдено по запросу: {query!r}")
        return _error(
            f"rg завершился с кодом {proc.returncode}: {err.decode(errors='replace')}"
        )

    lines = stdout.splitlines()
    if len(lines) > MAX_MATCHES:
        lines = lines[:MAX_MATCHES] + [f"... (обрезано на {MAX_MATCHES})"]
    return _ok("\n".join(lines))


async def _execute(args: dict[str, Any], ctx: ToolContext) -> str:
    """Совместимая обёртка: текст результата без структуры."""
    return (await _execute_result(args, ctx)).output


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
    executor=_execute_result,
    read_only=True,
)
