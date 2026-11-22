"""search_history: фрагменты из архива должны нести код, а не 200 символов.

HISTORY_SNIPPET=200 обрезал почти любую сигнатуру функции. После compress
история — единственный путь восстановить прочитанный код, поэтому сниппет
поднят до 600, а per-tool лимит — до 4096.
"""

from __future__ import annotations

from agent_coder.agent import tool_extract


def test_history_snippet_is_large_enough() -> None:
    assert tool_extract.HISTORY_SNIPPET >= 600, (
        f"HISTORY_SNIPPET={tool_extract.HISTORY_SNIPPET} — код из архива "
        f"приходит обрывком"
    )
    assert tool_extract.DEFAULT_LIMITS["search_history"] >= 4096


def test_five_blocks_fit_without_cap() -> None:
    """5 фрагментов по HISTORY_SNIPPET влезают целиком, без «обрезано»."""
    block = "x" * (tool_extract.HISTORY_SNIPPET + 400)
    raw = "\n\n".join([block] * tool_extract.HISTORY_TOP)
    out = tool_extract.extract(
        "search_history", {}, raw, tool_extract.DEFAULT_MAX_CHARS
    )
    assert "обрезано" not in out
    assert out.count("x" * tool_extract.HISTORY_SNIPPET) == tool_extract.HISTORY_TOP


def test_extra_blocks_are_reported() -> None:
    """Лишние фрагменты — счётчиком «ещё N скрыто», а не молча."""
    blocks = ["y" * 50] * (tool_extract.HISTORY_TOP + 2)
    out = tool_extract.extract(
        "search_history", {}, "\n\n".join(blocks), tool_extract.DEFAULT_MAX_CHARS
    )
    assert "ещё 2 фрагментов скрыто" in out
