"""Инструмент: поиск по архиву сжатой истории сессии (RAG-lite).

Архив пишется в sessions/archive/<session_id>.jsonl при каждом сжатии
истории (см. agent_coder.agent.history_archive). Поиск — через ripgrep
по JSONL-файлу.
"""
from __future__ import annotations

import asyncio
import shutil
from typing import Any

from agent_coder.agent import history_archive
from agent_coder.agent.session import content_to_text
from agent_coder.tools.registry import Tool, ToolContext

MAX_MATCHES = 30
TIMEOUT_SEC = 30


def _format_match(line: str) -> str:
    """JSONL-строку → читаемый фрагмент для агента."""
    import json

    try:
        m = json.loads(line)
    except json.JSONDecodeError:
        return line[:500]
    role = m.get("role", "?")
    text = content_to_text(m.get("content")).strip()
    tcs = m.get("tool_calls") or []
    if tcs:
        names = ", ".join(
            (tc.get("function") or {}).get("name", "?") for tc in tcs
        )
        text = (text + f"\n[вызвал: {names}]").strip()
    return f"[{role}] {text[:600]}"


async def _execute(args: dict[str, Any], ctx: ToolContext) -> str:
    query = args.get("query", "").strip()
    if not query:
        return "Ошибка: пустой query"

    path = history_archive.archive_path(ctx.session_id)
    if not path.exists():
        return (
            "Архив истории пуст (история ещё не сжималась). "
            "Детали прошлых задач недоступны."
        )

    rg = shutil.which("rg")
    if not rg:
        return "Ошибка: ripgrep (rg) не найден на сервере"

    cmd = [
        rg, "--no-heading", "--no-line-number", "--color=never",
        "--max-count", str(MAX_MATCHES),
        "--fixed-strings",
        "--", query, str(path),
    ]

    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        start_new_session=True,
    )
    try:
        out, err = await asyncio.wait_for(proc.communicate(), timeout=TIMEOUT_SEC)
    except asyncio.TimeoutError:
        try:
            proc.kill()
            await proc.wait()
        except Exception:
            pass
        return f"search_history: таймаут {TIMEOUT_SEC}с"

    stdout = out.decode("utf-8", errors="replace").strip()
    if not stdout:
        if proc.returncode == 1:
            return f"В архиве истории ничего не найдено по запросу: {query!r}"
        return f"rg завершился с кодом {proc.returncode}: {err.decode(errors='replace')}"

    lines = stdout.splitlines()
    if len(lines) > MAX_MATCHES:
        lines = lines[:MAX_MATCHES] + [f"... (обрезано на {MAX_MATCHES})"]
    # Без --line-number каждая строка вывода rg — это JSONL-строка целиком
    return "\n\n".join(_format_match(l) for l in lines)


search_history_tool = Tool(
    name="search_history",
    description=(
        "Поиск по архиву сжатой истории этой сессии. Когда старая часть "
        "диалога сжимается в резюме, исходные сообщения сохраняются в архив. "
        "Если резюме не содержит нужной детали (точные числа, код, пути) — "
        "найди их здесь. Поиск подстроки, без regex."
    ),
    parameters={
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "Что искать (подстрока, напр. имя файла или функции)",
            },
        },
        "required": ["query"],
    },
    executor=_execute,
    read_only=True,
)
