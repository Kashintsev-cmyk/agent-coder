"""scaffold CLI: argc, --json, --no-git, error на непустом target."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from agent_coder import scaffold_cli
from agent_coder.standard import ENV_BOTS_DIR


def _run(argv: list[str], monkeypatch: pytest.MonkeyPatch, tmp: Path) -> int:
    monkeypatch.setenv(ENV_BOTS_DIR, str(tmp))
    old = sys.argv
    sys.argv = ["agent-coder-scaffold", *argv]
    try:
        return scaffold_cli.main()
    finally:
        sys.argv = old


def test_missing_project_argc(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    with pytest.raises(SystemExit) as exc:
        _run([], monkeypatch, tmp_path)
    assert exc.value.code == 2


def test_invalid_name_returns_2(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    rc = _run(["--project", "123bad"], monkeypatch, tmp_path)
    assert rc == 2


def test_ok_creates_project(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys
) -> None:
    rc = _run(["--project", "demo", "--no-git"], monkeypatch, tmp_path)
    assert rc == 0
    assert (tmp_path / "demo" / "pyproject.toml").is_file()


def test_json_mode(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys) -> None:
    rc = _run(["--project", "demo", "--no-git", "--json"], monkeypatch, tmp_path)
    assert rc == 0
    out = capsys.readouterr().out
    data = json.loads(out)
    assert data["project"] == "demo"
    assert "error" not in data


def test_nonempty_target_returns_1(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    target = tmp_path / "demo"
    target.mkdir()
    (target / "x.txt").write_text("x", encoding="utf-8")
    rc = _run(
        ["--project", "demo", "--no-git", "--target", str(target)],
        monkeypatch,
        tmp_path,
    )
    assert rc == 1


def test_description_passed(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    rc = _run(
        ["--project", "demo", "--no-git", "--description", "Hello"],
        monkeypatch,
        tmp_path,
    )
    assert rc == 0
    text = (tmp_path / "demo" / "pyproject.toml").read_text(encoding="utf-8")
    assert 'description = "Hello"' in text
