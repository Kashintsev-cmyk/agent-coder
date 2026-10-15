"""Тесты сжатия истории (agent_coder.agent.compression)."""
from __future__ import annotations

from agent_coder.agent.compression import (
    find_cut,
    format_for_summary,
    soft_limit,
)
from agent_coder.config import config


def _mk_session(model="openai/gpt-5.6-luna"):
    from agent_coder.agent.prompts import Mode
    from agent_coder.agent.session import Session
    return Session.new(workspace="/tmp", model=model, mode=Mode.ACT)


# --- soft_limit ---

def test_soft_limit_uses_min_of_context_and_cap():
    s = _mk_session("openai/gpt-5.6-luna")  # context 1_050_000
    # 0.6 * 1_050_000 = 630_000 → берётся cap из config
    assert soft_limit(s) == config.MAX_HISTORY_TOKENS


def test_soft_limit_small_model():
    # kimi-k2.7-code context=262_144 → 0.6*262_144 ≈ 157_286 → cap из config
    s = _mk_session("moonshotai/kimi-k2.7-code")
    assert soft_limit(s) == config.MAX_HISTORY_TOKENS


def test_soft_limit_unknown_model_returns_cap():
    s = _mk_session("no/such-model-xyz")
    assert soft_limit(s) == config.MAX_HISTORY_TOKENS


def test_soft_limit_context_wins_when_smaller_than_cap(monkeypatch):
    """Если 0.6*context < MAX_HISTORY_TOKENS — порог задаёт модель, а не cap."""
    from agent_coder.model_catalog import catalog

    class _TinyModel:
        id = "test/tiny"
        context = 10_000
        name = "Tiny"

    monkeypatch.setattr(catalog, "get", lambda mid: _TinyModel())
    s = _mk_session("test/tiny")
    assert soft_limit(s) == 6_000


# --- find_cut ---

def _msgs(seq: list[str]) -> list[dict]:
    return [{"role": r, "content": ""} for r in seq]


def test_find_cut_short_returns_none():
    assert find_cut(_msgs(["system", "user", "assistant"])) is None


def test_find_cut_prefers_recent_user():
    # 12 сообщений, ищем user/assistant до индекса total-min_recent
    msgs = _msgs(["system"] + ["user"] * 11)
    cut = find_cut(msgs, min_recent=6)
    assert cut is not None
    assert msgs[cut]["role"] == "user"
    assert len(msgs) - cut >= 6


def test_find_cut_skips_tool():
    # Последние 6 — tool, ищем выше
    msgs = _msgs(["system"] + ["user", "assistant"] + ["tool"] * 8)
    cut = find_cut(msgs, min_recent=6)
    assert cut is not None
    assert msgs[cut]["role"] in ("user", "assistant")


def test_find_cut_no_user_or_assistant_in_range():
    # только tool в доступном диапазоне, но system на 0
    msgs = _msgs(["system"] + ["tool"] * 11)
    assert find_cut(msgs, min_recent=6) is None


# --- format_for_summary ---

def test_format_for_summary_basic():
    msgs = [
        {"role": "user", "content": "задача"},
        {"role": "assistant", "content": "думаю"},
        {"role": "tool", "content": "результат"},
    ]
    out = format_for_summary(msgs)
    assert "ПОЛЬЗОВАТЕЛЬ" in out
    assert "АССИСТЕНТ" in out
    assert "[TOOL]" in out


def test_format_for_summary_truncates_tool():
    msgs = [{"role": "tool", "content": "x" * 5000}]
    out = format_for_summary(msgs)
    # [TOOL]: + 500 символов + обвязка
    assert len(out) < 600


def test_format_for_summary_assistant_with_tool_calls():
    msgs = [{
        "role": "assistant",
        "content": "",
        "tool_calls": [{"function": {"name": "read_file"}}],
    }]
    out = format_for_summary(msgs)
    assert "вызвал: read_file" in out


# --- estimate_tokens ---

def test_estimate_tokens_empty():
    from agent_coder.agent.compression import estimate_tokens
    assert estimate_tokens([]) == 0


def test_estimate_tokens_grows_with_text():
    from agent_coder.agent.compression import estimate_tokens
    small = estimate_tokens([{"role": "user", "content": "x" * 400}])
    big = estimate_tokens([{"role": "user", "content": "x" * 4000}])
    assert small == 100   # 400 символов / 4
    assert big == 1000
    assert big > small


def test_estimate_tokens_counts_tool_calls():
    from agent_coder.agent.compression import estimate_tokens
    plain = estimate_tokens([{"role": "assistant", "content": "hi"}])
    with_tc = estimate_tokens([{
        "role": "assistant",
        "content": "hi",
        "tool_calls": [{"function": {"name": "run_command", "arguments": "{}"}}],
    }])
    assert with_tc > plain


def test_estimate_tokens_handles_multimodal_content():
    from agent_coder.agent.compression import estimate_tokens
    n = estimate_tokens([{
        "role": "user",
        "content": [
            {"type": "text", "text": "x" * 80},
            {"type": "image_url", "image_url": {"url": "data:..."}},
        ],
    }])
    assert n > 0


# --- compress (с mock-клиентом) ---

import pytest

from agent_coder.agent.compression import compress
from agent_coder.llm.types import Usage


class _FakeMessage:
    def __init__(self, content: str):
        self.content = content


class _FakeResponse:
    def __init__(self, content: str, usage: Usage):
        self.message = _FakeMessage(content)
        self.usage = usage


class _FakeClient:
    def __init__(self, reply: str = "кратко"):
        self.reply = reply
        self.calls: list[dict] = []

    async def chat(self, messages, **kwargs):
        self.calls.append({"messages": messages, "kwargs": kwargs})
        return _FakeResponse(
            self.reply,
            Usage(input_tokens=100, output_tokens=20, cost=0.01),
        )


@pytest.mark.asyncio
async def test_compress_replaces_old():
    s = _mk_session()
    # system + 14 user/assistant пар
    s.messages = [{"role": "system", "content": "SYSTEM"}]
    for i in range(14):
        s.messages.append({"role": "user", "content": f"задача {i}"})
        s.messages.append({"role": "assistant", "content": f"ответ {i}"})

    before = len(s.messages)
    client = _FakeClient(reply="СЖАТО")
    comp = await compress(s, client)
    assert comp is not None
    assert comp.tokens_before > 0
    assert comp.tokens_after < comp.tokens_before
    assert comp.messages_before == before
    assert comp.messages_after == len(s.messages)
    assert comp.summary_count == 1

    # system на месте
    assert s.messages[0]["role"] == "system"
    assert s.messages[0]["content"] == "SYSTEM"
    # второй элемент — резюме
    assert s.messages[1]["role"] == "user"
    assert "СЖАТО" in s.messages[1]["content"]
    assert "сжатие #1" in s.messages[1]["content"]
    # стало меньше
    assert len(s.messages) < before
    assert s.summary_count == 1
    assert s.last_input_tokens == 0


@pytest.mark.asyncio
async def test_compress_short_history_returns_false():
    s = _mk_session()
    s.messages = [{"role": "system", "content": "S"}]
    s.messages.append({"role": "user", "content": "hi"})
    s.messages.append({"role": "assistant", "content": "ok"})

    client = _FakeClient()
    ok = await compress(s, client)
    assert ok is None
    assert len(client.calls) == 0  # LLM не вызвали


@pytest.mark.asyncio
async def test_compress_empty_reply_returns_false():
    s = _mk_session()
    s.messages = [{"role": "system", "content": "S"}]
    for i in range(14):
        s.messages.append({"role": "user", "content": f"u{i}"})
        s.messages.append({"role": "assistant", "content": f"a{i}"})

    client = _FakeClient(reply="   ")  # пусто после strip
    ok = await compress(s, client)
    assert ok is None
    assert s.summary_count == 0


@pytest.mark.asyncio
async def test_compress_llm_error_returns_false():
    s = _mk_session()
    s.messages = [{"role": "system", "content": "S"}]
    for i in range(14):
        s.messages.append({"role": "user", "content": f"u{i}"})
        s.messages.append({"role": "assistant", "content": f"a{i}"})

    class _BoomClient:
        async def chat(self, *a, **kw):
            raise RuntimeError("boom")

    ok = await compress(s, _BoomClient())
    assert ok is None
    assert s.summary_count == 0


@pytest.mark.asyncio
async def test_compress_preserves_recent():
    s = _mk_session()
    s.messages = [{"role": "system", "content": "S"}]
    for i in range(10):
        s.messages.append({"role": "user", "content": f"u{i}"})
        s.messages.append({"role": "assistant", "content": f"a{i}"})
    # Последние два — контрольные (recent)
    s.messages.append({"role": "user", "content": "СВЕЖАЯ_ЗАДАЧА"})
    s.messages.append({"role": "assistant", "content": "СВЕЖИЙ_ОТВЕТ"})

    client = _FakeClient(reply="SUMMARY")
    await compress(s, client)

    # Хвост — те же свежие
    assert s.messages[-2]["content"] == "СВЕЖАЯ_ЗАДАЧА"
    assert s.messages[-1]["content"] == "СВЕЖИЙ_ОТВЕТ"

