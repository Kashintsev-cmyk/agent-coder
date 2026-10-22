"""B10.b: CHAT удалён из enum, legacy 'chat' грузится как PLAN."""

from __future__ import annotations

from agent_coder.agent.prompts import Mode
from agent_coder.agent.session import Session


def test_mode_chat_not_in_enum() -> None:
    assert not hasattr(Mode, "CHAT")
    assert "chat" not in [m.value for m in Mode]


def test_legacy_chat_session_loads_as_plan() -> None:
    """B10.b: старый JSON с mode='chat' грузится как PLAN (compat)."""
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
