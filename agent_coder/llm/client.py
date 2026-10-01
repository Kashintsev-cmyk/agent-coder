"""LLM-клиент с поддержкой tool calls и точной стоимости от RouterAI."""
from __future__ import annotations

import asyncio
import json
import logging
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

logger = logging.getLogger(__name__)


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
            http_client=self._http,
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
    ) -> ChatResponse:
        """Один вызов LLM с retry.

        `reasoning_effort` — "off" | "low" | "medium" | "high" | None.
        "off" или None — параметр не передаётся (модель работает без reasoning).
        """
        kwargs: dict[str, Any] = {"model": model, "messages": messages}
        if tools:
            kwargs["tools"] = tools
        if tool_choice:
            kwargs["tool_choice"] = tool_choice
        if temperature is not None:
            kwargs["temperature"] = temperature

        # Reasoning: RouterAI принимает none, minimal, low, medium, high, xhigh, max, ultra.
        # `none` = не передаём ничего, чтобы модель работала без thinking.
        if reasoning_effort and reasoning_effort not in ("none", ""):
            kwargs["reasoning_effort"] = reasoning_effort

        logger.info(
            "LLM → model=%s msgs=%d tools=%d reasoning=%s",
            model, len(messages),
            len(tools) if tools else 0,
            reasoning_effort or "-",
        )

        response = await self._with_retry(
            lambda: self.client.chat.completions.create(**kwargs),
            op_name=f"chat({model})",
        )

        logger.info(
            "LLM ← in=%s out=%s cost=%s",
            getattr(response.usage, "prompt_tokens", "?"),
            getattr(response.usage, "completion_tokens", "?"),
            getattr(response.usage, "cost", "?"),
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
            content=msg.content,
            tool_calls=tool_calls,
            reasoning=reasoning,
        )
        usage = _extract_usage(response)
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
            wait = backoff ** i
            logger.warning(
                "%s: попытка %d/%d провалена (%s), ждём %.1fs",
                op_name, i + 1, attempts, type(last).__name__, wait,
            )
            if i < attempts - 1:
                await asyncio.sleep(wait)
        assert last is not None
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
