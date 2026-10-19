"""Тесты agent_coder.agent.audit_prompt (AUDIT_PROMPT -> .md loader)."""

from __future__ import annotations

import pytest

from agent_coder.agent import audit_prompt
from agent_coder.agent.audit_prompt import build_audit_prompt
from agent_coder.rules import load_audit_standard


@pytest.fixture(autouse=True)
def _clear_cache():
    load_audit_standard.cache_clear()
    yield
    load_audit_standard.cache_clear()


def test_audit_standard_md_non_empty() -> None:
    text = load_audit_standard()
    assert text
    assert "РОЛЬ" in text
    assert "ЭТАП 1" in text
    assert "ЭТАП 4" in text
    assert "ЧЕК-ЛИСТ" in text.upper()


def test_build_audit_prompt_substitutes_all_placeholders() -> None:
    out = build_audit_prompt(
        workspace="/tmp/ws",
        project_name="demo",
        entrypoint="main.py",
        python_version="3.10",
        dependencies="openai,httpx",
        domain="bot",
        requirements="100 rps",
        priorities="security",
    )
    for val in (
        "/tmp/ws",
        "demo",
        "main.py",
        "3.10",
        "openai,httpx",
        "bot",
        "100 rps",
        "security",
    ):
        assert val in out
    for key in (
        "workspace",
        "project_name",
        "entrypoint",
        "python_version",
        "dependencies",
        "domain",
        "requirements",
        "priorities",
    ):
        assert "{" + key + "}" not in out, f"unfilled placeholder: {key}"


def test_project_name_defaults_to_workspace_basename() -> None:
    out = build_audit_prompt(workspace="/home/botuser/bots/na_shtile")
    assert "na_shtile" in out


def test_build_audit_prompt_empty_template(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(audit_prompt, "load_audit_standard", lambda: "")
    assert build_audit_prompt(workspace="/tmp/ws") == ""


def test_fill_does_not_break_on_curly_braces() -> None:
    # Пример кода в markdown может содержать { } — replace не должен падать
    template = "A={workspace} B={a: 1}"
    out = audit_prompt._fill(template, workspace="/tmp/ws")
    assert out == "A=/tmp/ws B={a: 1}"


def test_has_anti_hallucination_block() -> None:
    text = load_audit_standard()
    assert "# ГЛАВНОЕ ПРАВИЛО" in text
    assert "Ложный finding хуже пропущенного" in text
    assert "не подтверждено" in text


def test_has_confidence_column() -> None:
    text = load_audit_standard()
    assert "Confidence" in text
    assert "HIGH (код прочитан" in text
    assert "MEDIUM (косвенные" in text
    assert "LOW (эвристика" in text


def test_has_executive_summary() -> None:
    text = load_audit_standard()
    assert "## 0. EXECUTIVE SUMMARY" in text
    assert "GO / NO-GO" in text
    assert "Top-3 блокера" in text


def test_mentions_check_project_standard() -> None:
    text = load_audit_standard()
    assert "check_project_standard" in text
    assert "## 1.15. Соответствие стандарту" in text


def test_findings_cap_documented() -> None:
    text = load_audit_standard()
    assert "30 findings" in text
    assert "Ниже порога" in text


def test_audit_standard_has_findings_rule() -> None:
    """B9.1: правило записи findings после каждого turn в AUDIT."""
    text = load_audit_standard()
    assert "## ПРАВИЛО: запись findings после каждого turn" in text
    assert "update_work_state" in text
    assert "НЕ батчи findings на конец аудита" in text
    assert "read_file" in text


def test_work_state_rule_hardens_audit() -> None:
    """B9.1: _WORK_STATE_RULE содержит жёсткое правило для AUDIT."""
    from agent_coder.agent.prompts import _WORK_STATE_RULE

    assert "В AUDIT — особо жёстко" in _WORK_STATE_RULE
    assert "после КАЖДОГО turn" in _WORK_STATE_RULE
    # В константе разрыв строки: "...не\nбатчи на конец аудита" — "не" опускаем
    assert "батчи на конец аудита" in _WORK_STATE_RULE
