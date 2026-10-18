"""Telegram /migrate handlers: dry-run, apply, cancel.

Core logic (state/md/notes) — tests/test_migrate.py; здесь UI-слой.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from agent_coder.standard import ENV_BOTS_DIR, ENV_NOTES_DIR, ENV_STATE_DIR
from agent_coder.telegram.handlers import migrate as mig


def _make_project(root: Path, name: str) -> Path:
    p = root / name
    p.mkdir(parents=True)
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


def _msg(text: str) -> MagicMock:
    m = MagicMock()
    m.text = text
    m.answer = AsyncMock()
    return m


def _cb(data: str) -> MagicMock:
    q = MagicMock()
    q.data = data
    q.answer = AsyncMock()
    q.message = MagicMock()
    q.message.edit_text = AsyncMock()
    q.message.answer = AsyncMock()
    return q


async def test_cmd_migrate_no_arg_lists(env: dict[str, Path]) -> None:
    _make_project(env["bots"], "demo")
    m = _msg("/migrate")
    await mig.cmd_migrate(m)
    m.answer.assert_awaited_once()
    text = m.answer.call_args.args[0]
    assert "Миграция" in text
    kb = m.answer.call_args.kwargs["reply_markup"]
    labels = [btn.text for row in kb.inline_keyboard for btn in row]
    assert any("demo" in s for s in labels)
    assert any("Отмена" in s for s in labels)


async def test_cmd_migrate_with_arg_dry_run(env: dict[str, Path]) -> None:
    _make_project(env["bots"], "demo")
    m = _msg("/migrate demo")
    await mig.cmd_migrate(m)
    m.answer.assert_awaited_once()
    text = m.answer.call_args.args[0]
    assert "demo" in text
    assert "Score" in text
    assert "state" in text
    assert "md" in text
    kb = m.answer.call_args.kwargs["reply_markup"]
    labels = [btn.text for row in kb.inline_keyboard for btn in row]
    assert any("Применить" in s for s in labels)


async def test_cmd_migrate_invalid_name(env: dict[str, Path]) -> None:
    m = _msg("/migrate ../etc")
    await mig.cmd_migrate(m)
    text = m.answer.call_args.args[0]
    assert "недопустимое" in text


async def test_cmd_migrate_missing_project(env: dict[str, Path]) -> None:
    m = _msg("/migrate nope")
    await mig.cmd_migrate(m)
    text = m.answer.call_args.args[0]
    assert "Нет проекта" in text


async def test_cb_apply_writes_state_and_md(env: dict[str, Path]) -> None:
    _make_project(env["bots"], "demo")
    q = _cb("mig:apply:demo")
    await mig.cb_migrate_apply(q)
    q.message.edit_text.assert_awaited_once()
    text = q.message.edit_text.call_args.args[0]
    assert "demo" in text and "Score" in text
    assert (env["notes"] / "demo.md").is_file()
    assert (env["state"] / "demo" / "state.json").is_file()


async def test_cb_apply_keeps_notes(env: dict[str, Path]) -> None:
    _make_project(env["bots"], "demo")
    notes = env["notes"] / "demo.notes.md"
    notes.write_text("- [x] done\n", encoding="utf-8")
    q = _cb("mig:apply:demo")
    await mig.cb_migrate_apply(q)
    assert notes.read_text(encoding="utf-8") == "- [x] done\n"


async def test_cb_no_cancels(env: dict[str, Path]) -> None:
    q = _cb("mig:no")
    await mig.cb_migrate_no(q)
    q.message.edit_text.assert_awaited_once()
    assert "Отменено" in q.message.edit_text.call_args.args[0]


async def test_cb_dry_renders(env: dict[str, Path]) -> None:
    _make_project(env["bots"], "demo")
    q = _cb("mig:dry:demo")
    await mig.cb_migrate_dry(q)
    q.message.edit_text.assert_awaited_once()
    assert "demo" in q.message.edit_text.call_args.args[0]


async def test_cb_dry_invalid_name(env: dict[str, Path]) -> None:
    q = _cb("mig:dry:../etc")
    await mig.cb_migrate_dry(q)
    q.message.edit_text.assert_not_awaited()
    q.answer.assert_awaited()
    assert q.answer.call_args.kwargs.get("show_alert") is True


async def test_cb_apply_missing_project(env: dict[str, Path]) -> None:
    q = _cb("mig:apply:nope")
    await mig.cb_migrate_apply(q)
    q.message.edit_text.assert_awaited_once()
    assert "Ошибка" in q.message.edit_text.call_args.args[0]
