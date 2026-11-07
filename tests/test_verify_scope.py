"""M1 (2026-11-07): verify прокидывает session.audit_scopes в ToolContext.

Регресс на finding `8f0cd848`/M1: verify.py создавал ToolContext без
audit_scopes → read_file/list_files внутри verify читали весь workspace,
даже если /audit был scoped (`--scope bot/`).

Тест через registry-injection (`run_verify(..., registry=...)`):
фейковый `read_file` фиксирует `ctx.audit_scopes` из реального исполнения.
"""

from __future__ import annotations

import pytest

from agent_coder.agent import verify as verify_mod
from agent_coder.agent.prompts import Mode
from agent_coder.agent.session import Session
from agent_coder.agent.verify import run_verify
from agent_coder.llm.types import AssistantMessage, ChatResponse, ToolCall, Usage


def _patch_inputs(monkeypatch):
    """Обойти work_state/standard-промпт, оставить ToolContext-путь живым."""
    monkeypatch.setattr(verify_mod, "render_for_prompt", lambda _: "fake ws")
    monkeypatch.setattr(verify_mod, "load_verify_standard", lambda: "fake system")


class _CapturingTool:
    """Фейковый read_file: пишет ctx.audit_scopes в sink."""

    name = "read_file"
    read_only = True

    def __init__(self, sink: list):
        self._sink = sink

    def to_openai_schema(self):
        return {"type": "function", "function": {"name": "read_file"}}

    async def executor(self, args, ctx):
        self._sink.append(ctx.audit_scopes)
        return "ok"


class _CapturingReg:
    def __init__(self):
        self.captured: list = []

    def get(self, name):
        if name in verify_mod.VERIFY_TOOLS:
            return _CapturingTool(self.captured)
        return None


class _ScriptClient:
    def __init__(self, replies: list[ChatResponse]):
        self._replies = list(replies)
        self.calls: list = []

    async def chat(self, messages, **kwargs):
        self.calls.append(kwargs)
        if not self._replies:
            raise RuntimeError("нет ответов")
        return self._replies.pop(0)


def _reply_tool() -> ChatResponse:
    return ChatResponse(
        message=AssistantMessage(
            content="",
            tool_calls=[
                ToolCall(id="t1", name="read_file", arguments={"path": "bot/x.py"})
            ],
        ),
        usage=Usage(input_tokens=10, output_tokens=5, cost=0.0),
    )


def _reply_text() -> ChatResponse:
    return ChatResponse(
        message=AssistantMessage(
            content="CONFIRMED: foo is None | app/foo.py:10", tool_calls=[]
        ),
        usage=Usage(input_tokens=10, output_tokens=5, cost=0.0),
    )


def _session(scopes: list[str]) -> Session:
    s = Session.new(
        workspace="/tmp", model="deepseek/deepseek-v4-flash", mode=Mode.AUDIT
    )
    s.work_state = {"findings": ["x"]}
    s.audit_scopes = list(scopes)
    return s


@pytest.mark.asyncio
async def test_verify_propagates_audit_scopes(monkeypatch):
    """M1: session.audit_scopes=['bot/'] → ctx.audit_scopes=['bot/']."""
    _patch_inputs(monkeypatch)
    reg = _CapturingReg()
    client = _ScriptClient([_reply_tool(), _reply_text()])
    res = await run_verify(client=client, session=_session(["bot/"]), registry=reg)
    assert res.ok, res.error
    assert reg.captured == [["bot/"]], reg.captured


@pytest.mark.asyncio
async def test_verify_empty_audit_scopes_becomes_none(monkeypatch):
    """M1: пустой audit_scopes → None (без ограничений, contract read_file)."""
    _patch_inputs(monkeypatch)
    reg = _CapturingReg()
    client = _ScriptClient([_reply_tool(), _reply_text()])
    res = await run_verify(client=client, session=_session([]), registry=reg)
    assert res.ok, res.error
    assert reg.captured == [None], reg.captured
