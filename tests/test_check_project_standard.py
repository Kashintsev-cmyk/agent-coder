"""Тесты инструмента check_project_standard."""

from __future__ import annotations

import json
from pathlib import Path

from agent_coder.agent.prompts import Mode
from agent_coder.tools import build_registry
from agent_coder.tools.check_project_standard import check_project_standard_tool
from agent_coder.tools.registry import ToolContext

COMPLETE_PYPROJECT = """\
[project]
name = "demo"
version = "0.1.0"
requires-python = ">=3.10"

[tool.pytest.ini_options]
testpaths = ["tests"]

[tool.ruff]
line-length = 88

[tool.ruff.lint]
select = ["E", "F", "W", "I", "UP", "B", "C4", "SIM"]

[tool.coverage.run]
source = ["demo"]
"""


def _write_complete_skeleton(root: Path) -> None:
    (root / "pyproject.toml").write_text(COMPLETE_PYPROJECT, encoding="utf-8")
    for name in (
        "requirements.in",
        "requirements-dev.in",
        "requirements-dev.txt",
        ".gitignore",
        "README.md",
        "AGENTS.md",
    ):
        (root / name).write_text("", encoding="utf-8")
    (root / "tests").mkdir()


def _ctx(workspace: Path) -> ToolContext:
    return ToolContext(workspace=str(workspace), mode=Mode.PLAN)


async def _run(workspace: Path, path: str = ".") -> dict:
    raw = await check_project_standard_tool.executor({"path": path}, _ctx(workspace))
    return json.loads(raw)


# --- наличие файлов ---


def test_empty_project_reports_all_missing(tmp_path: Path) -> None:
    import asyncio

    data = asyncio.run(_run(tmp_path))
    assert data["score"] == "failures"
    missing = {i["path"] for i in data["issues"] if i["issue"] == "missing_file"}
    assert missing == {
        "pyproject.toml",
        "requirements.in",
        "requirements-dev.in",
        "requirements-dev.txt",
        ".gitignore",
        "README.md",
        "AGENTS.md",
    }
    assert any(i["issue"] == "missing_tests_dir" for i in data["issues"])


def test_complete_project_ok(tmp_path: Path) -> None:
    import asyncio

    _write_complete_skeleton(tmp_path)
    data = asyncio.run(_run(tmp_path))
    assert data["score"] == "ok", data
    assert data["majors"] == 0
    assert data["minors"] == 0


# --- pyproject.toml секции ---


def test_app_in_coverage_source_flagged(tmp_path: Path) -> None:
    import asyncio

    _write_complete_skeleton(tmp_path)
    p = tmp_path / "pyproject.toml"
    p.write_text(
        COMPLETE_PYPROJECT.replace('source = ["demo"]', 'source = ["app"]'),
        encoding="utf-8",
    )
    data = asyncio.run(_run(tmp_path))
    issues = [
        i for i in data["issues"] if i["issue"] == "coverage_source_placeholder_app"
    ]
    assert len(issues) == 1
    assert issues[0]["severity"] == "major"


def test_ruff_line_length_100_flagged(tmp_path: Path) -> None:
    import asyncio

    _write_complete_skeleton(tmp_path)
    p = tmp_path / "pyproject.toml"
    p.write_text(
        COMPLETE_PYPROJECT.replace("line-length = 88", "line-length = 100"),
        encoding="utf-8",
    )
    data = asyncio.run(_run(tmp_path))
    issues = [i for i in data["issues"] if i["issue"] == "ruff_line_length_not_88"]
    assert len(issues) == 1


def test_pytest_testpaths_missing_flagged(tmp_path: Path) -> None:
    import asyncio

    _write_complete_skeleton(tmp_path)
    p = tmp_path / "pyproject.toml"
    p.write_text(
        COMPLETE_PYPROJECT.replace('testpaths = ["tests"]\n', ""),
        encoding="utf-8",
    )
    data = asyncio.run(_run(tmp_path))
    issues = [i for i in data["issues"] if i["issue"] == "pytest_testpaths_missing"]
    assert len(issues) == 1


def test_ruff_select_incomplete_flagged(tmp_path: Path) -> None:
    import asyncio

    _write_complete_skeleton(tmp_path)
    p = tmp_path / "pyproject.toml"
    p.write_text(
        COMPLETE_PYPROJECT.replace(
            'select = ["E", "F", "W", "I", "UP", "B", "C4", "SIM"]',
            'select = ["E", "W"]',
        ),
        encoding="utf-8",
    )
    data = asyncio.run(_run(tmp_path))
    issues = [i for i in data["issues"] if i["issue"] == "ruff_select_incomplete"]
    assert len(issues) == 1


def test_pyproject_unparseable_flagged(tmp_path: Path) -> None:
    import asyncio

    (tmp_path / "pyproject.toml").write_text("not = [valid toml", encoding="utf-8")
    data = asyncio.run(_run(tmp_path))
    issues = [i for i in data["issues"] if i["issue"] == "pyproject_unparseable"]
    assert len(issues) == 1
    assert issues[0]["severity"] == "major"


# --- CI / remote ---


def test_ci_missing_when_git_remote_present(tmp_path: Path) -> None:
    import asyncio

    _write_complete_skeleton(tmp_path)
    (tmp_path / ".git").mkdir()
    (tmp_path / ".git" / "config").write_text(
        '[remote "origin"]\n\turl = git@example.com:x/y.git\n', encoding="utf-8"
    )
    data = asyncio.run(_run(tmp_path))
    issues = [i for i in data["issues"] if i["issue"] == "ci_missing_with_remote"]
    assert len(issues) == 1


def test_ci_not_flagged_when_no_remote(tmp_path: Path) -> None:
    import asyncio

    _write_complete_skeleton(tmp_path)
    data = asyncio.run(_run(tmp_path))
    issues = [i for i in data["issues"] if i["issue"] == "ci_missing_with_remote"]
    assert issues == []


# --- path guards ---


async def _run_raw(workspace: Path, path: str) -> str:
    return await check_project_standard_tool.executor({"path": path}, _ctx(workspace))


def test_path_outside_workspace_errors(tmp_path: Path) -> None:
    import asyncio

    raw: str = asyncio.run(_run_raw(tmp_path, "../etc"))
    assert raw.startswith("Ошибка: путь вне workspace")


def test_non_directory_errors(tmp_path: Path) -> None:
    import asyncio

    f = tmp_path / "file.txt"
    f.write_text("x", encoding="utf-8")
    raw: str = asyncio.run(_run_raw(tmp_path, "file.txt"))
    assert raw.startswith("Не директория")


# --- регистрация и read_only ---


def test_tool_registered_and_read_only() -> None:
    assert check_project_standard_tool.read_only is True
    assert check_project_standard_tool.requires_approval is False
    reg = build_registry()
    assert reg.get("check_project_standard") is check_project_standard_tool
    names_plan = {t.name for t in reg.for_mode(Mode.PLAN)}
    assert "check_project_standard" in names_plan
    names_audit = {t.name for t in reg.for_mode(Mode.AUDIT)}
    assert "check_project_standard" in names_audit
    names_chat = {t.name for t in reg.for_mode(Mode.CHAT)}
    assert "check_project_standard" not in names_chat
