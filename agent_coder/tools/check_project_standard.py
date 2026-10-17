"""Инструмент: проверка проекта на соответствие стандарту bots/.

Читает структуру проекта и pyproject.toml, возвращает JSON с issues.
Ничего не пишет — правки делает вызывающий агент через write_file/
edit_file/run_command в ACT.
"""

from __future__ import annotations

import asyncio
import json
import logging
from pathlib import Path
from typing import Any

try:
    import tomllib
except ImportError:  # Python 3.10
    import tomli as tomllib  # type: ignore[no-redef]

from agent_coder.tools.registry import Tool, ToolContext

logger = logging.getLogger(__name__)

REQUIRED_FILES: tuple[str, ...] = (
    "pyproject.toml",
    "requirements.in",
    "requirements-dev.in",
    "requirements-dev.txt",
    ".gitignore",
    "README.md",
    "AGENTS.md",
)
REQUIRED_RUFF_SELECT: frozenset[str] = frozenset({"E", "F", "I"})


def _issue(
    path: str, severity: str, issue: str, expected: str, found: str
) -> dict[str, Any]:
    return {
        "path": path,
        "severity": severity,
        "issue": issue,
        "expected": expected,
        "found": found,
    }


def _read_pyproject(base: Path) -> tuple[dict[str, Any] | None, str | None]:
    """(data, error). error заполнен, если файл есть, но не парсится."""
    p = base / "pyproject.toml"
    if not p.is_file():
        return None, None
    try:
        return tomllib.loads(p.read_text(encoding="utf-8")), None
    except tomllib.TOMLDecodeError as e:
        return None, f"invalid TOML: {e}"
    except OSError as e:
        return None, f"unreadable: {e}"


def _has_remote(base: Path) -> bool:
    cfg = base / ".git" / "config"
    if not cfg.is_file():
        return False
    try:
        return "[remote" in cfg.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return False


def _collect_issues(base: Path) -> list[dict[str, Any]]:
    issues: list[dict[str, Any]] = []

    # 1. Обязательные файлы и tests/
    for name in REQUIRED_FILES:
        if not (base / name).is_file():
            issues.append(
                _issue(name, "major", "missing_file", "file present", "not found")
            )
    if not (base / "tests").is_dir():
        issues.append(
            _issue(
                "tests/",
                "major",
                "missing_tests_dir",
                "directory present",
                "not found",
            )
        )

    # 2. pyproject.toml: парсинг + секции
    pp, pp_error = _read_pyproject(base)
    if pp_error is not None:
        issues.append(
            _issue(
                "pyproject.toml",
                "major",
                "pyproject_unparseable",
                "valid TOML",
                pp_error,
            )
        )
    elif pp is not None:
        tool = pp.get("tool", {}) if isinstance(pp, dict) else {}
        if not isinstance(tool, dict):
            tool = {}

        pytest_opts = tool.get("pytest", {})
        if not isinstance(pytest_opts, dict):
            pytest_opts = {}
        ini = pytest_opts.get("ini_options", {})
        if not isinstance(ini, dict):
            ini = {}
        testpaths = ini.get("testpaths")
        if not (isinstance(testpaths, list) and "tests" in testpaths):
            issues.append(
                _issue(
                    "pyproject.toml",
                    "minor",
                    "pytest_testpaths_missing",
                    'testpaths = ["tests"]',
                    f"testpaths = {testpaths!r}",
                )
            )

        ruff = tool.get("ruff", {})
        if not isinstance(ruff, dict):
            ruff = {}
        ll = ruff.get("line-length")
        if ll != 88:
            issues.append(
                _issue(
                    "pyproject.toml",
                    "minor",
                    "ruff_line_length_not_88",
                    "line-length = 88",
                    f"line-length = {ll!r}",
                )
            )

        ruff_lint = ruff.get("lint", {})
        if not isinstance(ruff_lint, dict):
            ruff_lint = {}
        select = ruff_lint.get("select")
        if not (
            isinstance(select, list) and REQUIRED_RUFF_SELECT.issubset(set(select))
        ):
            issues.append(
                _issue(
                    "pyproject.toml",
                    "minor",
                    "ruff_select_incomplete",
                    f"select ⊇ {sorted(REQUIRED_RUFF_SELECT)}",
                    f"select = {select!r}",
                )
            )

        cov = tool.get("coverage", {})
        if not isinstance(cov, dict):
            cov = {}
        cov_run = cov.get("run", {})
        if not isinstance(cov_run, dict):
            cov_run = {}
        source = cov_run.get("source")
        if isinstance(source, list) and "app" in source:
            issues.append(
                _issue(
                    "pyproject.toml",
                    "major",
                    "coverage_source_placeholder_app",
                    'source = ["<package_name>"]',
                    f"source = {source!r}",
                )
            )

    # 3. CI — только если у проекта есть remote
    if _has_remote(base) and not (base / ".github" / "workflows" / "ci.yml").is_file():
        issues.append(
            _issue(
                ".github/workflows/ci.yml",
                "minor",
                "ci_missing_with_remote",
                "ci.yml present",
                "not found",
            )
        )

    return issues


def _summarize(issues: list[dict[str, Any]]) -> dict[str, Any]:
    majors = sum(1 for i in issues if i["severity"] == "major")
    minors = sum(1 for i in issues if i["severity"] == "minor")
    score = "failures" if majors else ("warnings" if minors else "ok")
    return {"score": score, "majors": majors, "minors": minors, "issues": issues}


async def _execute(args: dict[str, Any], ctx: ToolContext) -> str:
    sub = (args.get("path") or ".").strip()
    base = Path(ctx.workspace).resolve()
    target = (base / sub).resolve()
    try:
        target.relative_to(base)
    except ValueError:
        return f"Ошибка: путь вне workspace: {sub}"
    if not target.is_dir():
        return f"Не директория: {sub}"
    issues = await asyncio.to_thread(_collect_issues, target)
    return json.dumps(_summarize(issues), ensure_ascii=False, indent=2)


check_project_standard_tool = Tool(
    name="check_project_standard",
    description=(
        "Проверить соответствие Python-проекта стандарту bots/: "
        "обязательные файлы, секции pyproject.toml, tests/, CI. "
        "Возвращает JSON: score, majors, minors, issues. Только чтение."
    ),
    parameters={
        "type": "object",
        "properties": {
            "path": {
                "type": "string",
                "description": "Подпапка workspace (по умолчанию '.')",
            },
        },
    },
    executor=_execute,
    requires_approval=False,
    read_only=True,
)
