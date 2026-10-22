"""Тесты ModelRouter (B1): единый MODEL_IDS, recommended-first,
приоритет ручного выбора, fallback-цепочки при 429/timeout."""

from __future__ import annotations

import httpx
import pytest
from openai import APIConnectionError, APITimeoutError, RateLimitError

from agent_coder.config import config
from agent_coder.llm.router import FALLBACK_ERRORS, RouterState, router


@pytest.fixture
def fresh_router():
    """Синглтон-роутер (MODEL_IDS читается в __init__)."""
    return router


# --- resolve: manual > recommended > MODEL_IDS ---


def test_resolve_manual_overrides_everything(fresh_router):
    manual = config.MODEL_IDS[-1]
    assert fresh_router.resolve("audit", manual_model=manual) == manual
    assert fresh_router.resolve("act", manual_model=manual) == manual


def test_resolve_uses_recommended_for_all_modes(fresh_router):
    for mode in ("act", "plan", "audit"):
        rec = config.RECOMMENDED_MODEL_BY_MODE[mode]
        assert fresh_router.resolve(mode) == rec


def test_resolve_falls_back_when_recommended_does_not_fit(monkeypatch, fresh_router):
    rec = "vendor/recommended-does-not-fit"
    monkeypatch.setitem(config.RECOMMENDED_MODEL_BY_MODE, "act", rec)
    original = fresh_router.fits_context

    def fake_fits(model_id, tokens=0):
        if model_id == rec:
            return False
        return original(model_id, tokens)

    monkeypatch.setattr(fresh_router, "fits_context", fake_fits)
    chosen = fresh_router.resolve("act")
    assert chosen != rec
    assert chosen == config.MODEL_IDS[0]


def test_resolve_router_disabled_returns_none(monkeypatch, fresh_router):
    monkeypatch.setattr(config, "MODEL_ROUTER_ENABLED", False)
    assert fresh_router.resolve("audit") is None
    # Ручной выбор работает и при выключенном роутере.
    assert fresh_router.resolve("audit", manual_model="x/y") == "x/y"


# --- проверка размера контекста ---


def test_max_input_tokens_respects_fill_ratio(fresh_router):
    # GLM 5.3: 1 048 576 * 0.8
    assert fresh_router.max_input_tokens("z-ai/glm-5.3") == int(
        1_048_576 * config.CONTEXT_FILL_RATIO
    )
    # R1: 64 000 * 0.8
    assert fresh_router.max_input_tokens("deepseek/deepseek-r1") == int(
        64_000 * config.CONTEXT_FILL_RATIO
    )


def test_fits_context_small_task_fits_r1(fresh_router):
    assert fresh_router.fits_context("deepseek/deepseek-r1", 10_000) is True


def test_fits_context_large_task_does_not_fit_r1(fresh_router):
    # 100k токенов > 64k * 0.8 = 51.2k → R1 не влезает.
    assert fresh_router.fits_context("deepseek/deepseek-r1", 100_000) is False
    assert fresh_router.fits_context("moonshotai/kimi-k2.7-code", 100_000) is True
    assert fresh_router.fits_context("z-ai/glm-5.3", 100_000) is True


# --- chain_for: MODEL_IDS order ---


def test_chain_input_zero_keeps_all_model_ids(fresh_router):
    """input_tokens=0 → фильтр по контексту не применяется, все MODEL_IDS."""
    first = config.MODEL_IDS[0]
    chain = fresh_router.chain_for(first, "act", 0)
    assert chain == list(config.MODEL_IDS)


def test_chain_middle_model_first(fresh_router):
    """Середина цепочки: выбранная первая, остальные — в порядке MODEL_IDS."""
    mid = config.MODEL_IDS[3]
    chain = fresh_router.chain_for(mid, "act", 0)
    assert chain[0] == mid
    assert chain[1:] == [m for m in config.MODEL_IDS if m != mid]


def test_chain_100k_filters_rest_by_context(fresh_router):
    first = config.MODEL_IDS[0]
    chain = fresh_router.chain_for(first, "act", 100_000)
    assert chain[0] == first
    for mid in chain[1:]:
        assert fresh_router.fits_context(mid, 100_000)


def test_chain_unknown_model_single_element(fresh_router):
    assert fresh_router.chain_for("vendor/unknown", "act", 10_000_000) == [
        "vendor/unknown"
    ]


# --- next_on_error ---


def _rate_limit_error() -> RateLimitError:
    request = httpx.Request("POST", "https://routerai.ru/api/v1/chat/completions")
    response = httpx.Response(429, request=request)
    return RateLimitError("429 rate limited", response=response, body=None)


def _timeout_error() -> APITimeoutError:
    request = httpx.Request("POST", "https://routerai.ru/api/v1/chat/completions")
    return APITimeoutError(request=request)


def test_next_on_error_manual_moves_down_chain(fresh_router):
    m0 = config.MODEL_IDS[0]
    state = RouterState(manual_model=m0)
    nxt = fresh_router.next_on_error(state, "act", _rate_limit_error())
    assert nxt == config.MODEL_IDS[1]
    assert state.current == m0  # manual не мутирует
    assert state.switches == [f"{m0} → {config.MODEL_IDS[1]}"]

    nxt2 = fresh_router.next_on_error(state, "act", _timeout_error())
    assert nxt2 == config.MODEL_IDS[2]


def test_next_on_error_chain_exhausted_returns_none(fresh_router):
    state = RouterState(manual_model=config.MODEL_IDS[0])
    for _ in range(len(config.MODEL_IDS) - 1):
        assert fresh_router.next_on_error(state, "act", _rate_limit_error()) is not None
    assert fresh_router.next_on_error(state, "act", _rate_limit_error()) is None


def test_next_on_error_non_fallback_error_returns_none(fresh_router):
    state = RouterState(manual_model=config.MODEL_IDS[0])
    assert fresh_router.next_on_error(state, "act", ValueError("boom")) is None
    assert state.chain_index == 0
    assert state.switches == []


def test_next_on_error_auto_uses_resolve(fresh_router):
    """manual=None → base = resolve(mode) = recommended → next = chain[1]."""
    rec = config.RECOMMENDED_MODEL_BY_MODE["audit"]
    state = RouterState(manual_model=None)
    assert fresh_router.resolve("audit") == rec  # база для fallback
    nxt = fresh_router.next_on_error(state, "audit", _rate_limit_error())
    assert nxt is not None
    chain = fresh_router.chain_for(rec, "audit", state.input_tokens)
    assert nxt == chain[1]


def test_next_on_error_no_base_model_returns_none(monkeypatch, fresh_router):
    monkeypatch.setattr(config, "MODEL_ROUTER_ENABLED", False)
    state = RouterState(manual_model=None)
    assert fresh_router.next_on_error(state, "act", _rate_limit_error()) is None


def test_fallback_errors_tuple_covers_429_and_timeout():
    assert RateLimitError in FALLBACK_ERRORS
    assert APITimeoutError in FALLBACK_ERRORS
    assert APIConnectionError in FALLBACK_ERRORS
