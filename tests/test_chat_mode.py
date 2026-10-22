"""B10.a: CHAT deprecated — история всегда чистится, tools пусты, enum остаётся."""

from __future__ import annotations

from agent_coder.agent.prompts import Mode
from agent_coder.telegram.handlers import _should_preserve_history
from agent_coder.tools import build_registry


def test_mode_chat_still_in_enum() -> None:
    """B10.a: enum остаётся (import не падает), B10.b удалит."""
    assert Mode.CHAT.value == "chat"


def test_chat_tools_are_empty() -> None:
    r = build_registry()
    assert r.for_mode(Mode.CHAT) == []


def test_chat_call_allowed_nothing() -> None:
    r = build_registry()
    for name, args in (
        ("read_file", {"path": "x"}),
        ("write_file", {"path": "x"}),
        ("run_command", {"command": "ls"}),
    ):
        tool = r.get(name)
        assert r.is_call_allowed(Mode.CHAT, tool, args) is False


def test_history_always_cleared() -> None:
    """B10.a: CHAT удалён, все переходы чистят историю."""
    pairs = [
        (Mode.PLAN, Mode.ACT),
        (Mode.ACT, Mode.AUDIT),
        (Mode.PLAN, Mode.AUDIT),
        (Mode.ACT, Mode.CHAT),
        (Mode.CHAT, Mode.ACT),
        (Mode.PLAN, Mode.CHAT),
        (Mode.CHAT, Mode.AUDIT),
    ]
    for old, new in pairs:
        assert _should_preserve_history(old, new) is False
