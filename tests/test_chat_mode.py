"""B10.b: CHAT удалён из enum, legacy 'chat' грузится как PLAN."""

from __future__ import annotations

import pytest

from agent_coder.agent.prompts import Mode
from agent_coder.agent.session import Session
from agent_coder.telegram.handlers import _should_preserve_history


def test_mode_chat_not_in_enum() -> None:
    assert not hasattr(Mode, "CHAT")
    assert "chat" not in [m.value for m in Mode]


def test_legacy_chat_session_loads_as_plan() -> None:
    data = {
        "id": "x",
        "workspace": "/tmp",
        "model": "m",
        "mode": "chat",
        "messages": [],
        "total_usage": {"input_tokens": 0, "output_tokens": 0, "cost": 0.0},
    }
    s = Session.from_dict(data)
    assert s.mode == Mode.PLAN


def test_history_always_cleared() -> None:
    for old, new in [
        (Mode.PLAN, Mode.ACT),
        (Mode.ACT, Mode.AUDIT),
        (Mode.PLAN, Mode.AUDIT),
        (Mode.AUDIT, Mode.PLAN),
    ]:
        assert _should_preserve_history(old, new) is False


@pytest.mark.parametrize("mode", [Mode.PLAN, Mode.ACT, Mode.AUDIT])
def test_preserve_self(mode: Mode) -> None:
    """Смена на тот же режим — тоже False (по текущей логике)."""
    assert _should_preserve_history(mode, mode) is False
