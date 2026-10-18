"""migrate_one: state + .md, notes не трогаются, идемпотентность."""

from __future__ import annotations

from pathlib import Path

import pytest

from agent_coder import migrate
from agent_coder.standard import ENV_BOTS_DIR, ENV_NOTES_DIR, ENV_STATE_DIR


def _make_project(root: Path, name: str) -> Path:
    """Минимальный проект: pyproject без стандарта -> 1+ issue."""
    p = root / name
    p.mkdir(parents=True)
    (p / "README.md").write_text("# x\n", encoding="utf-8")
    (p / "pyproject.toml").write_text(
        '[project]\nname = "x"\nversion = "0.1.0"\n', encoding="utf-8"
    )
    return p


@pytest.fixture()
def env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, Path]:
    bots = tmp_path / "bots"
    notes = tmp_path / "notes"
    state = tmp_path / "state"
    bots.mkdir()
    notes.mkdir()
    state.mkdir()
    monkeypatch.setenv(ENV_BOTS_DIR, str(bots))
    monkeypatch.setenv(ENV_NOTES_DIR, str(notes))
    monkeypatch.setenv(ENV_STATE_DIR, str(state))
    return {"bots": bots, "notes": notes, "state": state}


def test_migrate_writes_state_and_md(env: dict[str, Path]) -> None:
    _make_project(env["bots"], "demo")
    r = migrate.migrate_one("demo")
    assert "error" not in r
    assert Path(r["state"]).is_file()
    assert Path(r["md"]).is_file()
    assert r["md_changed"] is True


def test_second_run_md_unchanged(env: dict[str, Path]) -> None:
    _make_project(env["bots"], "demo")
    migrate.migrate_one("demo")
    r2 = migrate.migrate_one("demo")
    assert r2["md_changed"] is False, "second run must not rewrite .md"


def test_notes_not_touched(env: dict[str, Path]) -> None:
    _make_project(env["bots"], "demo")
    notes_file = env["notes"] / "demo.notes.md"
    notes_file.write_text("- [x] done\n", encoding="utf-8")
    before = notes_file.read_text(encoding="utf-8")
    migrate.migrate_one("demo")
    after = notes_file.read_text(encoding="utf-8")
    assert before == after, ".notes.md is human-owned, must not be rewritten"


def test_notes_embedded_in_md(env: dict[str, Path]) -> None:
    _make_project(env["bots"], "demo")
    (env["notes"] / "demo.notes.md").write_text("- [x] done\n", encoding="utf-8")
    r = migrate.migrate_one("demo")
    md = Path(r["md"]).read_text(encoding="utf-8")
    assert "- [x] done" in md


def test_missing_project_returns_error(env: dict[str, Path]) -> None:
    r = migrate.migrate_one("nope")
    assert r["error"] == "not_a_dir"


def test_json_mode_no_write(env: dict[str, Path]) -> None:
    _make_project(env["bots"], "demo")
    r = migrate.migrate_one("demo", write=False)
    assert "state" not in r
    assert not (env["notes"] / "demo.md").exists()
    assert not (env["state"] / "demo" / "state.json").exists()


def test_cli_main_requires_project(env: dict[str, Path], capsys) -> None:
    import sys

    argv = sys.argv
    sys.argv = ["agent-coder-migrate"]
    try:
        with pytest.raises(SystemExit) as exc:
            migrate.main()
        assert exc.value.code == 2
    finally:
        sys.argv = argv


def test_cli_main_ok(env: dict[str, Path]) -> None:
    import sys

    _make_project(env["bots"], "demo")
    argv = sys.argv
    sys.argv = ["agent-coder-migrate", "--project", "demo"]
    try:
        rc = migrate.main()
    finally:
        sys.argv = argv
    assert rc == 0
    assert (env["notes"] / "demo.md").is_file()
