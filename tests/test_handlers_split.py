"""Тесты разбивки длинного текста и экранирования в telegram-хендлерах."""
from __future__ import annotations

from agent_coder.telegram.handlers import _html_escape, _split_for_telegram


class TestSplitForTelegram:
    def test_short_text_single_part(self):
        text = "привет мир"
        assert _split_for_telegram(text) == [text]

    def test_exact_limit(self):
        text = "a" * 100
        assert _split_for_telegram(text, limit=100) == [text]

    def test_splits_by_lines(self):
        text = "\n".join(["line"] * 10)
        parts = _split_for_telegram(text, limit=20)
        assert len(parts) > 1
        # Ни одна часть не превышает лимит
        assert all(len(p) <= 20 for p in parts)

    def test_long_line_cut_by_space(self):
        text = ("word " * 100).strip()  # одна строка длиннее лимита
        parts = _split_for_telegram(text, limit=50)
        assert all(len(p) <= 50 for p in parts)
        # Ни один непробельный символ не потерян
        assert "".join(parts).replace(" ", "") == text.replace(" ", "")

    def test_very_long_word_no_space(self):
        # Слово-монолит без пробелов — режем по символам
        text = "x" * 250
        parts = _split_for_telegram(text, limit=100)
        assert all(len(p) <= 100 for p in parts)
        assert "".join(parts) == text

    def test_no_content_lost(self):
        text = "\n".join(f"строка {i}" for i in range(200))
        parts = _split_for_telegram(text, limit=100)
        # Разбивка идёт по границам строк — обратная склейка восстанавливает текст
        assert "\n".join(parts) == text


class TestHtmlEscape:
    def test_escapes_special_chars(self):
        assert _html_escape("<b>&") == "&lt;b&gt;&amp;"

    def test_plain_text_unchanged(self):
        assert _html_escape("hello world") == "hello world"

    def test_quotes_not_escaped(self):
        assert _html_escape('"quote"') == '"quote"'
