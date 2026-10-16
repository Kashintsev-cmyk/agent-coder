"""Тесты ModelRouter: дефолты по режиму, приоритет ручного выбора,
fallback-цепочки при 429/timeout."""
from __future__ import annotations

import httpx
import pytest
from openai import APIConnectionError, APITimeoutError, RateLimitError

from agent_coder.config import config
from agent_coder.llm.router import FALLBACK_ERRORS, RouterState, router


@pytest.fixture
def fresh_router():
    """Роутер с актуальными группами из config (на случай env-переопределений)."""
    return router


# --- дефолты по режиму ---

def test_default_for_act_is_first_coding(fresh_router):
    assert fresh_router.default_for_mode("act") == config.CODING_MODEL_IDS[0]


def test_default_for_audit_is_first_audit(fresh_router):
    assert fresh_router.default_for_mode("audit") == config.AUDIT_MODEL_IDS[0]


def test_default_for_plan_and_chat_use_coding_group(fresh_router):
    assert fresh_router.default_for_mode("plan") == config.CODING_MODEL_IDS[0]
    assert fresh_router.default_for_mode("chat") == config.CODING_MODEL_IDS[0]


# --- приоритет ручного выбора (вариант «б») ---

def test_resolve_manual_overrides_router(fresh_router):
    manual = config.CURATED_MODEL_IDS[-1]
    assert fresh_router.resolve("audit", manual_model=manual) == manual


def test_resolve_auto_uses_mode_default(fresh_router):
    assert fresh_router.resolve("audit") == config.AUDIT_MODEL_IDS[0]
    assert fresh_router.resolve("act") == config.CODING_MODEL_IDS[0]


def test_resolve_router_disabled_returns_none(monkeypatch, fresh_router):
    monkeypatch.setattr(config, "MODEL_ROUTER_ENABLED", False)
    assert fresh_router.resolve("audit") is None
    # Ручной выбор работает и при выключенном роутере.
    assert fresh_router.resolve("audit", manual_model="x/y") == "x/y"


# --- fallback-цепочки ---

def test_chain_coding_order(fresh_router):
    coding = config.CODING_MODEL_IDS
    assert fresh_router.chain_for(coding[0], "act") == list(coding)
    # Середина цепочки: выбранная модель первая, остальные в исходном порядке.
    assert fresh_router.chain_for(coding[1], "act") == [
        coding[1], coding[0], coding[2],
    ]


def test_chain_audit_order(fresh_router):
    audit = config.AUDIT_MODEL_IDS
    assert fresh_router.chain_for(audit[0], "audit") == list(audit)


def test_chain_unknown_model_single_element(fresh_router):
    assert fresh_router.chain_for("vendor/unknown", "act") == ["vendor/unknown"]


# --- next_on_error ---

def _rate_limit_error() -> RateLimitError:
    request = httpx.Request("POST", "https://routerai.ru/api/v1/chat/completions")
    response = httpx.Response(429, request=request)
    return RateLimitError("429 rate limited", response=response, body=None)


def _timeout_error() -> APITimeoutError:
    request = httpx.Request("POST", "https://routerai.ru/api/v1/chat/completions")
    return APITimeoutError(request=request)


def test_next_on_error_rate_limit_moves_down_chain(fresh_router):
    coding = config.CODING_MODEL_IDS
    state = RouterState(manual_model=coding[0])
    nxt = fresh_router.next_on_error(state, "act", _rate_limit_error())
    assert nxt == coding[1]
    assert state.current == coding[0]  # manual не мутирует
    assert state.switches == [f"{coding[0]} → {coding[1]}"]

    nxt2 = fresh_router.next_on_error(state, "act", _timeout_error())
    assert nxt2 == coding[2]
    assert state.switches[-1] == f"{coding[1]} → {coding[2]}"


def test_next_on_error_chain_exhausted_returns_none(fresh_router):
    coding = config.CODING_MODEL_IDS
    state = RouterState(manual_model=coding[0])
    # Пройти всю цепочку.
    for _ in range(len(coding)):
        fresh_router.next_on_error(state, "act", _rate_limit_error())
    assert fresh_router.next_on_error(state, "act", _rate_limit_error()) is None


def test_next_on_error_non_fallback_error_returns_none(fresh_router):
    state = RouterState(manual_model=config.CODING_MODEL_IDS[0])
    assert fresh_router.next_on_error(state, "act", ValueError("boom")) is None
    assert state.chain_index == 0
    assert state.switches == []


def test_next_on_error_auto_mode_uses_group_default(fresh_router):
    audit = config.AUDIT_MODEL_IDS
    state = RouterState(manual_model=None)
    nxt = fresh_router.next_on_error(state, "audit", _rate_limit_error())
    assert nxt == audit[1]


def test_next_on_error_no_base_model_returns_none(monkeypatch, fresh_router):
    monkeypatch.setattr(config, "MODEL_ROUTER_ENABLED", False)
    state = RouterState(manual_model=None)
    assert fresh_router.next_on_error(state, "act", _rate_limit_error()) is None


def test_fallback_errors_tuple_covers_429_and_timeout():
    assert RateLimitError in FALLBACK_ERRORS
    assert APITimeoutError in FALLBACK_ERRORS
    assert APIConnectionError in FALLBACK_ERRORS
