"""Диагностика тарифа time-of-day (B7).

Показывает ставки провайдеров, окна «время суток» и множитель на каждый час
текущих суток — тем же кодом, что работает в бою (`compression._tod_factors`).

Только чтение: кэш каталога и сессии не трогает.

    .venv/bin/python scripts/tod_probe.py deepseek/deepseek-v4.1-flash

Без аргумента берётся `MODEL_MAIN` из конфига.
"""

from __future__ import annotations

import asyncio
import sys
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx

from agent_coder.agent.compression import _provider_rate, _tod_factors
from agent_coder.config import config
from agent_coder.model_catalog import ModelCatalog
from agent_coder.models import ModelInfo


async def _fetch_endpoints(model_id: str) -> Any:
    url = f"{config.ROUTERAI_BASE_URL.rstrip('/')}/models/{model_id}/endpoints"
    headers = (
        {"Authorization": f"Bearer {config.ROUTERAI_API_KEY}"}
        if config.ROUTERAI_API_KEY
        else {}
    )
    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.get(url, headers=headers)
        resp.raise_for_status()
        return resp.json()


def _window_days(days: Any) -> str:
    return "все дни" if days is None else ",".join(days)


def _window_owner(info: ModelInfo, base: float) -> str:
    """Провайдер, чья ставка входа совпала с базой окна (логика `_tod_factors`)."""
    for name in info.provider_prices:
        rate = _provider_rate(info, name)
        if rate and base > 0 and abs(base - rate[0]) <= max(1e-9, rate[0] * 1e-6):
            return name
    return "?"


def _print_prices(info: ModelInfo) -> None:
    print(f"Цена /models: in={info.input_price} out={info.output_price} ₽/1M")
    print("\nСтавки провайдеров (₽/1M):")
    if not info.provider_prices:
        print("  — в каталоге нет (обновится при следующем refresh)")
        return
    rows = sorted(info.provider_prices.items(), key=lambda kv: kv[1][0])
    for name, (in_p, out_p, cache_p) in rows:
        print(
            f"  {name:<20} in={in_p:<12.4f} out={out_p:<12.4f} cache_read={cache_p:.4f}"
        )


def _print_windows(info: ModelInfo) -> str | None:
    """Печатает окна и возвращает провайдера, к которому они относятся."""
    if not info.tod_windows:
        print("\nОкон time-of-day нет — множитель всегда 1.0")
        return None
    print("\nОкна (UTC):")
    owner = ""
    for days, h_from, h_to, in_f, out_f, base in info.tod_windows:
        if not owner:
            owner = _window_owner(info, base)
        print(
            f"  {_window_days(days):<24} {h_from:02d}:00–{h_to:02d}:00  "
            f"in×{in_f:g} out×{out_f:g}  база={base:.6g} ₽"
        )
    return owner


def _print_hours(info: ModelInfo, provider: str) -> None:
    now = datetime.now(UTC)
    day = now.replace(hour=0, minute=0, second=0, microsecond=0)
    print(
        f"\nМножители на сегодня ({now:%A, %Y-%m-%d}, UTC), "
        f"провайдер {provider or '—'}:"
    )
    for h in range(24):
        when = day + timedelta(hours=h)
        in_f, out_f = _tod_factors(info, when, provider=provider)
        mark = "  ← сейчас" if when.hour == now.hour else ""
        print(f"  {h:02d}:00  in×{in_f:<5g} out×{out_f:<5g}{mark}")


async def main() -> int:
    model_id = sys.argv[1] if len(sys.argv) > 1 else config.MODEL_MAIN
    payload = await _fetch_endpoints(model_id)
    info = ModelCatalog()._merge_endpoints(
        ModelInfo(id=model_id, name=model_id, context=0, reasoning=False), payload
    )
    print(f"Модель: {model_id}")
    _print_prices(info)
    owner = _print_windows(info)
    if info.tod_windows:
        _print_hours(info, owner or "")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
