"""Сквозные тесты: AGENTS.md попадает в system_prompt для PLAN/ACT."""

from __future__ import annotations

from pathlib import Path

import pytest

from agent_coder.agent import workspace_rules
from agent_coder.agent.prompts import (
    SYSTEM_ACT,
    SYSTEM_PLAN,
    Mode,
    system_prompt,
)
from agent_coder.agent.workspace_rules import (
    MAX_AGENTS_MD_BYTES,
    SECTION_FOOTER,
    SECTION_HEADER,
    TRUNCATION_MARKER,
    clear_cache,
)


@pytest.fixture(autouse=True)
def _clean_cache():
    clear_cache()
    yield
    clear_cache()


def _write(tmp_path: Path, text: str) -> None:
    (tmp_path / "AGENTS.md").write_text(text, encoding="utf-8")


# (а) есть AGENTS.md — содержимое в промте PLAN, ACT
@pytest.mark.parametrize("mode", [Mode.PLAN, Mode.ACT])
def test_agents_md_added_to_prompt(tmp_path: Path, mode: Mode) -> None:
    _write(tmp_path, "use ruff and pytest")
    out = system_prompt(mode, str(tmp_path))
    assert "use ruff and pytest" in out
    assert SECTION_HEADER.strip() in out
    assert SECTION_FOOTER.strip() in out


# (б) нет файла — промт как раньше, байт-в-байт
@pytest.mark.parametrize(
    "mode,template",
    [
        (Mode.PLAN, SYSTEM_PLAN),
        (Mode.ACT, SYSTEM_ACT),
    ],
)
def test_no_rules_backward_compat(
    tmp_path: Path,
    mode: Mode,
    template: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Без AGENTS.md и builtin: PLAN/ACT = template + _WORK_STATE_RULE."""
    from agent_coder.agent.prompts import _WORK_STATE_RULE

    monkeypatch.setattr(workspace_rules, "append_builtin_standard", lambda p: p)
    ws = str(tmp_path)
    expected = template.format(workspace=ws)
    expected = expected + "\n\n" + _WORK_STATE_RULE
    assert system_prompt(mode, ws) == expected


def test_builtin_standard_present_when_no_agents_md(tmp_path: Path) -> None:
    ws = str(tmp_path)
    out = system_prompt(Mode.PLAN, ws)
    assert "--- PROJECT STANDARD (builtin) ---" in out
    assert "Стандарт оформления" in out


def test_builtin_standard_absent_when_agents_md_present(tmp_path: Path) -> None:
    _write(tmp_path, "project-specific rule")
    ws = str(tmp_path)
    out = system_prompt(Mode.PLAN, ws)
    assert "--- PROJECT STANDARD (builtin) ---" not in out
    assert "project-specific rule" in out


# (в) файл больше лимита — обрезан + маркер, сквозь промт
def test_truncated_through_prompt(tmp_path: Path) -> None:
    _write(tmp_path, "a" * (MAX_AGENTS_MD_BYTES + 1))
    ws = str(tmp_path)
    base = SYSTEM_PLAN.format(workspace=ws)
    out = system_prompt(Mode.PLAN, ws)
    assert TRUNCATION_MARKER.strip() in out
    assert SECTION_HEADER.strip() in out
    assert SECTION_FOOTER.strip() in out
    assert "a" * MAX_AGENTS_MD_BYTES in out
    assert "a" * (MAX_AGENTS_MD_BYTES + 1) not in out
    from agent_coder.agent.prompts import _WORK_STATE_RULE

    upper = (
        len(base.encode("utf-8"))
        + len(SECTION_HEADER)
        + MAX_AGENTS_MD_BYTES
        + len(TRUNCATION_MARKER)
        + len(SECTION_FOOTER)
        + len(("\n\n" + _WORK_STATE_RULE).encode("utf-8"))
    )
    assert len(out.encode("utf-8")) <= upper


# AUDIT не тронут
def test_audit_not_touched(tmp_path: Path) -> None:
    _write(tmp_path, "SECRET_RULE_XYZ")
    ws = str(tmp_path)
    from agent_coder.agent.audit_prompt import build_audit_prompt
    from agent_coder.agent.prompts import _WORK_STATE_RULE

    assert system_prompt(Mode.AUDIT, ws) == (
        build_audit_prompt(workspace=ws) + "\n\n" + _WORK_STATE_RULE
    )
    assert "SECRET_RULE_XYZ" not in system_prompt(Mode.AUDIT, ws)


# (д) падение workspace_rules -> откат на base, для PLAN/ACT
@pytest.mark.parametrize(
    "mode,template",
    [
        (Mode.PLAN, SYSTEM_PLAN),
        (Mode.ACT, SYSTEM_ACT),
    ],
)
def test_failure_falls_back_to_base(
    tmp_path: Path,
    mode: Mode,
    template: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _write(tmp_path, "x")

    def boom(*_a: object, **_kw: object) -> str:
        raise RuntimeError("boom")

    monkeypatch.setattr(workspace_rules, "append_agents_md", boom)
    ws = str(tmp_path)
    out = system_prompt(mode, ws)
    from agent_coder.agent.prompts import _WORK_STATE_RULE

    expected = template.format(workspace=ws)
    expected = expected + "\n\n" + _WORK_STATE_RULE
    assert out == expected


def test_work_state_rule_is_shared_across_modes() -> None:
    """Единое правило structured memory инжектится в PLAN/ACT/AUDIT."""
    from agent_coder.agent.prompts import _WORK_STATE_RULE

    assert "update_work_state" in _WORK_STATE_RULE
    for mode in (Mode.PLAN, Mode.ACT, Mode.AUDIT):
        assert "update_work_state" in system_prompt(mode, "/tmp")


def test_system_act_no_duplicate_rule() -> None:
    """Правило в _WORK_STATE_RULE, не в SYSTEM_ACT (один источник)."""
    from agent_coder.agent.prompts import SYSTEM_ACT

    assert "update_work_state" not in SYSTEM_ACT
