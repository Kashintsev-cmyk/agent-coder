"""Инструмент: быстрые проверки качества проекта (ruff + pytest)."""

from __future__ import annotations

from typing import Any

from agent_coder.shell_runner import clean_env, run_shell
from agent_coder.tools.registry import (
    Tool,
    ToolContext,
    ToolExecutionResult,
    ToolStatus,
)

# Таймауты: ruff быстрый, pytest — как у run_command.
RUFF_TIMEOUT = 60
PYTEST_TIMEOUT = 300
MYPY_TIMEOUT = 180
BANDIT_TIMEOUT = 180
MAX_OUTPUT = 30_000

# Фиксированный набор (B9.2h): флаги --no-cache / -p no:cacheprovider —
# чтобы проверки не писали .ruff_cache/.pytest_cache вAudited репозиторий.
# M3 (8f0cd848): `--noconftest` — чужой conftest.py в audited workspace не
# импортируется (RCE-вектор закрыт). `env=clean_env()` — секреты бота не
# утекают в subprocess (whitelist PATH/HOME/LANG/PYTHONPATH/...).
#
# F-series: mypy и bandit идут в CI (.github/workflows/ci.yml), но в цепочке
# проверок агента их не было — сессии их не запускали и не видели результата.
# Теперь есть. Оба «мягкие» (soft=True): аудируемый проект может не иметь
# mypy/bandit или конфига — это не провал проверки, а «неприменимо», и
# помечается отдельно, чтобы не выглядело как зелёный результат.
_CHECKS: list[tuple[str, int, bool]] = [
    ("ruff check --no-cache .", RUFF_TIMEOUT, False),
    ("pytest -p no:cacheprovider --noconftest -q", PYTEST_TIMEOUT, False),
    ("mypy .", MYPY_TIMEOUT, True),
    ("python3 -m bandit -r . -c bandit.yaml", BANDIT_TIMEOUT, True),
]

# Признаки того, что инструмент в проекте не настроен/не установлен —
# тогда строка помечается «пропущено», а не «успех».
_SKIP_MARKERS = (
    "no module named bandit",
    "mypy: not found",
    "no module named mypy",
    "command not found",
    "error: cannot find implementation or library stub",
    "[tool.bandit]",
    # bandit без bandit.yaml в проекте: exit=2 + "bandit.yaml : Could not read
    # config file" (живой probe 2026-11-21). Это отсутствие настройки, а не
    # найденная проблема — иначе чужой проект получил бы ложный «провал».
    "could not read config file",
)


def _is_not_configured(result: Any) -> bool:
    """Инструмент не установлен/не настроен в аудируемом проекте.

    Отличаем «нечего проверять» от «проверка провалена»: иначе отсутствие
    mypy в чужом проекте выглядело бы как найденная проблема.
    """
    blob = f"{result.stdout}\n{result.stderr}".lower()
    return any(marker in blob for marker in _SKIP_MARKERS)


async def _execute_result(
    args: dict[str, Any], ctx: ToolContext
) -> ToolExecutionResult:
    # Failed checks are errors, so the ACT completion gate cannot treat a
    # report containing failed tests/lint as successful verification.
    parts: list[str] = []
    failed = False
    for cmd, timeout, soft in _CHECKS:
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
            failed = True
            parts.append(f"{header}\nПрерван по таймауту ({timeout}с)")
            continue
        if result.error:
            failed = True
            parts.append(f"{header}\nОшибка запуска: {result.error}")
            continue
        body = result.stdout.strip() or "(нет вывода)"
        if result.stderr.strip():
            body += f"\n--- stderr ---\n{result.stderr.strip()}"
        if soft and result.returncode != 0 and _is_not_configured(result):
            parts.append(f"{header}\nпропущено: инструмент не настроен в проекте")
            continue
        if result.returncode != 0:
            failed = True
        parts.append(f"{header}\nexit code: {result.returncode}\n{body}")
    return ToolExecutionResult(
        "\n\n".join(parts),
        ToolStatus.ERROR if failed else ToolStatus.SUCCESS_NO_CHANGE,
        exit_code=1 if failed else 0,
    )


async def _execute(args: dict[str, Any], ctx: ToolContext) -> str:
    """Совместимая обёртка: текст результата без структуры."""
    return (await _execute_result(args, ctx)).output


run_checks_tool = Tool(
    name="run_checks",
    description=(
        "Быстрые проверки качества проекта: ruff check --no-cache и pytest "
        "(-p no:cacheprovider --noconftest, env-clean). Ничего не меняет и не "
        "пишет кэш-файлы. Используй в PLAN/AUDIT для вердикта по коду и "
        "после правок в ACT."
    ),
    parameters={"type": "object", "properties": {}},
    executor=_execute_result,
    requires_approval=False,
    read_only=True,
)
