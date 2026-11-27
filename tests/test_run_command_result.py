"""Структурированный результат run_command."""

import importlib

import pytest

from agent_coder.agent.modes import Mode
from agent_coder.shell_runner import ShellResult
from agent_coder.tools.registry import ToolContext, ToolStatus

run_command = importlib.import_module("agent_coder.tools.run_command")


@pytest.mark.asyncio
async def test_nonzero_exit_code_is_error(monkeypatch, tmp_path):
    async def fake_run_shell(*args, **kwargs):
        return ShellResult(returncode=7, stdout="", stderr="failed")

    monkeypatch.setattr(run_command, "run_shell", fake_run_shell)
    result = await run_command._execute(
        {"command": "false"},
        ToolContext(workspace=str(tmp_path), mode=Mode.ACT),
    )

    assert result.status == ToolStatus.ERROR
    assert result.exit_code == 7
    assert "exit code: 7" in result.output


@pytest.mark.asyncio
async def test_successful_read_only_command_has_no_change(monkeypatch, tmp_path):
    async def fake_run_shell(*args, **kwargs):
        return ShellResult(returncode=0, stdout="listing", stderr="")

    monkeypatch.setattr(run_command, "run_shell", fake_run_shell)
    result = await run_command._execute(
        {"command": "ls"},
        ToolContext(workspace=str(tmp_path), mode=Mode.ACT),
    )

    assert result.status == ToolStatus.SUCCESS_NO_CHANGE
    assert result.exit_code == 0
    assert "listing" in result.output


@pytest.mark.asyncio
async def test_successful_mutating_command_effect_is_unverified(monkeypatch, tmp_path):
    async def fake_run_shell(*args, **kwargs):
        return ShellResult(returncode=0, stdout="done", stderr="")

    monkeypatch.setattr(run_command, "run_shell", fake_run_shell)
    result = await run_command._execute(
        {"command": "python script.py"},
        ToolContext(workspace=str(tmp_path), mode=Mode.ACT),
    )

    assert result.status == ToolStatus.SUCCESS_EFFECT_UNKNOWN
    assert result.exit_code == 0
