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


def test_pytest_command_has_noconftest() -> None:
    """M3 (8f0cd848): pytest запускается с --noconftest (RCE-вектор закрыт)."""
    from agent_coder.tools.run_checks import _CHECKS

    pytest_cmds = [c for c, _ in _CHECKS if "pytest" in c]
    assert pytest_cmds, "в _CHECKS нет pytest-команды"
    for cmd in pytest_cmds:
        assert "--noconftest" in cmd, cmd
        assert "-p no:cacheprovider" in cmd, cmd
