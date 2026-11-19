"""state.py: path, round-trip, atomic write, write_if_changed."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

import pytest

from agent_coder.standard import (
    ENV_STATE_DIR,
    StateWriteError,
    build_state,
    read_state,
    state_dir,
    state_path,
    write_if_changed,
    write_state,
)


def _summary() -> dict:
    return {
        "score": "warnings",
        "majors": 0,
        "minors": 1,
        "issues": [
            {
                "path": "pyproject.toml",
                "severity": "minor",
                "issue": "ruff_target_version_not_py312",
                "expected": "py312",
                "found": "py310",
            }
        ],
    }


def test_state_dir_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(ENV_STATE_DIR, raising=False)
    assert state_dir() == Path.home() / ".agent-coder" / "projects"


def test_state_dir_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv(ENV_STATE_DIR, str(tmp_path))
    assert state_dir() == tmp_path


def test_state_path_shape(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv(ENV_STATE_DIR, str(tmp_path))
    assert state_path("x") == tmp_path / "x" / "state.json"


def test_read_state_missing(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv(ENV_STATE_DIR, str(tmp_path))
    assert read_state("nope") is None


def test_read_state_broken_json(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv(ENV_STATE_DIR, str(tmp_path))
    p = tmp_path / "x" / "state.json"
    p.parent.mkdir(parents=True)
    p.write_text("not json {", encoding="utf-8")
    assert read_state("x") is None


def test_write_read_round_trip(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv(ENV_STATE_DIR, str(tmp_path))
    state = build_state("x", _summary())
    p = write_state("x", state)
    assert p.is_file()
    assert read_state("x") == state


def test_build_state_shape() -> None:
    state = build_state("x", _summary())
    assert set(state) == {
        "project",
        "generated_at",
        "score",
        "majors",
        "minors",
        "issues",
    }
    assert state["project"] == "x"
    assert state["score"] == "warnings"
    ts = datetime.fromisoformat(state["generated_at"])
    assert ts.tzinfo is not None, "generated_at must be tz-aware (UTC)"


def test_write_state_no_tmp_leftover(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv(ENV_STATE_DIR, str(tmp_path))
    p = write_state("x", build_state("x", _summary()))
    assert not p.with_suffix(".json.tmp").exists()


def test_write_state_creates_parents(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv(ENV_STATE_DIR, str(tmp_path))
    p = write_state("deep/nested", {"a": 1})
    assert p.is_file()
    assert p.parent.is_dir()


def test_write_if_changed_first_second_third(tmp_path: Path) -> None:
    p = tmp_path / "a.txt"
    assert write_if_changed(p, "hello") is True
    assert write_if_changed(p, "hello") is False
    assert write_if_changed(p, "world") is True
    assert p.read_text(encoding="utf-8") == "world"


def test_write_if_changed_creates_parents(tmp_path: Path) -> None:
    p = tmp_path / "a" / "b" / "c.txt"
    assert write_if_changed(p, "x") is True
    assert p.is_file()


def test_write_state_raises_clear_error_on_readonly_dir(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Read-only ФС (прод: ProtectHome=read-only) — StateWriteError с путём."""
    monkeypatch.setenv(ENV_STATE_DIR, str(tmp_path / "state"))
    # каталог на месте, но родитель — файл: mkdir упадёт с ENOTDIR/OSError
    blocker = tmp_path / "state"
    blocker.write_text("not a dir", encoding="utf-8")
    with pytest.raises(StateWriteError) as exc:
        write_state("demo", {"a": 1})
    assert exc.value.path == state_path("demo")
    assert "state" in str(exc.value)
    assert exc.value.cause is not None


def test_write_if_changed_raises_clear_error(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    blocker = tmp_path / "dir"
    blocker.write_text("not a dir", encoding="utf-8")
    with pytest.raises(StateWriteError):
        write_if_changed(blocker / "x.md", "content")


def test_state_write_error_is_runtime_error() -> None:
    """Хендлеры ловят StateWriteError; он должен быть RuntimeError-совместим."""
    assert issubclass(StateWriteError, RuntimeError)
