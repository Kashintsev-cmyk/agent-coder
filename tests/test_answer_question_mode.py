"""B10.a: _should_answer_directly всегда False (CHAT удалён)."""

from __future__ import annotations

from agent_coder.agent.prompts import Mode
from agent_coder.telegram.handlers.task import _should_answer_directly


class _FakeSession:
    def __init__(self, mode: Mode) -> None:
        self.mode = mode


def test_always_false() -> None:
    """B10.a: прямой ответ отключён во всех режимах."""
    for mode in (Mode.PLAN, Mode.ACT, Mode.AUDIT, Mode.CHAT):
        assert _should_answer_directly(_FakeSession(mode), "как дела?") is False
        assert _should_answer_directly(_FakeSession(mode), "x" * 250) is False
