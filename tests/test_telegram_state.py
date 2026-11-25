"""Tests for Telegram session-state maintenance."""

from __future__ import annotations

from pathlib import Path

from agent_coder.telegram import state


def test_remove_session_references_only_prunes_deleted_sessions(
    tmp_path: Path, monkeypatch
):
    monkeypatch.setattr(state, "STATE_PATH", tmp_path / "telegram_state.json")
    state.set_active_session(1, "expired")
    state.set_active_session(2, "keep")
    state.set_active_session(3, "expired")

    removed = state.remove_session_references({"expired"})

    assert removed == 2
    assert state.get_active_session(1) is None
    assert state.get_active_session(2) == "keep"
    assert state.get_active_session(3) is None
    assert state.remove_session_references(set()) == 0
