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

## Открыто (следующая сессия)

- P1: PTL-retry, fallback-цепочка моделей (Opus→Sonnet→Haiku),
  суточный ₽-лимит на пользователя.
- P1: model_catalog.py (API+TTL) — сейчас models.py статический.
- P2: streaming SSE, web_search tool-loop.
- agent-coder: mypy pre-existing 24 errors (loop.py, __main__.py).
