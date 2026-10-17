"""Тесты: _should_answer_directly отвечает только в CHAT-режиме."""

from __future__ import annotations

from agent_coder.agent.prompts import Mode
from agent_coder.telegram.handlers.task import _should_answer_directly


class _FakeSession:
    def __init__(self, mode: Mode) -> None:
        self.mode = mode


def test_chat_short_question_direct() -> None:
    assert _should_answer_directly(_FakeSession(Mode.CHAT), "как дела?") is True


def test_act_short_question_goes_to_loop() -> None:
    assert _should_answer_directly(_FakeSession(Mode.ACT), "как дела?") is False


def test_plan_short_question_goes_to_loop() -> None:
    assert _should_answer_directly(_FakeSession(Mode.PLAN), "как дела?") is False


def test_audit_short_question_goes_to_loop() -> None:
    assert _should_answer_directly(_FakeSession(Mode.AUDIT), "как дела?") is False


def test_chat_long_message_not_direct() -> None:
    assert _should_answer_directly(_FakeSession(Mode.CHAT), "x" * 250) is False


def test_chat_non_question_not_direct() -> None:
    assert (
        _should_answer_directly(_FakeSession(Mode.CHAT), "отрефактори парсер") is False
    )
