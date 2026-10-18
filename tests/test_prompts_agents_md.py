"""Сквозные тесты: AGENTS.md попадает в system_prompt для PLAN/ACT/CHAT."""

from __future__ import annotations

from pathlib import Path

import pytest

from agent_coder.agent import workspace_rules
from agent_coder.agent.prompts import (
    SYSTEM_ACT,
    SYSTEM_CHAT,
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


# (а) есть AGENTS.md — содержимое в промте PLAN, ACT и CHAT
@pytest.mark.parametrize("mode", [Mode.PLAN, Mode.ACT, Mode.CHAT])
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
        (Mode.CHAT, SYSTEM_CHAT),
    ],
)
def test_no_rules_backward_compat(
    tmp_path: Path,
    mode: Mode,
    template: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(workspace_rules, "append_builtin_standard", lambda p: p)
    ws = str(tmp_path)
    assert system_prompt(mode, ws) == template.format(workspace=ws)


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
    upper = (
        len(base.encode("utf-8"))
        + len(SECTION_HEADER)
        + MAX_AGENTS_MD_BYTES
        + len(TRUNCATION_MARKER)
        + len(SECTION_FOOTER)
    )
    assert len(out.encode("utf-8")) <= upper


# (г) CHAT теперь тоже подхватывает, хвост — базовый SYSTEM_CHAT
def test_chat_gets_agents_md(tmp_path: Path) -> None:
    _write(tmp_path, "SECRET_RULE_XYZ")
    ws = str(tmp_path)
    out = system_prompt(Mode.CHAT, ws)
    assert "SECRET_RULE_XYZ" in out
    assert out.startswith(SYSTEM_CHAT.format(workspace=ws))


# AUDIT не тронут
def test_audit_not_touched(tmp_path: Path) -> None:
    _write(tmp_path, "SECRET_RULE_XYZ")
    ws = str(tmp_path)
    from agent_coder.agent.audit_prompt import build_audit_prompt

    assert system_prompt(Mode.AUDIT, ws) == build_audit_prompt(workspace=ws)
    assert "SECRET_RULE_XYZ" not in system_prompt(Mode.AUDIT, ws)


# (д) падение workspace_rules -> откат на base, для PLAN/ACT/CHAT
@pytest.mark.parametrize(
    "mode,template",
    [
        (Mode.PLAN, SYSTEM_PLAN),
        (Mode.ACT, SYSTEM_ACT),
        (Mode.CHAT, SYSTEM_CHAT),
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
    assert out == template.format(workspace=ws)


def test_system_act_has_work_state_rule() -> None:
    """Правило 14 — про structured memory, не меняя формат SYSTEM_*."""
    from agent_coder.agent.prompts import SYSTEM_ACT

    assert "update_work_state" in SYSTEM_ACT
    assert (
        "structured memory" in SYSTEM_ACT.lower() or "Structured memory" in SYSTEM_ACT
    )
