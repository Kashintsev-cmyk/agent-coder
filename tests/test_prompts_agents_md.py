"""Сквозные тесты: AGENTS.md попадает в system_prompt для PLAN/ACT."""

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


# (а) есть AGENTS.md — содержимое в промте PLAN и ACT
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
    [(Mode.PLAN, SYSTEM_PLAN), (Mode.ACT, SYSTEM_ACT)],
)
def test_no_file_backward_compat(tmp_path: Path, mode: Mode, template: str) -> None:
    ws = str(tmp_path)
    assert system_prompt(mode, ws) == template.format(workspace=ws)


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


# (г) CHAT и AUDIT не тронуты при наличии AGENTS.md
def test_chat_not_touched(tmp_path: Path) -> None:
    _write(tmp_path, "SECRET_RULE_XYZ")
    ws = str(tmp_path)
    assert system_prompt(Mode.CHAT, ws) == SYSTEM_CHAT.format(workspace=ws)
    assert "SECRET_RULE_XYZ" not in system_prompt(Mode.CHAT, ws)


def test_audit_not_touched(tmp_path: Path) -> None:
    _write(tmp_path, "SECRET_RULE_XYZ")
    ws = str(tmp_path)
    from agent_coder.agent.audit_prompt import build_audit_prompt

    assert system_prompt(Mode.AUDIT, ws) == build_audit_prompt(workspace=ws)
    assert "SECRET_RULE_XYZ" not in system_prompt(Mode.AUDIT, ws)


# (д) падение workspace_rules -> откат на base, без исключения
def test_failure_falls_back_to_base(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _write(tmp_path, "x")

    def boom(*_a: object, **_kw: object) -> str:
        raise RuntimeError("boom")

    monkeypatch.setattr(workspace_rules, "append_agents_md", boom)
    # system_prompt импортирует append_agents_md внутри функции,
    # поэтому подмены в модуле workspace_rules достаточно.
    out = system_prompt(Mode.PLAN, str(tmp_path))
    assert out == SYSTEM_PLAN.format(workspace=str(tmp_path))
