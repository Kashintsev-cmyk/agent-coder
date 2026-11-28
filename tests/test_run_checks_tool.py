"""B9.2h: инструмент run_checks — фиксированные проверки, read-only."""

from __future__ import annotations

from agent_coder.tools.run_checks import run_checks_tool


def test_tool_metadata() -> None:
    assert run_checks_tool.name == "run_checks"
    assert run_checks_tool.read_only is True
    assert run_checks_tool.requires_approval is False
    assert run_checks_tool.parameters == {"type": "object", "properties": {}}


def test_registered_and_allowed_in_plan_audit() -> None:
    from agent_coder.agent.prompts import Mode
    from agent_coder.tools import build_registry

    reg = build_registry()
    tool = reg.get("run_checks")
    assert tool is not None
    assert reg.is_call_allowed(Mode.PLAN, tool, {})
    assert reg.is_call_allowed(Mode.AUDIT, tool, {})
    assert tool in reg.for_mode(Mode.PLAN)


def test_checks_cover_mypy_and_bandit() -> None:
    """mypy и bandit входят в цепочку проверок (как в CI), и они «мягкие»."""
    from agent_coder.tools.run_checks import _CHECKS

    by_tool = {
        ("mypy" if "mypy" in c else "bandit" if "bandit" in c else "other"): (soft)
        for c, _timeout, soft in _CHECKS
    }
    assert "mypy" in by_tool, "mypy отсутствует в _CHECKS"
    assert "bandit" in by_tool, "bandit отсутствует в _CHECKS"
    assert by_tool["mypy"] is True, (
        "mypy должен быть soft (в чужом проекте может не быть)"
    )
    assert by_tool["bandit"] is True, "bandit должен быть soft"


def test_not_configured_detected_for_missing_bandit_config() -> None:
    """Живой probe 2026-11-21: bandit без bandit.yaml → exit=2 + этот текст.

    Без маркера чужой проект получал бы ложный «провал проверки».
    """
    from types import SimpleNamespace

    from agent_coder.tools.run_checks import _is_not_configured

    result = SimpleNamespace(
        stdout="",
        stderr="[main]\tERROR\tbandit.yaml : Could not read config file.",
    )
    assert _is_not_configured(result)


def test_not_configured_detected_for_missing_mypy() -> None:
    from types import SimpleNamespace

    from agent_coder.tools.run_checks import _is_not_configured

    result = SimpleNamespace(stdout="", stderr="bash: mypy: command not found")
    assert _is_not_configured(result)


def test_real_failure_is_not_treated_as_missing_tool() -> None:
    """Настоящая находка bandit/mypy не должна маскироваться под «пропущено»."""
    from types import SimpleNamespace

    from agent_coder.tools.run_checks import _is_not_configured

    result = SimpleNamespace(
        stdout="agent_coder/x.py:3: error: Incompatible return value type",
        stderr="",
    )
    assert not _is_not_configured(result)


def test_pytest_command_has_noconftest() -> None:
    """M3 (8f0cd848): pytest запускается с --noconftest (RCE-вектор закрыт)."""
    from agent_coder.tools.run_checks import _CHECKS

    pytest_cmds = [c for c, _, _soft in _CHECKS if "pytest" in c]
    assert pytest_cmds, "в _CHECKS нет pytest-команды"
    for cmd in pytest_cmds:
        assert "--noconftest" in cmd, cmd
        assert "-p no:cacheprovider" in cmd, cmd


async def test_failed_check_returns_error_status(monkeypatch) -> None:
    from types import SimpleNamespace

    from agent_coder.agent.prompts import Mode
    from agent_coder.tools import run_checks
    from agent_coder.tools.registry import ToolContext, ToolStatus

    monkeypatch.setattr(run_checks, "_CHECKS", [("pytest", 10, False)])

    async def failed_command(*args, **kwargs):
        return SimpleNamespace(
            timed_out=False,
            error=None,
            returncode=1,
            stdout="1 failed",
            stderr="",
        )

    monkeypatch.setattr(run_checks, "run_shell", failed_command)
    result = await run_checks._execute_result(
        {}, ToolContext(workspace="/tmp", mode=Mode.ACT)
    )

    assert result.status == ToolStatus.ERROR
    assert result.exit_code == 1
    assert "1 failed" in result.output
