"""Инструменты для git: status, diff, log, commit."""
from __future__ import annotations

import asyncio
from typing import Any

from agent_coder.tools.registry import Tool, ToolContext

MAX_OUTPUT = 20_000
GIT_TIMEOUT = 30


async def _run_git(ctx: ToolContext, *args: str, timeout: int = GIT_TIMEOUT):
    proc = await asyncio.create_subprocess_exec(
        "git", *args,
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
    return proc.returncode, stdout_b.decode("utf-8", errors="replace"), stderr_b.decode("utf-8", errors="replace")


def _truncate(text: str, limit: int = MAX_OUTPUT) -> str:
    if len(text) <= limit:
        return text
    return text[:limit] + f"\n... (обрезано, всего {len(text)} символов)"


# ---------- git_status ----------

async def _status(args: dict[str, Any], ctx: ToolContext) -> str:
    code, out, err = await _run_git(ctx, "status", "--porcelain=v1", "--branch")
    if code != 0:
        if "not a git repository" in err.lower():
            return "Не git-репозиторий"
        return f"git status упал (код {code}): {err.strip()}"
    if not out.strip():
        return "Рабочее дерево чисто"
    return _truncate(out.rstrip())


git_status_tool = Tool(
    name="git_status",
    description="Показать состояние рабочего дерева (аналог `git status --porcelain --branch`).",
    parameters={"type": "object", "properties": {}},
    executor=_status,
    read_only=True,
)


# ---------- git_diff ----------

async def _diff(args: dict[str, Any], ctx: ToolContext) -> str:
    cmd = ["diff"]
    if args.get("staged"):
        cmd.append("--staged")
    if args.get("path"):
        cmd.append("--")
        cmd.append(str(args["path"]))
    code, out, err = await _run_git(ctx, *cmd)
    if code != 0:
        return f"git diff упал (код {code}): {err.strip()}"
    if not out.strip():
        return "Нет изменений" + (" (staged)" if args.get("staged") else "")
    return _truncate(out)


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
    executor=_diff,
    read_only=True,
)


# ---------- git_log ----------

async def _log(args: dict[str, Any], ctx: ToolContext) -> str:
    n = int(args.get("count") or 10)
    n = max(1, min(n, 50))
    code, out, err = await _run_git(ctx, "log", f"-{n}", "--pretty=format:%h %ad %an: %s", "--date=short")
    if code != 0:
        if "does not have any commits" in err.lower() or "not a git repository" in err.lower():
            return "Нет коммитов или это не git-репозиторий"
        return f"git log упал (код {code}): {err.strip()}"
    return _truncate(out)


git_log_tool = Tool(
    name="git_log",
    description="Показать последние N коммитов (по умолчанию 10, максимум 50).",
    parameters={
        "type": "object",
        "properties": {
            "count": {"type": "integer", "description": "Сколько коммитов (1-50)"},
        },
    },
    executor=_log,
    read_only=True,
)


# ---------- git_commit ----------

async def _commit(args: dict[str, Any], ctx: ToolContext) -> str:
    message = (args.get("message") or "").strip()
    if not message:
        return "Ошибка: пустое сообщение коммита"

    if message.startswith("-"):
        return "Ошибка: сообщение не должно начинаться с дефиса"

    # 1. Проверяем, что есть что коммитить (staged)
    code, out, err = await _run_git(ctx, "diff", "--staged", "--name-only")
    if code != 0:
        return f"Не git-репозиторий: {err.strip()}"
    staged = out.strip().splitlines()
    if not staged:
        return (
            "Нечего коммитить: индекс пуст. Сначала добавь файлы "
            "(например, через run_command: `git add <файлы>`)."
        )

    # 2. Коммит
    code, out, err = await _run_git(
        ctx, "commit", "-m", message,
        timeout=60,
    )
    if code != 0:
        return f"git commit упал (код {code}): {err.strip()}"

    # 3. Возвращаем свежий хеш
    code2, hash_out, _ = await _run_git(ctx, "rev-parse", "--short", "HEAD")
    short_hash = hash_out.strip() if code2 == 0 else "?"

    files_summary = ", ".join(staged[:5])
    if len(staged) > 5:
        files_summary += f" и ещё {len(staged) - 5}"

    return f"Коммит {short_hash}: {message}\nФайлов: {len(staged)} ({files_summary})"


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
    executor=_commit,
    requires_approval=True,
    read_only=False,
)
