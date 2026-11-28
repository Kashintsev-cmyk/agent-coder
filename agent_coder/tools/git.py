"""Инструменты для git: status, diff, log, commit."""

from __future__ import annotations

import asyncio
from typing import Any

from agent_coder.tools.registry import (
    Tool,
    ToolContext,
    ToolExecutionResult,
    ToolStatus,
)


def _error(output: str) -> ToolExecutionResult:
    return ToolExecutionResult(output, ToolStatus.ERROR)


def _success(output: str) -> ToolExecutionResult:
    return ToolExecutionResult(output, ToolStatus.SUCCESS_NO_CHANGE)


MAX_OUTPUT = 20_000
GIT_TIMEOUT = 30


async def _run_git(ctx: ToolContext, *args: str, timeout: int = GIT_TIMEOUT):
    proc = await asyncio.create_subprocess_exec(
        "git",
        *args,
        cwd=ctx.workspace,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        stdout_b, stderr_b = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except TimeoutError:
        proc.kill()
        await proc.wait()
        return -1, "", f"git {args[0]}: таймаут {timeout}с"
    return (
        proc.returncode,
        stdout_b.decode("utf-8", errors="replace"),
        stderr_b.decode("utf-8", errors="replace"),
    )


def _truncate(text: str, limit: int = MAX_OUTPUT) -> str:
    if len(text) <= limit:
        return text
    return text[:limit] + f"\n... (обрезано, всего {len(text)} символов)"


# ---------- git_status ----------


async def _status_result(args: dict[str, Any], ctx: ToolContext) -> ToolExecutionResult:
    code, out, err = await _run_git(ctx, "status", "--porcelain=v1", "--branch")
    if code != 0:
        if "not a git repository" in err.lower():
            return _error("Не git-репозиторий")
        return _error(f"git status упал (код {code}): {err.strip()}")
    if not out.strip():
        return _success("Рабочее дерево чисто")
    return _success(_truncate(out.rstrip()))


async def _status(args: dict[str, Any], ctx: ToolContext) -> str:
    """Compatibility wrapper for callers expecting plain output text."""
    return (await _status_result(args, ctx)).output


git_status_tool = Tool(
    name="git_status",
    description="Показать состояние рабочего дерева (аналог `git status --porcelain --branch`).",
    parameters={"type": "object", "properties": {}},
    executor=_status_result,
    read_only=True,
)


# ---------- git_diff ----------


async def _diff_result(args: dict[str, Any], ctx: ToolContext) -> ToolExecutionResult:
    cmd = ["diff"]
    if args.get("staged"):
        cmd.append("--staged")
    if args.get("path"):
        cmd.append("--")
        cmd.append(str(args["path"]))
    code, out, err = await _run_git(ctx, *cmd)
    if code != 0:
        return _error(f"git diff упал (код {code}): {err.strip()}")
    if not out.strip():
        return _success("Нет изменений" + (" (staged)" if args.get("staged") else ""))
    return _success(_truncate(out))


async def _diff(args: dict[str, Any], ctx: ToolContext) -> str:
    """Compatibility wrapper for callers expecting plain output text."""
    return (await _diff_result(args, ctx)).output


git_diff_tool = Tool(
    name="git_diff",
    description=(
        "Показать diff текущих изменений. По умолчанию — не-staged. "
        "Флаг staged=true — для изменений в индексе. Можно ограничить файлом через path."
    ),
    parameters={
        "type": "object",
        "properties": {
            "staged": {"type": "boolean", "description": "Показать staged-изменения"},
            "path": {"type": "string", "description": "Ограничить одним файлом"},
        },
    },
    executor=_diff_result,
    read_only=True,
)


# ---------- git_log ----------


async def _log_result(args: dict[str, Any], ctx: ToolContext) -> ToolExecutionResult:
    n = int(args.get("count") or 10)
    n = max(1, min(n, 50))
    code, out, err = await _run_git(
        ctx, "log", f"-{n}", "--pretty=format:%h %ad %an: %s", "--date=short"
    )
    if code != 0:
        if (
            "does not have any commits" in err.lower()
            or "not a git repository" in err.lower()
        ):
            return _error("Нет коммитов или это не git-репозиторий")
        return _error(f"git log упал (код {code}): {err.strip()}")
    return _success(_truncate(out))


async def _log(args: dict[str, Any], ctx: ToolContext) -> str:
    """Compatibility wrapper for callers expecting plain output text."""
    return (await _log_result(args, ctx)).output


git_log_tool = Tool(
    name="git_log",
    description="Показать последние N коммитов (по умолчанию 10, максимум 50).",
    parameters={
        "type": "object",
        "properties": {
            "count": {"type": "integer", "description": "Сколько коммитов (1-50)"},
        },
    },
    executor=_log_result,
    read_only=True,
)


# ---------- git_commit ----------


async def _commit_result(args: dict[str, Any], ctx: ToolContext) -> ToolExecutionResult:
    message = (args.get("message") or "").strip()
    if not message:
        return _error("Ошибка: пустое сообщение коммита")

    if message.startswith("-"):
        return _error("Ошибка: сообщение не должно начинаться с дефиса")

    # 1. Проверяем, что есть что коммитить (staged)
    code, out, err = await _run_git(ctx, "diff", "--staged", "--name-only")
    if code != 0:
        return _error(f"Не git-репозиторий: {err.strip()}")
    staged = out.strip().splitlines()
    if not staged:
        return _error(
            "Нечего коммитить: индекс пуст. Сначала добавь файлы "
            "(например, через run_command: `git add <файлы>`)."
        )

    # 2. Коммит
    code, out, err = await _run_git(
        ctx,
        "commit",
        "-m",
        message,
        timeout=60,
    )
    if code != 0:
        return _error(f"git commit упал (код {code}): {err.strip()}")

    # 3. Возвращаем свежий хеш
    code2, hash_out, _ = await _run_git(ctx, "rev-parse", "--short", "HEAD")
    short_hash = hash_out.strip() if code2 == 0 else "?"

    files_summary = ", ".join(staged[:5])
    if len(staged) > 5:
        files_summary += f" и ещё {len(staged) - 5}"

    output = f"Коммит {short_hash}: {message}\nФайлов: {len(staged)} ({files_summary})"
    return ToolExecutionResult(
        output,
        ToolStatus.SUCCESS_CHANGED,
        changed_paths=tuple(staged),
    )


async def _commit(args: dict[str, Any], ctx: ToolContext) -> str:
    """Compatibility wrapper for callers expecting plain output text."""
    return (await _commit_result(args, ctx)).output


git_commit_tool = Tool(
    name="git_commit",
    description=(
        "Закоммитить всё, что уже в staging-индексе (не делает git add за тебя). "
        "Если индекс пуст — вернёт ошибку, сначала добавь файлы через run_command. "
        "Сообщение коммита — в стиле репозитория (короткое, императивное, на английском)."
    ),
    parameters={
        "type": "object",
        "properties": {
            "message": {
                "type": "string",
                "description": "Сообщение коммита (одна строка)",
            },
        },
        "required": ["message"],
    },
    executor=_commit_result,
    requires_approval=True,
    read_only=False,
)
