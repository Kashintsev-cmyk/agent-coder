"""Тариф «время суток» (RouterAI variable_pricings, type=time-of-day).

Провайдеры deepseek (BytePlus/DeepSeek/Alibaba) меняют ставку по часам UTC:
ночью и в выходные дешевле (×0.5), в пиковые часы дороже (×2.0).

Ключевое ограничение, зафиксированное тестами: окна принадлежат КОНКРЕТНОМУ
провайдеру, а цена в /models — часто другого. На 2026-11-21 базы не совпали
ни у одной из 8 curated-моделей (deepseek-flash: 4.70₽ от Sail Research,
а окна — у BytePlus с базой 35.26₽). Применять чужие множители к чужой
ставке нельзя, поэтому при несовпадении базы множитель = 1.0.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime

import pytest

from agent_coder.agent.compression import _tod_factors, model_price
from agent_coder.model_catalog import ModelCatalog, catalog
from agent_coder.models import ModelInfo

# Окно «выходные целиком, ×0.5» с базой 10 ₽/1M — чтобы input_price
# в тестах совпадал с базой и множитель применялся.
BASE = 10.0
WEEKEND = (("saturday", "sunday"), 0, 24, 0.5, 0.5, BASE)
# Будний пик 1:00–4:00 UTC, ×2.
PEAK = (
    ("monday", "tuesday", "wednesday", "thursday", "friday"),
    1,
    4,
    2.0,
    2.0,
    BASE,
)


def _model(windows: tuple, price: float = BASE) -> ModelInfo:
    return ModelInfo(
        id="test/tod",
        name="TOD",
        context=1_000_000,
        reasoning=False,
        input_price=price,
        output_price=price * 2,
        # tier_threshold=0 — «тира нет». Без этого default None включает
        # legacy-ветку (×5 выше 100K), и тест мерил бы тир, а не время суток.
        tier_threshold=0,
        tod_windows=windows,
    )


# --- _tod_factors ---------------------------------------------------------


def test_no_windows_means_flat_price():
    assert _tod_factors(_model(())) == (1.0, 1.0)


def test_weekend_window_applies_discount():
    """Суббота, любое время → ×0.5 (окно 0..24)."""
    saturday = datetime(2026, 11, 21, 12, 0, tzinfo=UTC)  # суббота
    assert _tod_factors(_model((WEEKEND,)), saturday) == (0.5, 0.5)


def test_weekday_outside_peak_is_flat():
    """Вторник 12:00 — пиковое окно 1..4 не покрывает → 1.0."""
    tuesday_noon = datetime(2026, 11, 24, 12, 0, tzinfo=UTC)
    assert _tod_factors(_model((PEAK,)), tuesday_noon) == (1.0, 1.0)


def test_weekday_inside_peak_is_doubled():
    """Вторник 02:00 — внутри окна 1..4 → ×2."""
    tuesday_night = datetime(2026, 11, 24, 2, 0, tzinfo=UTC)
    assert _tod_factors(_model((PEAK,)), tuesday_night) == (2.0, 2.0)


def test_window_boundary_start_is_inclusive():
    """Час начала окна входит в окно (1:00 → ×2)."""
    tuesday_1 = datetime(2026, 11, 24, 1, 0, tzinfo=UTC)
    assert _tod_factors(_model((PEAK,)), tuesday_1) == (2.0, 2.0)


def test_window_boundary_end_is_exclusive():
    """Час окончания — уже не окно (4:00 → 1.0)."""
    tuesday_4 = datetime(2026, 11, 24, 4, 0, tzinfo=UTC)
    assert _tod_factors(_model((PEAK,)), tuesday_4) == (1.0, 1.0)


def test_base_mismatch_falls_back_to_flat():
    """Окно от другого провайдера (база ≠ input_price) не применяется.

    Это реальный случай: у deepseek-flash цена приходит от Sail Research
    (4.70₽), а окна есть только у BytePlus (35.26₽). Умножить 4.70 на 0.5
    «по мотивам BytePlus» — выдуманная цифра.
    """
    saturday = datetime(2026, 11, 21, 12, 0, tzinfo=UTC)
    mismatched = _model((WEEKEND,), price=4.70)  # база окна = 10.0
    assert _tod_factors(mismatched, saturday) == (1.0, 1.0)


def test_zero_base_is_ignored():
    """База 0 (битые данные) не даёт деления на ноль и не применяется."""
    saturday = datetime(2026, 11, 21, 12, 0, tzinfo=UTC)
    bad = (("saturday", "sunday"), 0, 24, 0.5, 0.5, 0.0)
    assert _tod_factors(_model((bad,)), saturday) == (1.0, 1.0)


def test_none_days_means_every_day():
    """days=None — окно действует во все дни недели."""
    saturday = datetime(2026, 11, 21, 12, 0, tzinfo=UTC)
    every_day = (None, 0, 24, 0.5, 0.5, BASE)
    assert _tod_factors(_model((every_day,)), saturday) == (0.5, 0.5)


def test_default_when_uses_current_utc():
    """when=None — берётся текущий момент (не падаем и возвращаем кортеж)."""
    factors = _tod_factors(_model((WEEKEND,)))
    assert isinstance(factors, tuple)
    assert len(factors) == 2


# --- model_price: множитель входит в цену ---------------------------------


def test_model_price_applies_tod_factor(monkeypatch):
    """Суббота: цена в 2 раза ниже, чем в будний полдень (×0.5).

    База окна (BASE) должна совпадать с input_price модели — иначе сработает
    защита от чужого провайдера и множитель не применится.

    Оба вызова передают ``when`` явно: ``when=None`` берёт ТЕКУЩЕЕ время, и
    тест зависел бы от дня недели запуска (в реальный выходной «плоская»
    цена тоже получила бы скидку).
    """
    m = _model((WEEKEND,))  # price = BASE = 10 ₽/1M
    monkeypatch.setattr(catalog, "get", lambda mid: m)
    weekday_noon = datetime(2026, 11, 24, 12, 0, tzinfo=UTC)  # вторник
    saturday = datetime(2026, 11, 21, 12, 0, tzinfo=UTC)

    flat = model_price(m.id, 1_000_000, when=weekday_noon)
    discounted = model_price(m.id, 1_000_000, when=saturday)

    assert flat == pytest.approx(10.0)  # 1M токенов × 10₽/1M
    assert discounted == pytest.approx(5.0)


def test_model_price_tod_scales_output_too(monkeypatch):
    """Множитель окна применяется и к выходу (у deepseek они совпадают)."""
    m = _model((WEEKEND,))  # output_price = 20 ₽/1M
    monkeypatch.setattr(catalog, "get", lambda mid: m)
    saturday = datetime(2026, 11, 21, 12, 0, tzinfo=UTC)

    price = model_price(m.id, 0, output_tokens=1_000_000, when=saturday)
    # 20₽/1M × 0.5 = 10₽
    assert price == pytest.approx(10.0)


# --- _extract_tod: разбор ответа API --------------------------------------


def _endpoint(prompt: float, completion: float, windows: list[dict]) -> dict:
    return {
        "provider_name": "BytePlus",
        "pricing": {"prompt": prompt, "completion": completion},
        "variable_pricings": windows,
    }


def test_extract_tod_converts_hundreds_to_hours():
    """utc_start/utc_end приходят в сотнях часов: 100 → 1:00, 400 → 4:00."""
    eps = [
        _endpoint(
            1e-05,
            2e-05,
            [
                {
                    "type": "time-of-day",
                    "utc_start": 100,
                    "utc_end": 400,
                    "utc_days": ["monday"],
                    "prompt": 2e-05,
                    "completion": 4e-05,
                }
            ],
        )
    ]
    windows = ModelCatalog._extract_tod(eps)
    assert len(windows) == 1
    days, h_from, h_to, in_f, out_f, base = windows[0]
    assert days == ("monday",)
    assert (h_from, h_to) == (1, 4)
    assert in_f == pytest.approx(2.0)
    assert out_f == pytest.approx(2.0)
    assert base == pytest.approx(10.0)  # 1e-05 ₽/токен → 10 ₽/1M


def test_extract_tod_zero_end_means_until_midnight():
    """utc_end=0 — «до конца суток», а не «нулевой интервал»."""
    eps = [
        _endpoint(
            1e-05,
            2e-05,
            [
                {
                    "type": "time-of-day",
                    "utc_start": 1400,
                    "utc_end": 0,
                    "prompt": 5e-06,
                    "completion": 1e-05,
                }
            ],
        )
    ]
    (days, h_from, h_to, in_f, _out_f, _base) = ModelCatalog._extract_tod(eps)[0]
    assert days is None  # utc_days не передан → все дни
    assert (h_from, h_to) == (14, 24)
    assert in_f == pytest.approx(0.5)


def test_extract_tod_ignores_prompt_threshold_windows():
    """Тиры long-context (prompt-threshold) — не тариф по времени."""
    eps = [
        _endpoint(
            1e-05,
            2e-05,
            [{"type": "prompt-threshold", "threshold": 100_000, "prompt": 5e-05}],
        )
    ]
    assert ModelCatalog._extract_tod(eps) == ()


def test_extract_tod_without_windows_is_empty():
    assert ModelCatalog._extract_tod([_endpoint(1e-05, 2e-05, [])]) == ()


def test_extract_tod_skips_malformed_windows():
    """Битое окно (не int-часы) пропускается, а не роняет обновление каталога."""
    eps = [
        _endpoint(
            1e-05,
            2e-05,
            [
                {"type": "time-of-day", "utc_start": "x", "utc_end": 100},
                {
                    "type": "time-of-day",
                    "utc_start": 0,
                    "utc_end": 100,
                    "prompt": 5e-06,
                },
            ],
        )
    ]
    windows = ModelCatalog._extract_tod(eps)
    assert len(windows) == 1
    assert windows[0][1:3] == (0, 1)


# --- взаимодействие с long-context тиром ----------------------------------


def test_tod_and_tier_factors_multiply(monkeypatch):
    """Ночной тариф и long-context тир применяются вместе, а не вместо.

    База окна = BASE = input_price, иначе сработала бы защита от чужого
    провайдера и тариф по времени не применился бы вовсе.
    """
    m = replace(
        _model((WEEKEND,)),
        tier_threshold=500_000,
        tier_input_factor=2.0,
        tier_output_factor=2.0,
    )
    monkeypatch.setattr(catalog, "get", lambda mid: m)
    saturday = datetime(2026, 11, 21, 12, 0, tzinfo=UTC)

    # 1M токенов × 10₽/1M = 10 ; выше порога 500K (×2) ; суббота (×0.5) → 10
    price = model_price(m.id, 1_000_000, when=saturday)
    assert price == pytest.approx(10.0)
