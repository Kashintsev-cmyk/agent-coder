"""B10.b: _should_answer_directly/_answer_question/_is_question удалены."""

from __future__ import annotations


def test_helpers_removed() -> None:
    import agent_coder.telegram.handlers.task as t

    assert not hasattr(t, "_should_answer_directly")
    assert not hasattr(t, "_answer_question")
    assert not hasattr(t, "_is_question")
