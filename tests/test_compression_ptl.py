"""Retry при context-length в compress (Claude Code truncateHeadForPTLRetry).

Проблема (аудит 2026-10-19, MAJOR-3): если old-блок огромный, промпт
суммаризатора может не влезть в контекст SUMMARY_MODEL → 400
context-length → _register_failure → cooldown 600с → compress вернёт
None → история остаётся переполненной на 10 минут.

Фикс: при context-length дропаем старейшие API-раунды (assistant + его
tool-результаты — атомарно) и retry, до 3 раз. Оригинал old целиком
уходит в history_archive (search_history найдёт).
"""

from __future__ import annotations

import pytest

from agent_coder.agent import compression as _comp
from agent_coder.agent.compression import (
    _drop_oldest_api_round,
    _is_context_length_error,
    compress,
)
from agent_coder.agent.prompts import Mode
from agent_coder.agent.session import Session
from agent_coder.llm.types import Usage

# ---------- _is_context_length_error ----------


def test_context_length_markers() -> None:
    for msg in (
        "Error code: 400 - maximum context length is 65536 tokens",
        "prompt is too long: 70000 tokens > 65536",
        "request too large for model",
        "Input is too long for this model",
        "Reduce the length of the messages",
    ):
        assert _is_context_length_error(RuntimeError(msg)), msg


def test_non_context_errors_ignored() -> None:
    for msg in (
        "Error code: 402 - Insufficient balance",
        "rate limit exceeded",
        "internal server error",
        "invalid api key",
    ):
        assert not _is_context_length_error(RuntimeError(msg)), msg


# ---------- _drop_oldest_api_round ----------


def _round(user: str, tool: str, result: str) -> list[dict]:
    """Один API-раунд: user + assistant(tool_call) + tool-результат."""
    return [
        {"role": "user", "content": user},
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [
                {
                    "id": tool,
                    "type": "function",
                    "function": {"name": "read_file", "arguments": "{}"},
                }
            ],
        },
        {"role": "tool", "tool_call_id": tool, "content": result},
    ]


def test_drop_keeps_atomic_rounds() -> None:
    """assistant + его tool-результаты уходят/остаются вместе."""
    old: list[dict] = []
    for i in range(5):
        old.extend(_round(f"u{i}", f"t{i}", f"r{i}"))
    dropped = _drop_oldest_api_round(old)
    assert dropped is not None
    # Не осиротели tool_call: каждый tool_call_id имеет tool-ответ.
    call_ids = {tc["id"] for m in dropped for tc in (m.get("tool_calls") or [])}
    result_ids = {m["tool_call_id"] for m in dropped if m.get("role") == "tool"}
    assert call_ids == result_ids, "tool_call без tool-результата → 400 у провайдера"
    # Часть старейших дропнута (20% от 5 = 1 раунд).
    assert "u0" not in [m.get("content") for m in dropped]


def test_drop_returns_none_when_too_short() -> None:
    assert _drop_oldest_api_round([]) is None
    assert _drop_oldest_api_round([{"role": "user", "content": "x"}]) is None
    # Один раунд — дропать нечего, последний не трогаем.
    single = _round("u", "t", "r")
    assert _drop_oldest_api_round(single) is None


# ---------- compress: retry ----------


class _FakeMessage:
    def __init__(self, content: str):
        self.content = content
        self.tool_calls: list = []


class _FakeResponse:
    def __init__(self, content: str, usage: Usage):
        self.message = _FakeMessage(content)
        self.usage = usage


def _mk_session() -> Session:
    s = Session.new(workspace="/tmp", model="openai/gpt-5.6-luna", mode=Mode.ACT)
    s.messages = [{"role": "system", "content": "S"}]
    # 14 раундов user/assistant — стабильная old-часть.
    for i in range(14):
        s.messages.append({"role": "user", "content": f"задача {i}"})
        s.messages.append({"role": "assistant", "content": f"ответ {i}"})
    return s


class _PtlThenOkClient:
    """Первые N вызовов — context-length, потом успех."""

    def __init__(self, fail_times: int = 1):
        self.fail_times = fail_times
        self.calls = 0

    async def chat(self, messages, **kwargs):
        self.calls += 1
        if self.calls <= self.fail_times:
            raise RuntimeError("Error code: 400 - maximum context length exceeded")
        return _FakeResponse(
            "### Задача\n- x", Usage(input_tokens=100, output_tokens=20, cost=0.01)
        )


@pytest.mark.asyncio
async def test_compress_retries_on_context_length() -> None:
    """context-length → дроп старейших + retry → успех."""
    s = _mk_session()
    client = _PtlThenOkClient(fail_times=1)
    comp = await compress(s, client)
    assert comp is not None, "retry должен был спасти"
    assert client.calls == 2, "1 фейл + 1 успех"


@pytest.mark.asyncio
async def test_compress_gives_up_after_max_retries() -> None:
    """context-length всегда → после 3 retry сдаёмся, cooldown."""
    s = _mk_session()
    client = _PtlThenOkClient(fail_times=99)
    comp = await compress(s, client)
    assert comp is None
    # 1 + 3 retry = 4 вызова максимум
    assert client.calls <= 1 + _comp._PTL_MAX_RETRIES


@pytest.mark.asyncio
async def test_compress_non_ptl_error_no_retry() -> None:
    """Обычная ошибка (не context-length) — без retry, сразу failure."""

    class _BoomClient:
        calls = 0

        async def chat(self, *a, **kw):
            self.calls += 1
            raise RuntimeError("internal server error")

    s = _mk_session()
    client = _BoomClient()
    comp = await compress(s, client)
    assert comp is None
    assert client.calls == 1  # без retry


@pytest.mark.asyncio
async def test_archive_gets_full_old_even_after_drop() -> None:
    """При PTL-дропе в history_archive уходит ПОЛНЫЙ old, не урезанный."""
    from agent_coder.agent import history_archive

    s = _mk_session()
    client = _PtlThenOkClient(fail_times=1)
    comp = await compress(s, client)
    assert comp is not None
    archived = history_archive.load(s.id)
    # old = messages[1:cut] (cut ~ MIN_RECENT=6 → recent = последние 3
    # пары 11,12,13; в архив идут 0..10). Суть: даже если PTL-retry
    # дропнул старейшие раунды для LLM, в архив уходит ПОЛНЫЙ old —
    # задача 0 (самая старая, кандидат на дроп) должна быть.
    archived_text = " ".join(str(m.get("content") or "") for m in archived)
    assert "задача 0" in archived_text, "старейшее сообщение потеряно из архива"
    assert "задача 5" in archived_text
    # recent (11..13) в архив не идёт — они остаются в messages.
    assert "задача 13" not in archived_text
