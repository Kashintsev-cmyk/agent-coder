"""Тесты _split_for_telegram: не рвать HTML блочные теги."""

from __future__ import annotations

from agent_coder.telegram.handlers import _split_for_telegram


def test_short_text_unchanged():
    s = "<b>hello</b>"
    assert _split_for_telegram(s, limit=100) == [s]


def test_block_pre_not_split():
    pre = (
        "<pre>"
        + "\n".join(f"line {i} xxxxxxxxxxxxxxxxxxxx" for i in range(20))
        + "</pre>"
    )
    parts = _split_for_telegram(pre, limit=200)
    for p in parts:
        assert p.count("<pre>") == p.count("</pre>")
    assert "<pre>" in parts[0]


def test_block_blockquote_not_split():
    q = "<blockquote>" + "x" * 500 + "</blockquote>"
    parts = _split_for_telegram(q, limit=100)
    for p in parts:
        assert p.count("<blockquote>") == p.count("</blockquote>")


def test_mixed_html_and_text():
    text = (
        "<b>Header</b>\n"
        + "some text " * 30
        + "\n<pre>\n"
        + "code line\n" * 10
        + "</pre>\n"
        + "footer " * 20
    )
    parts = _split_for_telegram(text, limit=200)
    for p in parts:
        assert p.count("<pre>") == p.count("</pre>")
