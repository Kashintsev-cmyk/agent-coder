"""F16: check_project_standard ловит неприменённый ruff format."""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from agent_coder.standard import collect_issues, summarize
from agent_coder.tools.check_project_standard import _collect_issues


def _min_project(root: Path) -> Path:
    p = root / "proj"
    p.mkdir(parents=True)
    (p / "pyproject.toml").write_text(
        '[project]\nname = "x"\nversion = "0.1.0"\n', encoding="utf-8"
    )
    return p


def _issue_ids(issues: list[dict]) -> set[str]:
    return {i["issue"] for i in issues}


@pytest.mark.skipif(shutil.which("ruff") is None, reason="ruff not in PATH")
def test_unformatted_file_triggers_issue(tmp_path: Path) -> None:
    p = _min_project(tmp_path)
    # Явно неотформатированный код (много пробелов внутри строки)
    (p / "messy.py").write_text("def f( a,b ):\n    return   a+b\n", encoding="utf-8")
    issues = _collect_issues(p)
    assert "ruff_format_not_applied" in _issue_ids(issues)


@pytest.mark.skipif(shutil.which("ruff") is None, reason="ruff not in PATH")
def test_formatted_file_no_issue(tmp_path: Path) -> None:
    p = _min_project(tmp_path)
    (p / "clean.py").write_text(
        "def f(a: int, b: int) -> int:\n    return a + b\n", encoding="utf-8"
    )
    issues = _collect_issues(p)
    assert "ruff_format_not_applied" not in _issue_ids(issues)


def test_no_ruff_in_path_skips(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    p = _min_project(tmp_path)
    (p / "messy.py").write_text("def f( a,b ):\n    return   a+b\n", encoding="utf-8")
    monkeypatch.setattr(
        "agent_coder.tools.check_project_standard.shutil.which",
        lambda _name: None,
    )
    issues = _collect_issues(p)
    assert "ruff_format_not_applied" not in _issue_ids(issues)


def test_no_pyproject_skips(tmp_path: Path) -> None:
    p = tmp_path / "empty"
    p.mkdir()
    issues = _collect_issues(p)
    assert "ruff_format_not_applied" not in _issue_ids(issues)


@pytest.mark.skipif(shutil.which("ruff") is None, reason="ruff not in PATH")
def test_adapter_exposes_issue_as_dataclass(tmp_path: Path) -> None:
    """collect_issues (публичный adapter) видит новую проверку."""
    p = _min_project(tmp_path)
    (p / "messy.py").write_text("def f( a,b ):\n    return   a+b\n", encoding="utf-8")
    issues = collect_issues(p)
    assert any(i.issue == "ruff_format_not_applied" for i in issues)
    summary = summarize(issues)
    assert summary["minors"] >= 1
