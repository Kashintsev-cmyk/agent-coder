"""Тесты /verify (B9): парсинг вердиктов, бюджет, model_manual не влияет."""

from __future__ import annotations

import pytest

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
