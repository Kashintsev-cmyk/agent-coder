"""LLM-клиент с поддержкой tool calls и точной стоимости от RouterAI."""
from __future__ import annotations

import asyncio
import json
import logging
import random
import re
from typing import Any

import httpx
from openai import (
    APIConnectionError,
    APIError,
    APITimeoutError,
    AsyncOpenAI,
    RateLimitError,
)

from agent_coder.config import config
from agent_coder.llm.types import AssistantMessage, ChatResponse, ToolCall, Usage
from agent_coder.log_safety import SecretFilter

logger = logging.getLogger(__name__)
logger.addFilter(SecretFilter())


_DSML_RE = re.compile(
    r"<\|\|DSML\|\|.*?>.*?<\|\|DSML\|\|.*?>",
    re.DOTALL,
)
_DSML_SIMPLE_RE = re.compile(r"<\|\|DSML\|\|.*", re.DOTALL)


def _strip_dsml(text: str) -> str:
    """Убрать нативные DSML-теги DeepSeek из content.

    DeepSeek иногда возвращает вызовы инструментов в нативном формате
    `<||DSML||invoke ...>...</||DSML||invoke>`, минуя стандартные
    OpenAI tool_calls. Это ломает UI и вводит в заблуждение пользователя.
    RouterAI обычно мапит их корректно, но в редких конфигурациях
    (например, урезанный набор tools) утечка случается.
    """
    if not text or "DSML" not in text:
        return text
    text = _DSML_RE.sub("", text)
    text = _DSML_SIMPLE_RE.sub("", text)
    return text.strip()


def _build_request_kwargs(
    model: str,
    messages: list[dict[str, Any]],
    *,
    tools: list[dict[str, Any]] | None = None,
    tool_choice: str | None = None,
    temperature: float | None = None,
    reasoning_effort: str | None = None,
    web_search: bool = False,
    service_tier: str | None = None,
    verbosity: str | None = None,
) -> dict[str, Any]:
    """Собрать kwargs для chat.completions.create.

    Вынесено отдельно от chat() — чтобы тестировать без mock httpx/OpenAI.
    """
    kwargs: dict[str, Any] = {"model": model, "messages": messages}
    if tools:
        kwargs["tools"] = tools
    if tool_choice:
        kwargs["tool_choice"] = tool_choice
    if temperature is not None:
        kwargs["temperature"] = temperature
    if reasoning_effort and reasoning_effort not in ("none", ""):
        kwargs["reasoning_effort"] = reasoning_effort
    if web_search:
        kwargs["plugins"] = [{
            "id": "web",
            "engine": config.WEB_SEARCH_ENGINE,
            "max_results": config.WEB_SEARCH_MAX_RESULTS,
        }]
    if service_tier and service_tier not in ("default", ""):
        kwargs["service_tier"] = service_tier
    if verbosity:
        kwargs["verbosity"] = verbosity
    return kwargs


class LLMClient:
    """Обёртка над AsyncOpenAI, заточенная под RouterAI."""

    def __init__(self) -> None:
        self._client: AsyncOpenAI | None = None
        self._http: httpx.AsyncClient | None = None

    async def start(self) -> None:
        self._http = httpx.AsyncClient(
            timeout=httpx.Timeout(config.ROUTERAI_TIMEOUT, connect=15.0)
        )
        self._client = AsyncOpenAI(
            api_key=config.ROUTERAI_API_KEY,
            base_url=f"{config.ROUTERAI_BASE_URL.rstrip('/')}/",
            # openai 3.x использует вендоренный httpx (namespace "httpx2"),
            # mypy не видит совпадения типов с нашим httpx 0.28.
            # На рантайме это один и тот же класс.
            http_client=self._http,  # type: ignore[arg-type]
        )

    async def close(self) -> None:
        if self._http:
            await self._http.aclose()
            self._http = None
        self._client = None

    @property
    def client(self) -> AsyncOpenAI:
        if not self._client:
            raise RuntimeError("LLMClient не запущен. Вызови start().")
        return self._client

    async def chat(
        self,
        messages: list[dict[str, Any]],
        *,
        model: str,
        tools: list[dict[str, Any]] | None = None,
        tool_choice: str | None = None,
        temperature: float | None = None,
        reasoning_effort: str | None = None,
        web_search: bool = False,
        service_tier: str | None = None,
        verbosity: str | None = None,
    ) -> ChatResponse:
        """Один вызов LLM с retry.

        `reasoning_effort` — none | minimal | low | medium | high | xhigh |
        max | ultra | None. При "none" или None параметр не передаётся
        (модель работает без reasoning).
        """
        kwargs = _build_request_kwargs(
            model,
            messages,
            tools=tools,
            tool_choice=tool_choice,
            temperature=temperature,
            reasoning_effort=reasoning_effort,
            web_search=web_search,
            service_tier=service_tier,
            verbosity=verbosity,
        )

        logger.info(
            "LLM → model=%s msgs=%d tools=%d reasoning=%s",
            model, len(messages),
            len(tools) if tools else 0,
            reasoning_effort or "-",
        )

        try:
            response = await self._with_retry(
                lambda: self.client.chat.completions.create(**kwargs),
                op_name=f"chat({model})",
            )
        except Exception:
            # flex строго возвращает ошибку, если нет мощностей. Пробуем
            # один раз с default — пользователь не увидит провала.
            if service_tier == "flex":
                logger.warning("flex упал — повторяю запрос с service_tier=default")
                kwargs.pop("service_tier", None)
                response = await self._with_retry(
                    lambda: self.client.chat.completions.create(**kwargs),
                    op_name=f"chat({model})[flex→default]",
                )
            else:
                raise

        usage = _extract_usage(response)
        logger.info(
            "LLM ← in=%d out=%d cache=%d cost=%.4f",
            usage.input_tokens, usage.output_tokens,
            usage.cache_read_tokens, usage.cost,
        )

        choice = response.choices[0]
        msg = choice.message

        tool_calls: list[ToolCall] = []
        for tc in (msg.tool_calls or []):
            try:
                args = json.loads(tc.function.arguments) if tc.function.arguments else {}
            except json.JSONDecodeError as e:
                logger.warning("Плохой JSON в tool_call %s: %s", tc.function.name, e)
                args = {"_raw": tc.function.arguments, "_error": str(e)}
            tool_calls.append(ToolCall(id=tc.id, name=tc.function.name, arguments=args))

        reasoning = getattr(msg, "reasoning", None) or None

        assistant = AssistantMessage(
            content=_strip_dsml(msg.content or ""),
            tool_calls=tool_calls,
            reasoning=reasoning,
        )
        return ChatResponse(message=assistant, usage=usage, raw=response)

    async def _with_retry(self, factory, op_name: str):
        attempts = max(1, config.MAX_RETRIES)
        backoff = max(1.0, config.RETRY_BACKOFF)
        last: Exception | None = None
        for i in range(attempts):
            try:
                return await factory()
            except APITimeoutError as e:
                last = e
            except (APIConnectionError, RateLimitError) as e:
                last = e
            except APIError as e:
                status = getattr(e, "status_code", None) or 0
                if 400 <= status < 500 and status != 429:
                    raise
                last = e

            # Retry-After (если RouterAI его прислал) — приоритетнее
            # экспоненциального backoff.
            retry_after = _parse_retry_after(last)
            if retry_after is not None:
                wait = retry_after
                src = " [Retry-After]"
            else:
                base = backoff ** i
                # Jitter: 0..25% сверху, чтобы клиенты не били в одну точку
                wait = base + random.uniform(0, base * 0.25)
                src = ""
            # Потолок: не ждём больше 60 с, даже если сервер просит
            wait = min(wait, 60.0)

            logger.warning(
                "%s: попытка %d/%d провалена (%s), ждём %.2fs%s",
                op_name, i + 1, attempts, type(last).__name__, wait, src,
            )
            if i < attempts - 1:
                await asyncio.sleep(wait)

        if last is None:
            raise RuntimeError(
                f"{op_name}: retry-цикл завершился без исключения "
                f"(attempts={attempts}, вероятно, ошибка в логике)"
            )
        raise last

    async def get_balance(self) -> dict[str, Any]:
        """Получить баланс/лимиты аккаунта routerai."""
        if not self._http:
            raise RuntimeError("LLMClient не запущен")
        url = f"{config.ROUTERAI_BASE_URL.rstrip('/')}/credits"
        resp = await self._http.get(
            url,
            headers={"Authorization": f"Bearer {config.ROUTERAI_API_KEY}"},
            timeout=10.0,
        )
        resp.raise_for_status()
        return resp.json()

def _parse_retry_after(exc: Exception) -> float | None:
    """Retry-After из HTTP-ответа (секунды или HTTP-дата). None если нет.

    RouterAI/провайдеры могут присылать Retry-After в 429/503. Уважать его
    важнее, чем экспоненциальный backoff.
    """
    resp = getattr(exc, "response", None)
    if resp is None:
        return None
    headers = getattr(resp, "headers", None) or {}
    raw = headers.get("Retry-After") or headers.get("retry-after")
    if not raw:
        return None
    try:
        return max(0.0, float(raw))
    except (TypeError, ValueError):
        pass
    # HTTP-date формат: "Wed, 21 Oct 2015 07:28:00 GMT"
    try:
        from datetime import datetime, timezone
        from email.utils import parsedate_to_datetime
        dt = parsedate_to_datetime(raw)
        if dt is None:
            return None
        delta = (dt - datetime.now(timezone.utc)).total_seconds()
        return max(0.0, delta)
    except Exception:
        return None


def _extract_usage(response: Any) -> Usage:
    raw = getattr(response, "usage", None)
    if raw is None:
        return Usage()

    def _get(name: str, default: int = 0) -> int:
        value = getattr(raw, name, None)
        return int(value) if isinstance(value, (int, float)) else default

    input_tokens = _get("prompt_tokens") or _get("input_tokens")
    output_tokens = _get("completion_tokens") or _get("output_tokens")

    details = getattr(raw, "prompt_tokens_details", None)
    cache_read = 0
    if details is not None:
        cache_read = int(getattr(details, "cached_tokens", 0) or 0)

    cost: float = 0.0
    direct = getattr(raw, "cost", None)
    if isinstance(direct, (int, float)):
        cost = float(direct)
    if cost == 0.0:
        extra = getattr(raw, "model_extra", None) or {}
        val = extra.get("cost")
        if isinstance(val, (int, float)):
            cost = float(val)
    if cost == 0.0 and hasattr(raw, "model_dump"):
        dumped = raw.model_dump()
        val = dumped.get("cost")
        if isinstance(val, (int, float)):
            cost = float(val)

    return Usage(
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        cache_read_tokens=cache_read,
        cost=cost,
    )
