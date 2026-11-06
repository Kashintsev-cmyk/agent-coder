"""B9.2h: scope-gate run_command в AUDIT (B11.3, ctx.audit_scopes)."""

from __future__ import annotations

from agent_coder.tools.run_command import _scope_violation

SCOPES = ["bot/"]


def test_in_scope_passes() -> None:
    assert _scope_violation("cat bot/a.py", SCOPES) is None
    assert _scope_violation("head -20 bot/a.py", SCOPES) is None
    assert _scope_violation("cat bot/*.py", SCOPES) is None


def test_out_of_scope_blocked() -> None:
    assert _scope_violation("cat config.py", SCOPES) is not None
    assert _scope_violation("wc -l src/x.py", SCOPES) is not None
    assert _scope_violation("cat *.py", SCOPES) is not None
    assert _scope_violation("cat ~/x", SCOPES) is not None
    assert _scope_violation("cat /etc/hosts", SCOPES) is not None


def test_grep_pattern_vs_paths() -> None:
    assert _scope_violation("grep -rn error bot/", SCOPES) is None
    assert _scope_violation("grep -A 2 error bot/x.log", SCOPES) is None
    assert _scope_violation("grep -rn error src/", SCOPES) is not None
    assert _scope_violation("rg -n TODO src/", SCOPES) is not None


def test_find_first_arg_only() -> None:
    assert _scope_violation("find bot -name '*.py'", SCOPES) is None
    assert _scope_violation("find . -name '*.py'", SCOPES) is not None


def test_sed_awk_script_not_path() -> None:
    assert _scope_violation("sed -n '1,5p' bot/a.py", SCOPES) is None
    assert _scope_violation("sed -n '1,5p'", SCOPES) is None
    assert _scope_violation("awk '{print $1}' bot/a.log", SCOPES) is None
    assert _scope_violation("awk -v x=1 '{print}' bot/a.log", SCOPES) is None
    assert _scope_violation("sed -n '1,5p' /etc/passwd", SCOPES) is not None
    assert _scope_violation("awk -v x=1 '{print}' src/a.log", SCOPES) is not None


def test_diagnostics_not_gated() -> None:
    assert _scope_violation("df -h", SCOPES) is None
    assert _scope_violation("wc -l", SCOPES) is None
    assert _scope_violation("stat -c '%s' bot/f", SCOPES) is None
    assert _scope_violation("journalctl -u bot | tail -20", SCOPES) is None


def test_scope_dot_allows_all() -> None:
    assert _scope_violation("cat /etc/hosts", ["."]) is None


def test_empty_scopes_no_gate() -> None:
    assert _scope_violation("cat config.py", []) is None


def test_flag_values_skipped() -> None:
    assert _scope_violation("head -n 5 bot/a.py", SCOPES) is None
    assert _scope_violation("tail -n 5 bot/a.py", SCOPES) is None


def test_execute_gates_in_audit() -> None:
    import asyncio

    from agent_coder.agent.prompts import Mode
    from agent_coder.tools.registry import ToolContext
    from agent_coder.tools.run_command import run_command_tool

    ctx = ToolContext(workspace=".", mode=Mode.ACT, audit_scopes=SCOPES)
    out = asyncio.run(
        run_command_tool.executor({"command": "cat config.py"}, ctx)
    )
    assert "вне области аудита" in out
