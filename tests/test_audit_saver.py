"""Тесты audit_saver (B9.3): полный отчёт, findings, сводка."""

from __future__ import annotations

from agent_coder.agent.prompts import Mode
from agent_coder.agent.session import Session
from agent_coder.reports.audit_saver import (
    extract_audit_report,
    extract_audit_summary,
    format_work_state_findings,
    save_audit_report,
)


def _session(messages: list[dict], findings: list[str] | None = None) -> Session:
    s = Session.new(
        workspace="/tmp", model="deepseek/deepseek-v4-flash", mode=Mode.AUDIT
    )
    s.messages = list(messages)
    if findings is not None:
        s.work_state = {"findings": list(findings)}
    return s


def test_extract_picks_longest_not_first():
    """Баг до B9.3: брался первый >500 с конца (summary), а не полный отчёт."""
    summary = "Кратко: 5/10, NO-GO." + "x" * 600
    full = "ОТЧЁТ АУДИТА\n" + "y" * 8000
    s = _session(
        [
            {"role": "user", "content": "аудит"},
            {"role": "assistant", "content": full},
            {"role": "assistant", "content": summary},
        ]
    )
    assert extract_audit_report(s) == full


def test_extract_no_assistant_returns_empty():
    s = _session([{"role": "user", "content": "аудит"}])
    assert extract_audit_report(s) == "(пусто)"


def test_extract_ignores_assistant_before_last_user():
    s = _session(
        [
            {"role": "assistant", "content": "старое " * 500},
            {"role": "user", "content": "аудит"},
            {"role": "assistant", "content": "короткий ответ после user"},
        ]
    )
    assert extract_audit_report(s) == "короткий ответ после user"


def test_summary_is_last_and_shorter():
    full = "ОТЧЁТ " + "y" * 8000
    summary = "Кратко: 5/10."
    s = _session(
        [
            {"role": "user", "content": "аудит"},
            {"role": "assistant", "content": full},
            {"role": "assistant", "content": summary},
        ]
    )
    assert extract_audit_summary(s, full) == summary


def test_summary_empty_when_same_as_full():
    full = "ОТЧЁТ " + "y" * 8000
    s = _session(
        [
            {"role": "user", "content": "аудит"},
            {"role": "assistant", "content": full},
        ]
    )
    assert extract_audit_summary(s, full) == ""


def test_format_findings_empty():
    s = _session([], findings=[])
    assert format_work_state_findings(s) == ""


def test_format_findings_bullets():
    s = _session([], findings=["a.py:1 — bad", "b.py:2 — worse"])
    out = format_work_state_findings(s)
    assert "## Структурированные findings" in out
    assert "- a.py:1 — bad" in out
    assert "- b.py:2 — worse" in out


def test_save_audit_report_all_sections(tmp_path, monkeypatch):
    from agent_coder.config import config

    monkeypatch.setattr(config, "SESSIONS_DIR", tmp_path)
    full = "ОТЧЁТ " + "y" * 8000
    summary = "Кратко: 5/10."
    s = _session(
        [
            {"role": "user", "content": "аудит"},
            {"role": "assistant", "content": full},
            {"role": "assistant", "content": summary},
        ],
        findings=["pricing.py:13 — float", "notifications.py:97 — while True"],
    )
    path = save_audit_report(s)
    text = path.read_text(encoding="utf-8")
    assert "## Отчёт" in text
    assert "## Структурированные findings" in text
    assert "## Сводка" in text
    assert full in text
    assert summary in text
    assert "- pricing.py:13 — float" in text


def test_save_audit_report_no_findings_no_summary(tmp_path, monkeypatch):
    from agent_coder.config import config

    monkeypatch.setattr(config, "SESSIONS_DIR", tmp_path)
    full = "ОТЧЁТ " + "y" * 8000
    s = _session(
        [
            {"role": "user", "content": "аудит"},
            {"role": "assistant", "content": full},
        ],
        findings=[],
    )
    path = save_audit_report(s)
    text = path.read_text(encoding="utf-8")
    assert "## Отчёт" in text
    assert "## Структурированные findings" not in text
    assert "## Сводка" not in text
