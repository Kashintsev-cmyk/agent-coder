"""render_markdown: детерминизм, FIX_SPECS, notes, escape."""

from __future__ import annotations

from pathlib import Path

import pytest

from agent_coder.standard import (
    ENV_NOTES_DIR,
    notes_path,
    read_notes,
    render_markdown,
)


def _summary(majors: int = 0, minors: int = 0) -> dict:
    issues = []
    for k in range(majors):
        issues.append(
            {
                "path": f"f{k}.py",
                "severity": "major",
                "issue": "requires_python_below_312",
                "expected": ">=3.12",
                "found": ">=3.10",
            }
        )
    for k in range(minors):
        issues.append(
            {
                "path": f"m{k}.py",
                "severity": "minor",
                "issue": "ruff_target_version_not_py312",
                "expected": "py312",
                "found": "py310",
            }
        )
    score = "failures" if majors else ("warnings" if minors else "ok")
    return {
        "score": score,
        "majors": majors,
        "minors": minors,
        "issues": issues,
    }


def test_deterministic() -> None:
    s = _summary(majors=1, minors=1)
    a = render_markdown("proj", s, notes_md="")
    b = render_markdown("proj", s, notes_md="")
    assert a == b, "render must be idempotent (no timestamp)"


def test_no_timestamp_in_output() -> None:
    out = render_markdown("proj", _summary())
    assert "Generated:" not in out


def test_score_ok_sections() -> None:
    out = render_markdown("proj", _summary())
    assert "Score: **ok**" in out
    assert "## Critical (major)" in out
    assert "- (нет)" in out


def test_critical_row_contains_fix_hint() -> None:
    out = render_markdown("proj", _summary(majors=1))
    assert "requires_python_below_312" in out
    assert "**fix:**" in out
    assert ">=3.12" in out


def test_notes_embedded() -> None:
    out = render_markdown("proj", _summary(), notes_md="- [x] done\n")
    assert "- [x] done" in out


def test_notes_absent_placeholder() -> None:
    out = render_markdown("proj", _summary(), notes_md="")
    assert "proj.notes.md" in out


def test_escape_backticks_in_found() -> None:
    s = _summary(majors=1)
    s["issues"][0]["found"] = "`weird`"
    out = render_markdown("proj", s)
    assert "\\`weird\\`" in out


def test_notes_path_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(ENV_NOTES_DIR, raising=False)
    p = notes_path("x")
    assert p.name == "x.notes.md"
    assert p.parent == Path("/home/botuser/bots/_migrations")


def test_notes_path_env_override(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv(ENV_NOTES_DIR, str(tmp_path))
    assert notes_path("x") == tmp_path / "x.notes.md"


def test_read_notes_missing(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(ENV_NOTES_DIR, str(tmp_path))
    assert read_notes("nope") == ""


def test_read_notes_existing(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(ENV_NOTES_DIR, str(tmp_path))
    (tmp_path / "p.notes.md").write_text("hello", encoding="utf-8")
    assert read_notes("p") == "hello"
