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
- P3: **time-of-day тариф дремлет** — парсинг и расчёт есть
  (`ModelInfo.tod_windows`, `_tod_factors`), но база окна не совпадает с
  ценой `/models` ни у одной из 8 моделей, поэтому множитель = 1.0.
  Нужен учёт фактического провайдера из ответа (см. FINDINGS B7).
