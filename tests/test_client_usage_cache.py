"""B4.x (2026-11-10): _extract_usage парсит cache_write_tokens.

Probe RouterAI 2026-11-10: оба провайдера (openai/gpt-6-luna и
anthropic/claude-haiku-5.5) на первом запросе отдают
`prompt_tokens_details.cache_write_tokens > 0`, на втором —
`cached_tokens > 0`. Раньше `_extract_usage` читал только `cached_tokens`,
`Usage.cache_write_tokens` всегда был 0 — теряли метрику (cost из raw.cost
уже корректен, его не трогаем).
"""

from __future__ import annotations

from types import SimpleNamespace

from agent_coder.llm.client import _extract_usage


def _resp(*, prompt=0, completion=0, cached=0, cache_write=0, cost=0.0):
    raw_usage = SimpleNamespace(
        prompt_tokens=prompt,
        completion_tokens=completion,
        prompt_tokens_details=SimpleNamespace(
            cached_tokens=cached,
            cache_write_tokens=cache_write,
        ),
        cost=cost,
    )
    return SimpleNamespace(usage=raw_usage)


def test_extract_usage_cache_write_luna_first_call():
    """Probe 2026-11-10, luna 1-й: cache_write=1219, cached=0."""
    u = _extract_usage(
        _resp(prompt=1222, completion=1665, cached=0, cache_write=1219, cost=0.10598)
    )
    assert u.input_tokens == 1222
    assert u.output_tokens == 1665
    assert u.cache_read_tokens == 0
    assert u.cache_write_tokens == 1219
    assert abs(u.cost - 0.10598) < 1e-6


def test_extract_usage_cache_read_luna_second_call():
    """Probe 2026-11-10, luna 2-й: cache_read=1219, cache_write=0."""
    u = _extract_usage(
        _resp(prompt=1222, completion=1655, cached=1219, cache_write=0, cost=0.09037)
    )
    assert u.cache_read_tokens == 1219
    assert u.cache_write_tokens == 0


def test_extract_usage_cache_write_haiku():
    """Probe 2026-11-10, haiku: cache_write=2249 (= весь prompt)."""
    u = _extract_usage(
        _resp(prompt=2249, completion=3018, cached=0, cache_write=2249, cost=0.19258)
    )
    assert u.cache_write_tokens == 2249
    assert u.cache_read_tokens == 0


def test_extract_usage_no_prompt_details_backward_compat():
    """Ответ без prompt_tokens_details (старые провайдеры) — 0/0."""
    raw_usage = SimpleNamespace(
        prompt_tokens=100,
        completion_tokens=50,
        cost=0.001,
    )
    u = _extract_usage(SimpleNamespace(usage=raw_usage))
    assert u.cache_read_tokens == 0
    assert u.cache_write_tokens == 0
