"""Стоимость генерации как функция объёма истории: где начинается рост.

Вопрос: при каком объёме токенов стоимость запроса начинает расти?

Ответ заложен в двух местах:

1. Цена модели (``ModelInfo.input_price`` / ``output_price``) — скаляры
   за 1M токенов. В прайсинге RouterAI (``/api/v1/models``) полей тиров
   нет: ``pricing`` = prompt / completion (+ input_cache_read и т.п.,
   они не влияют на линейную часть). Значит стоимость запроса растёт
   С ПЕРВОГО токена и строго линейно — порога «до X токенов дешево,
   после X дорого» в коде нет.
2. Порог сжатия (``compression.soft_limit``) — единственный излом в
   системе, но платится он не ценой модели, а дополнительным вызовом
   суммаризации, который срабатывает на КАЖДОЙ итерации, где
   ``effective_tokens > soft``.

Тесты держат оба факта. Если появится поддержка тиров (например,
long-context ×5 после 100K) — упадёт ``test_growth_is_linear``;
если порог сжатия уедет — упадёт ``test_soft_limit_formula``.
"""

from __future__ import annotations

from itertools import pairwise

import pytest

from agent_coder.agent.compression import (
    TIER_FACTOR,
    TIER_THRESHOLD_TOKENS,
    estimate_tokens,
    model_price,
    soft_limit,
)
from agent_coder.agent.verify import _VERIFY_EST_OUTPUT_TOKENS, _estimate_cost_rub
from agent_coder.config import config
from agent_coder.model_catalog import ModelCatalog, catalog

# Сетка из ТЗ: 40K — 90K с шагом 10K (6 точек).
GRID = (40_000, 50_000, 60_000, 70_000, 80_000, 90_000)
STEP = 10_000
TOL = 1e-12


def _messages(tokens: int) -> list[dict[str, str]]:
    """История ровно на ``tokens`` токенов по эвристике estimate_tokens.

    Только латиница: ``latin / 4`` → 1 символ = 0.25 токена, точное
    попадание без остатка от деления кириллической части.
    """
    return [{"role": "user", "content": "a" * (tokens * 4)}]


def _priced_models() -> list[str]:
    """Модели каталога с ненулевой ценой входа и выхода.

    Пустые пропускаем: для placeholder'а ``_estimate_cost_rub``
    возвращает ``None`` (pre-check бюджета отключён), проверять
    нечего. Требуем хотя бы две, чтобы тест не проходил вхолостую.
    """
    ids = [
        m.id
        for m in (catalog.get(mid) for mid in config.CURATED_MODEL_IDS)
        if m is not None and m.input_price > 0 and m.output_price > 0
    ]
    assert len(ids) >= 2, f"нет моделей с ценой в каталоге: {ids}"
    return ids


PRICED = _priced_models()


def _cost(tokens: int, model: str) -> float:
    value = _estimate_cost_rub(_messages(tokens), model)
    assert value is not None, f"{model}: pre-check отключён (цена 0?)"
    return value


def test_estimate_tokens_is_exact_on_grid():
    """Опора теста: сетка действительно даёт ровно заявленные токены."""
    for tokens in GRID:
        assert estimate_tokens(_messages(tokens)) == tokens


@pytest.mark.parametrize("model", PRICED)
def test_cost_grows_with_every_step(model):
    """Рост начинается с первой же точки сетки — «мёртвой зоны» нет.

    Стоимость строго возрастает на каждом шаге 40K→50K→…→90K.
    """
    costs = [_cost(tokens, model) for tokens in GRID]
    for (left, a), (right, b) in pairwise(zip(GRID, costs, strict=True)):
        assert b > a, (
            f"{model}: нет роста между {left} и {right} токенами ({a:.6f} → {b:.6f} ₽)"
        )


@pytest.mark.parametrize("model", PRICED)
def test_growth_is_linear(model):
    """Прирост на каждом шаге одинаковый — тиров и скачков цены нет.

    Именно этот тест упадёт, если в прайсе появится long-context тир
    (например, «свыше 100K токенов — ×5 по всему запросу»).
    """
    costs = [_cost(tokens, model) for tokens in GRID]
    deltas = [b - a for a, b in pairwise(costs)]
    assert max(deltas) - min(deltas) < TOL, (
        f"{model}: нелинейный рост, приросты по {STEP} токенов = "
        + ", ".join(f"{d:.9f}" for d in deltas)
    )


@pytest.mark.parametrize("model", PRICED)
def test_growth_rate_equals_input_price(model):
    """Скорость роста задаётся только ценой входа: STEP * price_in / 1e6.

    Output-часть в приросте не участвует — она константа запроса.
    """
    info = catalog.get(model)
    assert info is not None
    expected = STEP * info.input_price / 1_000_000
    actual = _cost(GRID[1], model) - _cost(GRID[0], model)
    assert actual == pytest.approx(expected, rel=1e-9, abs=TOL)


@pytest.mark.parametrize("model", PRICED)
def test_output_part_is_constant(model):
    """«Стоимость генерации» от объёма истории не зависит вовсе.

    ``_estimate_cost_rub`` = input + фиксированные __VERIFY_EST_OUTPUT_TOKENS.
    Сколько бы история ни выросла, output-слагаемое одно и то же —
    поэтому у моделей с дорогим выходом (output/input ≈ 5) при малой
    истории именно оно и определяет счёт.
    """
    info = catalog.get(model)
    assert info is not None
    output_part = _VERIFY_EST_OUTPUT_TOKENS * info.output_price / 1_000_000
    for tokens in GRID:
        cost = _cost(tokens, model)
        input_part = tokens * info.input_price / 1_000_000
        # Ровно два слагаемых: линейный вход + неизменный выход.
        assert cost == pytest.approx(input_part + output_part, rel=1e-9, abs=TOL)
        assert cost - input_part == pytest.approx(output_part, rel=1e-9, abs=TOL)


@pytest.mark.parametrize("model", PRICED)
def test_growth_is_linear_above_the_grid_too(model):
    """Линейность держится далеко за пределами сетки 40—90K.

    Зачем отдельный тест: если провайдер введёт long-context тир
    (типичный порог — 100K), скачок будет ВЫШЕ нашей сетки, и
    ``test_growth_is_linear`` его не увидит. Здесь проверяем
    40K→300K широким шагом, плюс окрестность 100K.
    """
    info = catalog.get(model)
    assert info is not None
    wide = (*GRID, 100_000, 150_000, 200_000, 300_000)
    wide = tuple(t for t in wide if t <= info.context)
    if len(wide) < 3:
        pytest.skip(f"{model}: context={info.context} слишком мал")
    costs = [_cost(tokens, model) for tokens in wide]
    deltas = [
        (b - a) / (tb - ta)
        for (ta, a), (tb, b) in pairwise(zip(wide, costs, strict=True))
    ]
    rate = info.input_price / 1_000_000
    for got in deltas:
        assert got == pytest.approx(rate, rel=1e-6, abs=TOL), (
            f"{model}: ставка за токен меняется по диапазону — похоже на тир "
            f"(ждали {rate:.9f} ₽/токен, точки {wide})"
        )


@pytest.mark.parametrize("model", PRICED)
def test_no_jump_at_soft_limit_boundary(model):
    """На самом пороге сжатия цена не изламывается.

    Проверяем окрестность ``soft_limit`` (включая значения чуть ниже и
    чуть выше): если бы на пороге менялась ставка, прирост здесь
    отличался бы от прироста на дальнем участке сетки.
    """
    soft = soft_limit(type("S", (), {"model": model})())
    far = _cost(90_000, model) - _cost(80_000, model)
    near = [
        _cost(soft - 1, model),
        _cost(soft, model),
        _cost(soft + 1, model),
    ]
    assert near[1] - near[0] == pytest.approx(near[2] - near[1], rel=1e-6, abs=TOL)
    # Ставка та же, что на дальнем участке: одна цена за 1 токен везде.
    rate_far = far / 10_000
    rate_near = (near[2] - near[0]) / 2
    assert rate_near == pytest.approx(rate_far, rel=1e-6, abs=TOL)


@pytest.mark.parametrize("model", config.CURATED_MODEL_IDS)
def test_soft_limit_formula(model):
    """Где заканчивается линейный участок: min(0.6 * context, MAX_HISTORY_TOKENS).

    Выше этой границы рост стоимости всё ещё линейный, но запрос
    начинает платить ещё и за сжатие — поэтому порог фиксируем тестом.
    """
    info = catalog.get(model)
    assert info is not None
    expected = min(int(info.context * 0.6), config.MAX_HISTORY_TOKENS)
    assert soft_limit(type("S", (), {"model": model})()) == expected


# ---------------------------------------------------------------------------
# Long-context тир: единственный настоящий скачок цены в системе
# ---------------------------------------------------------------------------
#
# В прайсинге RouterAI тиров нет (pricing — скаляры), поэтому цена в
# ModelInfo линейна. Но у части моделей у провайдера есть long-context
# режим: выше порога пересчитывается ВСЯ ставка, не только превышение
# (haiku 5.5: >100K → ×5 — обоснование MAX_HISTORY_TOKENS=90000, коммит
# 43ce75b). model_price() этот излом воспроизводит; тесты ниже держат его
# форму, чтобы «незаметно линейный» прайсинг не разошёлся с реальным счётом.


def test_tier_constants_match_provider_pricing():
    """Порог и множитель тира — те, что даёт провайдер (>100K → ×5)."""
    assert TIER_THRESHOLD_TOKENS == 100_000
    assert TIER_FACTOR == 5.0
    # MAX_HISTORY_TOKENS подобран как 0.9 × порог тира (запас под
    # расхождение эвристики и реальных токенов).
    assert config.MAX_HISTORY_TOKENS < TIER_THRESHOLD_TOKENS


@pytest.mark.parametrize("model", PRICED)
def test_no_tier_cliff_inside_history_limit(model):
    """Внутри истории (до MAX_HISTORY_TOKENS) тира нет — иначе сжатие
    не спасает: каждый запрос платит дорогую ставку."""
    for tokens in GRID:
        linear = _cost(tokens, model)  # input + фиксированные 2000 output
        tiered = model_price(model, tokens, output_tokens=_VERIFY_EST_OUTPUT_TOKENS)
        assert linear == pytest.approx(tiered, rel=1e-9), (
            f"{model} на {tokens}: тир сработал ниже MAX_HISTORY_TOKENS"
        )


@pytest.mark.parametrize("model", PRICED)
def test_tier_cliff_is_x5_at_threshold(model):
    """Выше порога цена скачет ×5 сразу — пересчёт всей ставки.

    Множитель здесь — литерал, а не TIER_FACTOR из модуля: иначе подмена
    константы на 1.0 проходила бы незамеченной (обе стороны сравнения
    менялись бы одинаково).
    """
    below = model_price(model, TIER_THRESHOLD_TOKENS)
    above = model_price(model, TIER_THRESHOLD_TOKENS + 1)
    assert above / below == pytest.approx(5.0, rel=0.01)


def test_tier_threshold_is_strictly_greater():
    """Порог строго «больше»: ровно 100K — ещё дешёвый тир."""
    model = PRICED[0]
    info = catalog.get(model)
    assert info is not None
    at = model_price(model, TIER_THRESHOLD_TOKENS)
    assert at == pytest.approx(TIER_THRESHOLD_TOKENS * info.input_price / 1e6)


@pytest.mark.parametrize("model", PRICED)
def test_model_price_is_linear_below_threshold(model):
    """Ниже порога model_price совпадает с обычной формулой токен×ставка."""
    info = catalog.get(model)
    assert info is not None
    for tokens in GRID:
        expected = (tokens * info.input_price) / 1e6
        assert model_price(model, tokens) == pytest.approx(expected, rel=1e-9)


@pytest.mark.parametrize("model", PRICED)
def test_model_price_counts_output(model):
    """Выход входит в ту же ставку и в тот же множитель тира."""
    info = catalog.get(model)
    assert info is not None
    out = 2000
    with_out = model_price(model, 50_000, output_tokens=out)
    without = model_price(model, 50_000)
    assert with_out - without == pytest.approx(out * info.output_price / 1e6)
    # Выше порога оба компонента тоже ×TIER_FACTOR.
    with_out_hi = model_price(model, TIER_THRESHOLD_TOKENS + 1000, output_tokens=out)
    without_hi = model_price(model, TIER_THRESHOLD_TOKENS + 1000)
    assert with_out_hi - without_hi == pytest.approx(
        out * info.output_price / 1e6 * 5.0
    )


def test_model_price_unknown_or_free_is_zero():
    """Нет модели/цены — 0.0 (посчитать нельзя), а не выдуманное число."""
    assert model_price("нет/такой-модели", 50_000) == 0.0


@pytest.mark.parametrize("model", PRICED)
def test_summary_cost_at_soft_limit_stays_in_cheap_tier(model):
    """Сжатие при штатном MAX_HISTORY_TOKENS не должно попадать в тир.

    old-блок доходит до soft_limit (recent может быть маленьким), поэтому
    проверяем именно границу: суммаризатор должен успеть до 100K.
    """
    soft = config.MAX_HISTORY_TOKENS
    assert soft < TIER_THRESHOLD_TOKENS, (
        f"MAX_HISTORY_TOKENS={soft} уже за порогом тира {TIER_THRESHOLD_TOKENS}"
    )
    summary_entry = model_price(config.SUMMARY_MODEL, soft)
    summary_tier = model_price(config.SUMMARY_MODEL, TIER_THRESHOLD_TOKENS + 1)
    assert summary_entry < summary_tier


def test_extra_pricing_keys_do_not_break_parsing():
    """Живой API кроме prompt/completion отдаёт input_cache_read/write,
    web_search и т.п. Парсер обязан брать только две скалярные ставки
    и не превращать цену в ноль из-за незнакомых ключей.
    """
    raw = {
        "id": config.DEFAULT_MODEL_ID,
        "context_length": 1_000_000,
        "architecture": {"input_modalities": ["text"]},
        "supported_parameters": ["tools"],
        "pricing": {
            "prompt": 2.5e-06,
            "completion": 1.0e-05,
            "input_cache_read": 1.0e-06,
            "input_cache_write": 3.0e-06,
            "web_search": 1.0633311,
        },
        "pricing_units": {"prompt": "token", "completion": "token"},
    }
    info = ModelCatalog()._parse_one(raw)
    assert info.input_price == pytest.approx(2.5, rel=1e-9)
    assert info.output_price == pytest.approx(10.0, rel=1e-9)
