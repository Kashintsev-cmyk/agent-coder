"""B7: цена и окна «время суток» берутся у ФАКТИЧЕСКОГО провайдера ответа.

Проблема, которую закрывают эти тесты: RouterAI отдаёт цену в ``/models`` от
одного провайдера, а окна ``time-of-day`` принадлежат конкретному
(BytePlus/DeepSeek/Alibaba). На 2026-11-21 база окон не совпала с ценой
``/models`` ни у одной из 8 curated-моделей, поэтому ``_tod_factors()`` всегда
возвращала 1.0 — тариф был «спящим».

Решение: провайдер приходит в ТЕЛЕ ответа (top-level ``provider``, живой probe
2026-11-22: ``"provider":"BytePlus"``), а в ``/models/{id}/endpoints`` есть и
ставка этого провайдера, и его окна. Когда провайдер известен — считаем по его
ставке и его окнам, и множитель наконец применяется.
"""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

from agent_coder.agent.compression import _tod_factors, model_price
from agent_coder.llm.client import _extract_provider
from agent_coder.llm.types import ChatResponse
from agent_coder.model_catalog import ModelCatalog, catalog
from agent_coder.models import ModelInfo

# --- живые данные probe 2026-11-22 (deepseek/deepseek-v4.1-flash) ----------
# BytePlus: 35.26 ₽/1M prompt, 141.04 ₽/1M completion, 6 окон time-of-day.
BYTEPLUS_IN = 3.52599819e-05
BYTEPLUS_OUT = 0.0001410399276
# Alibaba: 10.578 ₽/1M prompt, 2 окна.
ALIBABA_IN = 1.057799457e-05
ALIBABA_OUT = 0.00004231197828
# Цена, которую /models отдаёт для этой модели (другой провайдер!) — 4.70 ₽/1M.
MODELS_LEVEL_IN = 4.70

SATURDAY = datetime(2026, 11, 21, 12, 0, tzinfo=UTC)  # суббота, окно 0..24 ×0.5
TUESDAY_PEAK = datetime(2026, 11, 24, 2, 0, tzinfo=UTC)  # вторник 02:00, окно 1..4 ×2
TUESDAY_NOON = datetime(2026, 11, 24, 12, 0, tzinfo=UTC)  # вне окна ×1


def _deepseek_endpoints() -> list[dict]:
    """Два endpoint'а с окнами — как в живом ответе API (сокращённо)."""
    return [
        {
            "provider_name": "BytePlus",
            "pricing": {
                "prompt": BYTEPLUS_IN,
                "completion": BYTEPLUS_OUT,
                "input_cache_read": 7.05199638e-07,
            },
            "variable_pricings": [
                {
                    "type": "time-of-day",
                    "prompt": 1.762999095e-05,
                    "utc_end": 0,
                    "utc_days": ["saturday", "sunday"],
                    "utc_start": 0,
                    "completion": 7.05199638e-05,
                },
                {
                    "type": "time-of-day",
                    "prompt": 3.52599819e-05,
                    "utc_end": 400,
                    "utc_days": [
                        "monday",
                        "tuesday",
                        "wednesday",
                        "thursday",
                        "friday",
                    ],
                    "utc_start": 100,
                    "completion": 0.0001410399276,
                },
            ],
        },
        {
            "provider_name": "Alibaba",
            "pricing": {"prompt": ALIBABA_IN, "completion": ALIBABA_OUT},
            "variable_pricings": [
                {
                    "type": "time-of-day",
                    "prompt": 1.057799457e-05,
                    "utc_end": 0,
                    "utc_start": 1400,
                    "completion": 4.231197828e-05,
                }
            ],
        },
        # Третьего провайдера без окон — в живом ответе таких 31 из 33.
        {
            "provider_name": "Together",
            "pricing": {"prompt": 2e-05, "completion": 8e-05},
            "variable_pricings": [],
        },
    ]


def _deepseek_model(**kwargs) -> ModelInfo:
    eps = _deepseek_endpoints()
    base = ModelInfo(
        id="deepseek/deepseek-v4.1-flash",
        name="DeepSeek V4.1 Flash",
        context=1_000_000,
        reasoning=True,
        input_price=MODELS_LEVEL_IN,
        output_price=18.8,
        # tier_threshold=0 — «тира нет», иначе legacy-ветка ×5 мешала бы тесту.
        tier_threshold=0,
    )
    merged = ModelCatalog()._merge_endpoints(base, {"data": {"endpoints": eps}})
    for k, v in kwargs.items():
        object.__setattr__(merged, k, v)
    return merged


# --- _extract_provider_prices: ставки всех провайдеров модели --------------


def test_extract_provider_prices_maps_every_provider():
    """provider_name → (вход, выход) в ₽/1M по каждому endpoint'у."""
    prices = ModelCatalog._extract_provider_prices(_deepseek_endpoints())
    assert set(prices) == {"BytePlus", "Alibaba", "Together"}
    assert prices["BytePlus"][0] == pytest.approx(35.26, abs=0.01)
    assert prices["BytePlus"][1] == pytest.approx(141.04, abs=0.01)
    assert prices["Alibaba"][0] == pytest.approx(10.578, abs=0.001)


def test_extract_provider_prices_skips_endpoints_without_pricing():
    """Endpoint без цены или без имени провайдера просто не попадает в карту."""
    eps = [
        {"provider_name": "NoPrice"},
        {"pricing": {"prompt": 1e-05}},
        {"provider_name": "Ok", "pricing": {"prompt": 1e-05, "completion": 2e-05}},
    ]
    assert set(ModelCatalog._extract_provider_prices(eps)) == {"Ok"}


def test_extract_provider_prices_empty():
    assert ModelCatalog._extract_provider_prices([]) == {}


def test_merge_endpoints_fills_provider_prices():
    """_merge_endpoints проставляет per-provider ставки в ModelInfo."""
    m = _deepseek_model()
    assert set(m.provider_prices) == {"BytePlus", "Alibaba", "Together"}


# --- _tod_factors с провайдером -------------------------------------------


def test_tod_factor_applies_for_matching_provider():
    """BytePlus, суббота → ×0.5: база окна совпала со ставкой ЭТОГО провайдера."""
    m = _deepseek_model()
    assert _tod_factors(m, SATURDAY, provider="BytePlus") == (0.5, 0.5)


def test_tod_factor_peak_for_matching_provider():
    """BytePlus, вторник 02:00 → ×1.0 (пиковое окно 1..4 у BytePlus = базовая ставка)."""
    m = _deepseek_model()
    in_f, out_f = _tod_factors(m, TUESDAY_PEAK, provider="BytePlus")
    assert (in_f, out_f) == (1.0, 1.0)


def test_tod_factor_outside_any_window_is_flat():
    """BytePlus, вторник 12:00 — ни одно окно не покрывает час → 1.0."""
    m = _deepseek_model()
    assert _tod_factors(m, TUESDAY_NOON, provider="BytePlus") == (1.0, 1.0)


def test_tod_factor_unknown_provider_is_flat():
    """Провайдер, которого нет в карте ставок → 1.0 (не гадаем по чужим окнам)."""
    m = _deepseek_model()
    assert _tod_factors(m, SATURDAY, provider="Неизвестный") == (1.0, 1.0)


def test_tod_factor_provider_without_windows_is_flat():
    """У Together окон нет вовсе → 1.0, хотя у модели окна есть."""
    m = _deepseek_model()
    assert _tod_factors(m, SATURDAY, provider="Together") == (1.0, 1.0)


def test_tod_factor_without_provider_keeps_legacy_behaviour():
    """Без провайдера — старое поведение: сравнение с ценой /models (4.70 ₽).

    Ставка BytePlus (35.26 ₽) с ней не совпадает, поэтому множитель 1.0.
    Это ожидаемо: без факта провайдера окно применять не к чему.
    """
    m = _deepseek_model()
    assert _tod_factors(m, SATURDAY) == (1.0, 1.0)


# --- model_price с провайдером --------------------------------------------


def test_model_price_uses_provider_rate(monkeypatch):
    """С известным провайдером цена считается по ЕГО ставке, а не по /models.

    1M токенов × 35.26 ₽/1M (BytePlus) × 0.5 (суббота) = 17.63 ₽.
    Без провайдера было бы 4.70 ₽ (цена /models) — то есть другой провайдер.
    """
    m = _deepseek_model()
    monkeypatch.setattr(catalog, "get", lambda mid: m)

    priced = model_price(m.id, 1_000_000, when=SATURDAY, provider="BytePlus")
    assert priced == pytest.approx(17.63, abs=0.01)

    plain = model_price(m.id, 1_000_000, when=SATURDAY)
    assert plain == pytest.approx(MODELS_LEVEL_IN, abs=0.01)


def test_model_price_provider_rate_weekday_noon_is_full_rate(monkeypatch):
    """Вне окна провайдерская ставка применяется целиком: 35.26 ₽/1M."""
    m = _deepseek_model()
    monkeypatch.setattr(catalog, "get", lambda mid: m)
    assert model_price(m.id, 1_000_000, when=TUESDAY_NOON, provider="BytePlus") == (
        pytest.approx(35.26, abs=0.01)
    )


def test_model_price_provider_scales_output(monkeypatch):
    """Выход тоже считается по ставке провайдера и делится на 2 в субботу."""
    m = _deepseek_model()
    monkeypatch.setattr(catalog, "get", lambda mid: m)
    # 1M × 141.04 ₽/1M × 0.5 = 70.52
    price = model_price(
        m.id, 0, output_tokens=1_000_000, when=SATURDAY, provider="BytePlus"
    )
    assert price == pytest.approx(70.52, abs=0.01)


def test_model_price_unknown_provider_falls_back_to_models_price(monkeypatch):
    """Провайдер не из каталога — считаем по цене /models (плоско, без окон)."""
    m = _deepseek_model()
    monkeypatch.setattr(catalog, "get", lambda mid: m)
    price = model_price(m.id, 1_000_000, when=SATURDAY, provider="Неизвестный")
    assert price == pytest.approx(MODELS_LEVEL_IN, abs=0.01)


def test_model_price_unknown_model_is_zero():
    assert model_price("нет/такой-модели", 50_000, provider="BytePlus") == 0.0


def test_model_price_cache_tokens_use_provider_rate(monkeypatch):
    """Кэш-часть берётся из ставки провайдера (endpoints.pricing.input_cache_read).

    У BytePlus это 0.705 ₽/1M — в 50 раз дешевле его обычного входа (35.26 ₽),
    так что цена /models здесь не подставится даже случайно.
    """
    m = _deepseek_model()
    monkeypatch.setattr(catalog, "get", lambda mid: m)
    price = model_price(
        m.id,
        1_000_000,
        cache_read_tokens=1_000_000,
        when=TUESDAY_NOON,
        provider="BytePlus",
    )
    assert price == pytest.approx(0.7052, abs=0.001)


# --- провайдер из ответа API ----------------------------------------------


def test_extract_provider_top_level():
    """Живой probe 2026-11-22: provider — top-level поле ответа, не в usage."""
    resp = SimpleNamespace(provider="BytePlus", usage=SimpleNamespace(cost=0.001))
    assert _extract_provider(resp) == "BytePlus"


def test_extract_provider_missing_is_empty():
    """Провайдера может не быть (другие прокси) — пустая строка, не падение."""
    assert _extract_provider(SimpleNamespace(usage=None)) == ""


def test_extract_provider_from_model_extra():
    """SDK-объект может спрятать незнакомое поле в model_extra."""
    resp = SimpleNamespace(model_extra={"provider": "Alibaba"})
    assert _extract_provider(resp) == "Alibaba"


def test_extract_provider_from_provider_name():
    """Альтернативное имя поля — provider_name (так оно в endpoints)."""
    assert _extract_provider(SimpleNamespace(provider_name="Together")) == "Together"


def test_extract_provider_ignores_empty_string():
    assert _extract_provider(SimpleNamespace(provider="   ")) == ""


def test_chat_response_carries_provider():
    """ChatResponse доносит провайдера до вызывающего кода."""
    resp = ChatResponse(
        message=SimpleNamespace(), usage=SimpleNamespace(), provider="BytePlus"
    )  # type: ignore[arg-type]
    assert resp.provider == "BytePlus"


def test_chat_response_provider_defaults_to_empty():
    resp = ChatResponse(message=SimpleNamespace(), usage=SimpleNamespace())  # type: ignore[arg-type]
    assert resp.provider == ""
