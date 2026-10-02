"""Тесты content_to_text — нормализация content в строку."""
from __future__ import annotations

from agent_coder.agent.session import content_to_text


def test_none():
    assert content_to_text(None) == ""


def test_str_passthrough():
    assert content_to_text("привет") == "привет"


def test_empty_str():
    assert content_to_text("") == ""


def test_text_block():
    assert content_to_text([{"type": "text", "text": "привет"}]) == "привет"


def test_image_url_block():
    assert content_to_text([
        {"type": "image_url", "image_url": {"url": "http://x"}}
    ]) == "[изображение]"


def test_mixed_blocks():
    out = content_to_text([
        {"type": "text", "text": "что тут?"},
        {"type": "image_url", "image_url": {"url": "http://x"}},
    ])
    assert out == "что тут?\n[изображение]"


def test_unknown_block_skipped():
    out = content_to_text([
        {"type": "text", "text": "а"},
        {"type": "audio"},  # не поддерживаем
        {"type": "text", "text": "б"},
    ])
    assert out == "а\nб"


def test_non_dict_in_list_skipped():
    out = content_to_text([
        {"type": "text", "text": "а"},
        "мусор",
        None,
        {"type": "text", "text": "б"},
    ])
    assert out == "а\nб"


def test_empty_text_block_skipped():
    out = content_to_text([
        {"type": "text", "text": ""},
        {"type": "text", "text": "ок"},
    ])
    assert out == "ок"


def test_unexpected_type_fallback():
    # Не str, не list, не None — приводим к str
    assert content_to_text(42) == "42"
