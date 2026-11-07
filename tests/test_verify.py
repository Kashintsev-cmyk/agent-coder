"""Тесты /verify (B9): парсинг вердиктов, бюджет, model_manual не влияет."""

from __future__ import annotations

import pytest

from agent_coder.agent import verify as verify_mod
from agent_coder.agent.prompts import Mode
from agent_coder.agent.session import Session
from agent_coder.agent.verify import (
    MAX_VERIFY_ITERATIONS,
    VERIFY_TOOLS,
    parse_verdicts,
    run_verify,
)
from agent_coder.config import config
from agent_coder.llm.types import AssistantMessage, ChatResponse, ToolCall, Usage

# --- parse_verdicts ---


def test_parse_confirmed_rejected_uncertain():
    text = (
        "CONFIRMED: Foo is None | app/foo.py:10\n"
        "REJECTED: bar missing | app/bar.py:1 — bar есть\n"
        "UNCERTAIN: baz | нет файла\n"
    )
    out = parse_verdicts(text)
    assert [v["status"] for v in out] == ["confirmed", "rejected", "uncertain"]
    assert out[0]["text"] == "Foo is None"
    assert out[0]["evidence"] == "app/foo.py:10"
    assert out[1]["evidence"] == "app/bar.py:1 — bar есть"


def test_parse_markdown_bullets_and_backticks():
    text = "- **CONFIRMED**: x | y:1\n* `REJECTED`: z | причина\n"
    out = parse_verdicts(text)
    assert [v["status"] for v in out] == ["confirmed", "rejected"]


def test_parse_unparsed_and_empty():
    assert parse_verdicts("EMPTY\n") == []
    out = parse_verdicts("какой-то текст\nCONFIRMED: x | y:1")
    assert out[0]["status"] == "unparsed"
    assert out[1]["status"] == "confirmed"


def test_parse_empty_text():
    assert parse_verdicts("") == []
    assert parse_verdicts("   \n") == []


# --- run_verify ---


class _FakeClient:
    def __init__(self, replies: list[ChatResponse]):
        self._replies = list(replies)
        self.calls: list[dict] = []

    async def chat(self, messages, **kwargs):
        self.calls.append({"messages": messages, **kwargs})
        if not self._replies:
            raise RuntimeError("нет ответов")
        return self._replies.pop(0)


def _usage(cost: float = 0.0) -> Usage:
    return Usage(input_tokens=10, output_tokens=5, cost=cost)


def _session(work_state=None) -> Session:
    s = Session.new(
        workspace="/tmp", model="deepseek/deepseek-v4-flash", mode=Mode.AUDIT
    )
    s.work_state = work_state or {"findings": ["foo is None in app/foo.py:10"]}
    return s


@pytest.mark.asyncio
async def test_run_verify_empty_work_state():
    s = Session.new(workspace="/tmp", model="deepseek/deepseek-v4-flash", mode=Mode.ACT)
    client = _FakeClient([])
    res = await run_verify(client=client, session=s)
    assert res.empty_work_state
    assert not res.ok
    assert client.calls == []


@pytest.mark.asyncio
async def test_run_verify_ok_no_tools():
    s = _session()
    reply = ChatResponse(
        message=AssistantMessage(
            content="CONFIRMED: foo is None | app/foo.py:10", tool_calls=[]
        ),
        usage=_usage(cost=1.5),
    )
    client = _FakeClient([reply])
    res = await run_verify(client=client, session=s)
    assert res.ok, res.error
    assert res.cost == 1.5
    assert res.counts.get("confirmed") == 1
    assert s.verify_results[-1]["status"] == "confirmed"
    assert s.verify_results[-1]["run_id"] == res.run_id
    assert res.model == config.VERIFY_MODEL_ID


@pytest.mark.asyncio
async def test_run_verify_does_not_touch_session_messages():
    s = _session()
    s.messages = [
        {"role": "system", "content": "OLD"},
        {"role": "user", "content": "audit task"},
    ]
    before = list(s.messages)
    reply = ChatResponse(
        message=AssistantMessage(content="CONFIRMED: x | y:1", tool_calls=[]),
        usage=_usage(),
    )
    await run_verify(client=_FakeClient([reply]), session=s)
    assert s.messages == before


@pytest.mark.asyncio
async def test_run_verify_does_not_inherit_model_manual():
    s = _session()
    s.model = "anthropic/claude-sonnet-5.5"
    s.model_manual = True
    reply = ChatResponse(
        message=AssistantMessage(content="CONFIRMED: x | y:1", tool_calls=[]),
        usage=_usage(),
    )
    client = _FakeClient([reply])
    res = await run_verify(client=client, session=s)
    assert res.model == config.VERIFY_MODEL_ID
    assert client.calls[0]["model"] == config.VERIFY_MODEL_ID


@pytest.mark.asyncio
async def test_run_verify_explicit_model_override():
    s = _session()
    reply = ChatResponse(
        message=AssistantMessage(content="REJECTED: x | y:1", tool_calls=[]),
        usage=_usage(),
    )
    client = _FakeClient([reply])
    res = await run_verify(client=client, session=s, model="moonshotai/kimi-k2.7-code")
    assert res.model == "moonshotai/kimi-k2.7-code"
    assert client.calls[0]["model"] == "moonshotai/kimi-k2.7-code"


@pytest.mark.asyncio
async def test_run_verify_tool_call_whitelist():
    s = _session()
    reply1 = ChatResponse(
        message=AssistantMessage(
            content=None,
            tool_calls=[
                ToolCall(
                    id="c1", name="write_file", arguments={"path": "x", "content": "y"}
                )
            ],
        ),
        usage=_usage(),
    )
    reply2 = ChatResponse(
        message=AssistantMessage(content="CONFIRMED: x | y:1", tool_calls=[]),
        usage=_usage(),
    )
    client = _FakeClient([reply1, reply2])
    res = await run_verify(client=client, session=s)
    assert res.ok
    second_msgs = client.calls[1]["messages"]
    tool_msgs = [m for m in second_msgs if m.get("role") == "tool"]
    assert tool_msgs, "tool result не добавлен"
    assert "недоступен" in tool_msgs[0]["content"]


@pytest.mark.asyncio
async def test_run_verify_budget_exceeded():
    s = _session()
    s.work_state = {"findings": ["x", "y", "z"]}
    replies = [
        ChatResponse(
            message=AssistantMessage(
                content=None,
                tool_calls=[
                    ToolCall(id=f"c{i}", name="read_file", arguments={"path": "x"})
                ],
            ),
            usage=_usage(cost=30.0),
        )
        for i in range(5)
    ]
    res = await run_verify(client=_FakeClient(replies), session=s)
    assert res.budget_exceeded
    assert res.iterations < MAX_VERIFY_ITERATIONS


@pytest.mark.asyncio
async def test_run_verify_bad_format_records_unparsed():
    s = _session()
    reply = ChatResponse(
        message=AssistantMessage(content="Что-то не то", tool_calls=[]),
        usage=_usage(),
    )
    res = await run_verify(client=_FakeClient([reply]), session=s)
    assert res.counts.get("unparsed", 0) >= 1


@pytest.mark.asyncio
async def test_run_verify_empty_reply_is_error():
    s = _session()
    reply = ChatResponse(
        message=AssistantMessage(content="", tool_calls=[]), usage=_usage()
    )
    res = await run_verify(client=_FakeClient([reply]), session=s)
    assert res.error


def test_verify_tools_whitelist_is_read_only():
    from agent_coder.tools import build_registry

    reg = build_registry()
    for name in VERIFY_TOOLS:
        tool = reg.get(name)
        assert tool is not None, name
        assert tool.read_only, f"{name} не read-only"


@pytest.mark.asyncio
async def test_run_verify_timeout(monkeypatch):
    """B9.2g.6: таймаут chat → res.error='LLM: timeout after 180s', не raise."""
    import asyncio

    real_wait_for = asyncio.wait_for

    async def _fast_wait_for(coro, timeout):
        # Сжимаем 180s → 0.01s, чтобы тест не ждал 3 минуты.
        # Проверяем, что run_verify реально оборачивает chat в wait_for.
        return await real_wait_for(coro, timeout=0.01)

    monkeypatch.setattr(asyncio, "wait_for", _fast_wait_for)

    class _HangingClient:
        async def chat(self, messages, **kwargs):
            await asyncio.sleep(3600)
            raise AssertionError("unreachable")

    s = _session()
    res = await run_verify(client=_HangingClient(), session=s)
    assert res.error == "LLM: timeout after 180s"
    assert not res.ok


# --------------------------------------------------------------------------
# B11.x: pre-check MAX_COST_VERIFY перед client.chat.
# Контракт: est(cost + next) > MAX_COST_VERIFY -> budget_exceeded, chat не
# вызывается. est is None (цена неизвестна) -> пропуск, работает post-check.
# Граница: est == MAX_COST_VERIFY не блокирует (строгое >).
# --------------------------------------------------------------------------


class _PreTool:
    name = "read_file"
    read_only = True

    def to_openai_schema(self):
        return {"type": "function", "function": {"name": "read_file"}}

    async def executor(self, args, ctx):
        return ""


class _PreReg:
    def get(self, name):
        return _PreTool() if name in verify_mod.VERIFY_TOOLS else None


class _PreResp:
    def __init__(self):
        self.usage = Usage(input_tokens=100, output_tokens=50, cost=0.5)
        self.message = AssistantMessage(content="- CONFIRMED: fake", tool_calls=[])


class _PreClient:
    def __init__(self):
        self.calls = 0

    async def chat(self, **kw):
        self.calls += 1
        return _PreResp()


class _PreSession:
    id = "pre-test"
    workspace = "/tmp"
    reasoning_effort = "none"

    def __init__(self):
        self.audit_scopes: list[str] = []
        self.work_state = {"k": "v"}
        self.verify_results = []
        self.total_usage = Usage()

    def save(self):
        pass


def _patch_verify_inputs(monkeypatch):
    """Обойти work_state/standard-промпт, оставить pre-check живым."""
    monkeypatch.setattr(verify_mod, "render_for_prompt", lambda _: "fake ws")
    monkeypatch.setattr(verify_mod, "load_verify_standard", lambda: "fake system")


@pytest.mark.asyncio
async def test_verify_precheck_blocks(monkeypatch):
    _patch_verify_inputs(monkeypatch)
    monkeypatch.setattr(verify_mod, "_estimate_cost_rub", lambda *_: 60.0)
    monkeypatch.setattr(verify_mod.config, "MAX_COST_VERIFY", 50.0)
    client = _PreClient()
    res = await verify_mod.run_verify(client, _PreSession(), registry=_PreReg())
    assert res.budget_exceeded is True
    assert client.calls == 0
    assert res.iterations == 1


@pytest.mark.asyncio
async def test_verify_precheck_passes(monkeypatch):
    _patch_verify_inputs(monkeypatch)
    monkeypatch.setattr(verify_mod, "_estimate_cost_rub", lambda *_: 1.0)
    monkeypatch.setattr(verify_mod.config, "MAX_COST_VERIFY", 50.0)
    client = _PreClient()
    res = await verify_mod.run_verify(client, _PreSession(), registry=_PreReg())
    assert res.budget_exceeded is False
    assert client.calls >= 1


@pytest.mark.asyncio
async def test_verify_precheck_skips_when_unknown_price(monkeypatch):
    _patch_verify_inputs(monkeypatch)
    monkeypatch.setattr(verify_mod, "_estimate_cost_rub", lambda *_: None)
    monkeypatch.setattr(verify_mod.config, "MAX_COST_VERIFY", 50.0)
    client = _PreClient()
    await verify_mod.run_verify(client, _PreSession(), registry=_PreReg())
    assert client.calls >= 1  # pre-check пропущен, chat вызван


@pytest.mark.asyncio
async def test_verify_precheck_boundary_strict_gt(monkeypatch):
    """est == MAX_COST_VERIFY не блокирует: контракт строгого >."""
    _patch_verify_inputs(monkeypatch)
    monkeypatch.setattr(verify_mod.config, "MAX_COST_VERIFY", 50.0)
    monkeypatch.setattr(verify_mod, "_estimate_cost_rub", lambda *_: 50.0)
    client = _PreClient()
    res = await verify_mod.run_verify(client, _PreSession(), registry=_PreReg())
    assert res.budget_exceeded is False
    assert client.calls >= 1


@pytest.mark.asyncio
async def test_run_verify_chat_exception_appends_error_record():
    """P0b: chat упал (TypeError от SDK/профиля) → verify_results[-1].status='error'."""

    class _BrokenClient:
        async def chat(self, messages, **kwargs):
            raise TypeError("unexpected keyword argument 'top_k'")

    s = _session()
    res = await run_verify(client=_BrokenClient(), session=s)
    assert res.error.startswith("LLM:")
    assert not res.ok
    assert s.verify_results, "error-прогон должен попасть в историю"
    last = s.verify_results[-1]
    assert last["status"] == "error"
    assert "top_k" in last["text"]
    assert res.counts.get("error") == 1
