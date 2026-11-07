"""B1a (2026-11-07): patch прокидывает session.audit_scopes в ToolContext.

Регресс на `8f0cd848`/B1a: patch.py:294 создавал `ToolContext(yolo=True)`
без `audit_scopes` → read_file/write_file внутри patch игнорировали scoped-audit.
Тест через registry-injection (`run_patch(..., registry=...)`).
"""

from __future__ import annotations

import pytest

from agent_coder.agent.patch import PATCH_TOOLS, run_patch
from agent_coder.agent.prompts import Mode
from agent_coder.agent.session import Session
from agent_coder.llm.types import ToolCall, Usage


class _FakeMessage:
    def __init__(self, content: str, tool_calls=None):
        self.content = content
        self.tool_calls = tool_calls or []


class _FakeResponse:
    def __init__(self, content: str, tool_calls=None, cost: float = 0.01):
        self.message = _FakeMessage(content, tool_calls)
        self.usage = Usage(input_tokens=100, output_tokens=50, cost=cost)


class _ScriptedClient:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = 0

    async def chat(self, messages, **kwargs):
        self.calls += 1
        idx = min(self.calls - 1, len(self.responses) - 1)
        return self.responses[idx]


class _CapturingTool:
    """Фейковый tool — фиксирует ctx.audit_scopes и возвращает ok."""

    read_only = True

    def __init__(self, name: str, sink: list):
        self.name = name
        self._sink = sink

    def to_openai_schema(self):
        return {"type": "function", "function": {"name": self.name}}

    def needs_approval(self, args) -> bool:
        # M4: patch-цикл теперь зовёт needs_approval — fake обязан.
        return False

    async def executor(self, args, ctx):
        self._sink.append(ctx.audit_scopes)
        return f"ok: {self.name}"


class _CapturingRegistry:
    def __init__(self, sink: list):
        self._sink = sink

    def get(self, name):
        if name in PATCH_TOOLS:
            return _CapturingTool(name, self._sink)
        return None

    def is_call_allowed(self, mode, tool, args):
        # M4: контракт ToolRegistry — fake обязан реализовать.
        return True


def _confirmed(text: str = "A", evidence: str = "a.py:1") -> dict:
    return {"status": "confirmed", "text": text, "evidence": evidence, "run_id": "r1"}


def _tool_call(path: str) -> ToolCall:
    return ToolCall(id="t1", name="read_file", arguments={"path": path})


def _session(scopes: list[str]) -> Session:
    s = Session.new(workspace="/tmp", model="m", mode=Mode.ACT)
    s.audit_scopes = list(scopes)
    s.verify_results = [_confirmed()]
    return s


@pytest.mark.asyncio
async def test_patch_propagates_audit_scopes():
    """B1a: session.audit_scopes=['bot/'] → ctx.audit_scopes=['bot/']."""
    sink: list = []
    reg = _CapturingRegistry(sink)
    client = _ScriptedClient(
        [
            _FakeResponse("", tool_calls=[_tool_call("bot/x.py")]),
            _FakeResponse("готово"),
        ]
    )
    res = await run_patch(client=client, session=_session(["bot/"]), registry=reg)
    assert res.error == "", res.error
    assert sink, "tool не вызвался"
    assert sink[0] == ["bot/"], sink


@pytest.mark.asyncio
async def test_patch_empty_audit_scopes_becomes_none():
    """B1a: пустой audit_scopes → None (без ограничений, backward-compat)."""
    sink: list = []
    reg = _CapturingRegistry(sink)
    client = _ScriptedClient(
        [
            _FakeResponse("", tool_calls=[_tool_call("anything")]),
            _FakeResponse("готово"),
        ]
    )
    res = await run_patch(client=client, session=_session([]), registry=reg)
    assert res.error == "", res.error
    assert sink, "tool не вызвался"
    assert sink[0] is None, sink
