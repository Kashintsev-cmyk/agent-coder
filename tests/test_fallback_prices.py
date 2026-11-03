"""Регресс: _FALLBACK_CATALOG содержит ненулевые цены и ctx для MODEL_IDS.

Зачем: verify._estimate_verify_cost() считает pre-check бюджета по
catalog.get() (API → cache → fallback). Если price_in=0 — pre-check
отключается (return None). Если цена занижена — бюджет недосчитывается.

compress.soft_limit() читает context из catalog. context=0 → soft_limit
уходит в MAX_HISTORY_TOKENS (не модели-специфичный порог).

Поэтому для всех моделей из Config.MODEL_IDS в _FALLBACK_CATALOG
обязательны: input_price > 0, output_price > 0, context > 0.
"""

from __future__ import annotations

from agent_coder.config import Config
from agent_coder.models import _FALLBACK_CATALOG


def test_all_model_ids_in_fallback_catalog() -> None:
    missing = [mid for mid in Config.MODEL_IDS if mid not in _FALLBACK_CATALOG]
    assert not missing, (
        f"MODEL_IDS содержит модели без записи в _FALLBACK_CATALOG: {missing}. "
        "Добавь ModelInfo — иначе verify pre-check отключится, compress "
        "soft_limit уйдёт в дефолт."
    )


def test_all_model_ids_have_prices_and_context() -> None:
    bad: list[str] = []
    for mid in Config.MODEL_IDS:
        m = _FALLBACK_CATALOG[mid]
        if m.input_price <= 0:
            bad.append(f"{mid}: input_price={m.input_price}")
        if m.output_price <= 0:
            bad.append(f"{mid}: output_price={m.output_price}")
        if m.context <= 0:
            bad.append(f"{mid}: context={m.context}")
    assert not bad, "Ненулевые цены/context обязательны для MODEL_IDS:\n" + "\n".join(
        bad
    )
