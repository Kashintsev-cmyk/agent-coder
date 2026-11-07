"""Инструмент: быстрые проверки качества проекта (ruff + pytest)."""

from __future__ import annotations

from typing import Any

from agent_coder.shell_runner import clean_env, run_shell
from agent_coder.tools.registry import Tool, ToolContext

# Таймауты: ruff быстрый, pytest — как у run_command.
RUFF_TIMEOUT = 60
PYTEST_TIMEOUT = 300
MAX_OUTPUT = 30_000

# Фиксированный набор (B9.2h): флаги --no-cache / -p no:cacheprovider —
# чтобы проверки не писали .ruff_cache/.pytest_cache вAudited репозиторий.
# M3 (8f0cd848): `--noconftest` — чужой conftest.py в audited workspace не
# импортируется (RCE-вектор закрыт). `env=clean_env()` — секреты бота не
# утекают в subprocess (whitelist PATH/HOME/LANG/PYTHONPATH/...).
_CHECKS: list[tuple[str, int]] = [
    ("ruff check --no-cache .", RUFF_TIMEOUT),
    ("pytest -p no:cacheprovider --noconftest -q", PYTEST_TIMEOUT),
]


async def _execute(args: dict[str, Any], ctx: ToolContext) -> str:
    parts: list[str] = []
    for cmd, timeout in _CHECKS:
        result = await run_shell(
            cmd,
            cwd=ctx.workspace,
            timeout=timeout,
            max_bytes=MAX_OUTPUT,
            mask=True,
            env=clean_env(),  # M3: untrusted subprocess без секретов бота
        )
        header = f"$ {cmd}"
        if result.timed_out:
            parts.append(f"{header}\nПрерван по таймауту ({timeout}с)")
            continue
        if result.error:
            parts.append(f"{header}\nОшибка запуска: {result.error}")
            continue
        body = result.stdout.strip() or "(нет вывода)"
        if result.stderr.strip():
            body += f"\n--- stderr ---\n{result.stderr.strip()}"
        parts.append(f"{header}\nexit code: {result.returncode}\n{body}")
    return "\n\n".join(parts)


run_checks_tool = Tool(
    name="run_checks",
    description=(
        "Быстрые проверки качества проекта: ruff check --no-cache и pytest "
        "(-p no:cacheprovider --noconftest, env-clean). Ничего не меняет и не "
        "пишет кэш-файлы. Используй в PLAN/AUDIT для вердикта по коду и "
        "после правок в ACT."
    ),
    parameters={"type": "object", "properties": {}},
    executor=_execute,
    requires_approval=False,
    read_only=True,
)
