"""Публичный API стандарта: Issue, FixSpec, paths, adapter."""

from __future__ import annotations

from pathlib import Path

import pytest

from agent_coder.standard import (
    ENV_BOTS_DIR,
    FIX_SPECS,
    Issue,
    bots_dir,
    collect_issues,
    fix_for,
    project_dir,
    summarize,
)


def test_issue_to_dict_matches_tool_shape() -> None:
    i = Issue(
        path="pyproject.toml",
        severity="major",
        issue="requires_python_below_312",
        expected=">=3.12",
        found=">=3.10",
    )
    d = i.to_dict()
    assert set(d) == {"path", "severity", "issue", "expected", "found"}
    assert d["severity"] == "major"


def test_issue_round_trip() -> None:
    src = {
        "path": "README.md",
        "severity": "minor",
        "issue": "missing_file",
        "expected": "file present",
        "found": "not found",
    }
    assert Issue.from_dict(src).to_dict() == src


def test_fix_for_known_id() -> None:
    spec = fix_for("requires_python_below_312")
    assert spec is not None
    assert spec.kind == "edit_pyproject"
    assert ">=3.12" in spec.hint


def test_fix_for_unknown_id() -> None:
    assert fix_for("no_such_issue_id") is None


def test_fix_specs_keys_unique_and_nonempty() -> None:
    assert len(FIX_SPECS) >= 10
    for key, spec in FIX_SPECS.items():
        assert spec.issue_id == key
        assert spec.hint


def test_bots_dir_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(ENV_BOTS_DIR, raising=False)
    assert bots_dir() == Path("/home/botuser/bots")


def test_bots_dir_env_override(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv(ENV_BOTS_DIR, str(tmp_path))
    assert bots_dir() == tmp_path
    assert project_dir("x") == tmp_path / "x"


def test_collect_issues_on_self() -> None:
    """Сам agent-coder: adapter возвращает Issue, JSON-контракт сохранён."""
    issues = collect_issues(Path(__file__).resolve().parents[1])
    assert isinstance(issues, list)
    assert all(isinstance(i, Issue) for i in issues)
    summary = summarize(issues)
    assert set(summary) == {"score", "majors", "minors", "issues"}
    assert summary["score"] in {"ok", "warnings", "failures", "skipped"}
