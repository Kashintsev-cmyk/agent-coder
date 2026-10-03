"""Тесты _with_retry и _parse_retry_after."""
from __future__ import annotations

from types import SimpleNamespace

import httpx
import pytest
from openai import APIConnectionError, APIError, APITimeoutError, RateLimitError

from agent_coder.llm.client import LLMClient, _parse_retry_after


def _http_response(headers: dict | None = None) -> httpx.Response:
    """Настоящий httpx.Response с request — нужен конструкторам openai."""
    req = httpx.Request("GET", "https://api.example/")
    return httpx.Response(429, headers=headers or {}, request=req)


def _make_client() -> LLMClient:
    # __init__ не требует сеть, но проверим — просто создаём
    return LLMClient()


# ---------- _parse_retry_after ----------

def test_parse_retry_after_seconds():
    exc = SimpleNamespace(response=SimpleNamespace(headers={"Retry-After": "30"}))
    assert _parse_retry_after(exc) == 30.0


def test_parse_retry_after_lowercase_header():
    exc = SimpleNamespace(response=SimpleNamespace(headers={"retry-after": "5"}))
    assert _parse_retry_after(exc) == 5.0


def test_parse_retry_after_http_date():
    # Дата в прошлом → 0
    exc = SimpleNamespace(
        response=SimpleNamespace(
            headers={"Retry-After": "Wed, 21 Oct 2015 07:28:00 GMT"}
        )
    )
    assert _parse_retry_after(exc) == 0.0


def test_parse_retry_after_missing():
    exc = SimpleNamespace(response=SimpleNamespace(headers={}))
    assert _parse_retry_after(exc) is None


def test_parse_retry_after_no_response():
    exc = SimpleNamespace()
    assert _parse_retry_after(exc) is None


def test_parse_retry_after_garbage():
    exc = SimpleNamespace(response=SimpleNamespace(headers={"Retry-After": "not-a-number"}))
    assert _parse_retry_after(exc) is None


# ---------- _with_retry ----------

@pytest.mark.asyncio
async def test_retry_success_first_try():
    """Успех — вызов ровно один раз, без sleep."""
    client = _make_client()
    calls: list[int] = []

    async def factory():
        calls.append(1)
        return "ok"

    result = await client._with_retry(factory, "test")
    assert result == "ok"
    assert len(calls) == 1


@pytest.mark.asyncio
async def test_retry_eventually_succeeds():
    """Первый вызов падает timeout, второй — ок."""
    client = _make_client()
    attempts: list[int] = []

    async def factory():
        attempts.append(1)
        if len(attempts) == 1:
            raise APITimeoutError(request=SimpleNamespace())  # type: ignore
        return "ok"

    # jitter не должен добавить реального времени — backoff = 0
    import agent_coder.llm.client as c
    old = c.config.RETRY_BACKOFF
    c.config.RETRY_BACKOFF = 0.001
    try:
        result = await client._with_retry(factory, "test")
    finally:
        c.config.RETRY_BACKOFF = old

    assert result == "ok"
    assert len(attempts) == 2


@pytest.mark.asyncio
async def test_retry_4xx_no_429_raises_immediately():
    """400/401/403/404 не повторяются."""
    client = _make_client()
    calls: list[int] = []

    async def factory():
        calls.append(1)
        err = APIError("bad", request=SimpleNamespace(), body=None)
        err.status_code = 400  # type: ignore
        raise err

    with pytest.raises(APIError):
        await client._with_retry(factory, "test")
    assert len(calls) == 1


@pytest.mark.asyncio
async def test_retry_exhausted_raises_last():
    """Все попытки провалены — бросаем последнее исключение."""
    client = _make_client()

    async def factory():
        raise APIConnectionError(request=SimpleNamespace())  # type: ignore

    import agent_coder.llm.client as c
    old_max, old_backoff = c.config.MAX_RETRIES, c.config.RETRY_BACKOFF
    c.config.MAX_RETRIES = 2
    c.config.RETRY_BACKOFF = 0.001
    try:
        with pytest.raises(APIConnectionError):
            await client._with_retry(factory, "test")
    finally:
        c.config.MAX_RETRIES = old_max
        c.config.RETRY_BACKOFF = old_backoff


@pytest.mark.asyncio
async def test_retry_respects_retry_after(monkeypatch):
    """Если есть Retry-After — sleep = это значение (а не backoff)."""
    client = _make_client()
    sleeps: list[float] = []

    async def fake_sleep(t):
        sleeps.append(t)

    monkeypatch.setattr("asyncio.sleep", fake_sleep)

    err = RateLimitError(
        "rate limited",
        response=_http_response({"Retry-After": "7"}),
        body=None,
    )

    calls: list[int] = []

    async def factory():
        calls.append(1)
        if len(calls) == 1:
            raise err
        return "ok"

    import agent_coder.llm.client as c
    old = c.config.RETRY_BACKOFF
    c.config.RETRY_BACKOFF = 0.001
    try:
        await client._with_retry(factory, "test")
    finally:
        c.config.RETRY_BACKOFF = old

    # Первый sleep должен быть 7 (Retry-After), с учётом min(..., 60)
    assert sleeps and sleeps[0] == pytest.approx(7.0)


@pytest.mark.asyncio
async def test_retry_jitter_in_range(monkeypatch):
    """Без Retry-After — wait в диапазоне [base, base*1.25]."""
    client = _make_client()
    sleeps: list[float] = []

    async def fake_sleep(t):
        sleeps.append(t)

    monkeypatch.setattr("asyncio.sleep", fake_sleep)

    async def factory():
        raise APIConnectionError(request=SimpleNamespace())  # type: ignore

    import agent_coder.llm.client as c
    old_max, old_backoff = c.config.MAX_RETRIES, c.config.RETRY_BACKOFF
    c.config.MAX_RETRIES = 2
    c.config.RETRY_BACKOFF = 1.0
    try:
        with pytest.raises(APIConnectionError):
            await client._with_retry(factory, "test")
    finally:
        c.config.MAX_RETRIES = old_max
        c.config.RETRY_BACKOFF = old_backoff

    # Единственный sleep — base=1.0 + jitter 0..0.25 → 1.0..1.25
    assert len(sleeps) == 1
    assert 1.0 <= sleeps[0] <= 1.25


@pytest.mark.asyncio
async def test_retry_capped_at_60s(monkeypatch):
    """Даже если сервер просит 300 — ждём максимум 60."""
    client = _make_client()
    sleeps: list[float] = []

    async def fake_sleep(t):
        sleeps.append(t)

    monkeypatch.setattr("asyncio.sleep", fake_sleep)

    err = RateLimitError(
        "rate limited",
        response=_http_response({"Retry-After": "300"}),
        body=None,
    )

    calls: list[int] = []

    async def factory():
        calls.append(1)
        if len(calls) == 1:
            raise err
        return "ok"

    await client._with_retry(factory, "test")
    assert sleeps and sleeps[0] == 60.0
