# Session log — 2026-11-17

## Что сделано

### chat_bot
- Приведён к project_standard (docs/STYLE.md, AGENTS.md, CI mypy, lock).
- P0 (capability registry) закрыт: модели с caps (vision/file/tools/
  web_search/context_window), reasoning-фильтр в llm.py, retryable LLMError,
  clamp в settings, tier-UI, session key by chat_id, topic sessions.
- docs/STYLE.md выровнен с реальным unit-именем (chat-bot.service).

### agent-coder
- Удалены мёртвые/неполные модели из _FALLBACK_CATALOG:
  deepseek-r1 (503), gpt-5.6-luna (ctx=0, prices=0), mimo-v2.6-flash
  (prices=0), gpt-5-nano/gpt-5.4/gpt-5.4-mini/gemini-3.5-flash (TODO).
- Починены caps/prices у haiku-4.5, sonnet-4.6, gpt-6.1-sol по probe
  /v1/models (2026-11-16).
- tests/test_catalog_freshness.py — no stray / no zero ctx+prices / no TODO.
- tests/test_router.py decouple от deepseek-r1.
- Коммит: c3a317a.

## Probe-факты (RouterAI, 2026-11-16)

- Claude 5.5: reasoning_effort принимает low..max (все 5), reasoning_tokens
  в usage не репортится (0), cost растёт по effort.
- GPT-6*: reasoning_tokens репортится (Luna-Pro 65, Terra-Pro 26), seed
  поддерживается.
- DeepSeek v4: полный sampling, v4-pro = text-only (без vision/file).
- deepseek-r1: 503 No available providers.

## Открыто (сверено с кодом 2026-11-21)

Список перепроверен по факту — часть пунктов уже закрыта, и держать их
в «открытых» значит вводить следующую сессию в заблуждение.

**Закрыто (проверено, не переоткрывать):**

- ~~`mypy` pre-existing 24 errors~~ — `mypy`: `Success: no issues found in
  67 source files`.
- ~~`model_catalog.py` (API+TTL), «сейчас models.py статический»~~ —
  `async refresh()` + `_cache_ttl_hours` + дисковый кэш (`_CACHE_VERSION=5`).
- ~~fallback-цепочка моделей (Opus→Sonnet→Haiku)~~ — `router.next_on_error`
  + `RouterState.chain_index` + `FALLBACK_ERRORS` (A9: ротация при 429
  убрана осознанно, вместо неё стоп и продолжение кнопкой).
- ~~`web_search` tool-loop~~ — инструмент `web_search` есть (один вызов +
  источники из `url_citation`); многошаговый цикл уточнений — нет, и не
  планировался.

**Реально открыто:**

- P1: **суточный ₽-лимит на пользователя** — нет `MAX_COST_PER_DAY`;
  есть только `MAX_COST_PER_TASK` / `MAX_COST_PER_SESSION`.
- P1: **PTL-retry** (повтор при prompt-too-long после обрезки) — не
  реализовано; сейчас ошибка контекста уходит в fallback-цепочку/стоп.
- P2: **streaming SSE** — в `llm/client.py` нет `stream=True`/`astream`;
  ответ приходит целиком.
- ~~P3: time-of-day тариф дремлет~~ — **закрыто 2026-11-22**: провайдер
  берётся из ответа (`ChatResponse.provider`), цены — из
  `/models/{id}/endpoints` (`ModelInfo.provider_prices`), база окна
  сверяется с ценой того же провайдера. См. FINDINGS B7.

## B7 — time-of-day тариф (2026-11-22)

**Проблема.** `_tod_factors()` умножал множители окон на `m.input_price`
из `/models`, но окна принадлежат конкретному провайдеру, а `pricing` —
другому. На 8 curated-моделях база не совпала ни у одной → множитель
всегда 1.0, тариф «спал».

**Что сделано.**

- `llm/types.py` — `ChatResponse.provider`; `llm/client.py` —
  `_extract_provider()` (top-level `provider` ответа chat/completions,
  **не** в `usage`), прокидывается в `ChatResponse` и в лог.
- `models.py` — `ModelInfo.provider_prices: dict[str, tuple[float,float,float]]`
  (in/out/cache_read ₽ за 1M для каждого провайдера).
- `model_catalog.py` — `_extract_provider_prices()` из
  `/models/{id}/endpoints`, вызов в `_merge_endpoints`; `_CACHE_VERSION`
  5→6; нормализация списков в кортежи при чтении кэша.
- `agent/compression.py` — `_provider_rate()`, `_tod_factors(provider=)`,
  `model_price(provider=)`; прокинуто в оба вызова при сжатии
  (`summary_cost` fallback и warning long-context).

**Probe 2026-11-22** (`deepseek/deepseek-v4.1-flash`): в ответе
`"provider":"BytePlus"` — top-level, `usage.cost` приходит сразу. В
`/endpoints` 33 провайдера, окна только у BytePlus (6, in=35.26 /
out=141.04 / cache_read=0.7052 ₽/1M) и Alibaba (2, 10.578/42.31),
у остальных 31 — пусто.

**Проверено.** `tests/test_provider_pricing.py` — 23 теста (написаны до
реализации, падали на `ImportError: _extract_provider`); полный pytest
1450 passed; ruff check/format, mypy (68 файлов), bandit — чисто.

**Диагностика.** `scripts/tod_probe.py` — read-only: печатает ставки всех
провайдеров, окна и множитель на каждый час текущих суток тем же кодом,
что в бою (`compression._tod_factors`). Запуск:
`.venv/bin/python scripts/tod_probe.py deepseek/deepseek-v4.1-flash`.
