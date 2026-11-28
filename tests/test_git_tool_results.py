from __future__ import annotations

import asyncio
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from agent_coder.agent.prompts import Mode
from agent_coder.tools import git
from agent_coder.tools.git import (
    git_commit_tool,
    git_diff_tool,
    git_log_tool,
    git_status_tool,
)
from agent_coder.tools.registry import ToolContext, ToolExecutionResult, ToolStatus


@pytest.fixture
def ctx(tmp_path: Path) -> ToolContext:
    return ToolContext(workspace=str(tmp_path), mode=Mode.PLAN)


def test_read_only_git_tools_report_structured_success(
    ctx: ToolContext, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def fake_run_git(*args: Any, **kwargs: Any) -> tuple[int, str, str]:
        return 0, " M file.txt\n", ""

    monkeypatch.setattr(git, "_run_git", fake_run_git)
    for tool in (git_status_tool, git_diff_tool, git_log_tool):
        result = asyncio.run(tool.executor({}, ctx))
        assert isinstance(result, ToolExecutionResult)
        assert result.status == ToolStatus.SUCCESS_NO_CHANGE
        assert "file.txt" in result.output


def test_read_only_git_tool_failures_have_error_status(
    ctx: ToolContext, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def fake_run_git(*args: Any, **kwargs: Any) -> tuple[int, str, str]:
        return 1, "", "git failed"

    monkeypatch.setattr(git, "_run_git", fake_run_git)
    result = asyncio.run(git_status_tool.executor({}, ctx))
    assert isinstance(result, ToolExecutionResult)
    assert result.status == ToolStatus.ERROR
    assert "git status упал" in result.output


def test_git_commit_reports_changed_staged_paths(
    ctx: ToolContext, monkeypatch: pytest.MonkeyPatch
) -> None:
    responses = iter(
        [
            (0, "file.txt\nother.txt\n", ""),
            (0, "commit output", ""),
            (0, "abc123\n", ""),
        ]
    )

    async def fake_run_git(*args: Any, **kwargs: Any) -> tuple[int, str, str]:
        return next(responses)

    monkeypatch.setattr(git, "_run_git", fake_run_git)
    result = asyncio.run(git_commit_tool.executor({"message": "Add files"}, ctx))
    assert isinstance(result, ToolExecutionResult)
    assert result.status == ToolStatus.SUCCESS_CHANGED
    assert result.changed_paths == ("file.txt", "other.txt")
    assert "Коммит abc123: Add files" in result.output


def test_git_compatibility_wrappers_return_plain_text(
    ctx: ToolContext, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def fake_run_git(*args: Any, **kwargs: Any) -> tuple[int, str, str]:
        if args[1:4] == ("diff", "--staged", "--name-only"):
            return 0, "", ""
        return 0, "plain output", ""

    monkeypatch.setattr(git, "_run_git", fake_run_git)
    wrappers: tuple[Callable[..., Any], ...] = (
        git._status,
        git._diff,
        git._log,
    )
    for wrapper in wrappers:
        result = asyncio.run(wrapper({}, ctx))
        assert isinstance(result, str)
        assert "plain output" in result

    result = asyncio.run(git._commit({"message": "ignored"}, ctx))
    assert isinstance(result, str)
    assert "Нечего коммитить" in result
