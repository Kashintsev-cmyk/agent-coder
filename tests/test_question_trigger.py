"""Тесты ?-триггера: быстрый вопрос без агентного цикла."""
from __future__ import annotations

from agent_coder.telegram.handlers import _is_question


def test_question_mark_at_end():
    assert _is_question("Что мы сделали в этой сессии?") is True


def test_no_question_mark():
    assert _is_question("Покажи структуру проекта") is False


def test_question_mark_in_middle():
    # ? в середине — не триггер
    assert _is_question("Что дальше? Скажи коротко") is False


def test_question_too_long():
    # > 200 символов — даже с ? в конце это задача
    long_text = "а" * 250 + "?"
    assert _is_question(long_text) is False


def test_question_boundary_200():
    text_199 = "а" * 198 + "?"
    text_201 = "а" * 200 + "?"
    assert _is_question(text_199) is True
    assert _is_question(text_201) is False


def test_empty():
    assert _is_question("") is False


def test_whitespace_after_question():
    # Пробелы в конце — не мешают
    assert _is_question("Что дальше?   ") is True
    assert _is_question("Что дальше?\n") is True
