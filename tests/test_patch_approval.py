"""B1c+M4 (2026-11-07): approval-guard в patch-цикле + run_checks в PATCH_TOOLS.

Регресс на `8f0cd848`/M4: patch-цикл не звал `needs_approval` — approval-tools
исполнялись без подтверждения. После M3 (`defac89`) pytest через `run_checks`
уже безопасен; `run_command` убран из `PATCH_TOOLS`; добавлен guard.
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


class _ApprovalTool:
    """Tool с requires_approval=True, всегда требующий подтверждения."""

    read_only = False
    requires_approval = True

    def __init__(self, name: str, calls: list):
        self.name = name
        self._calls = calls

    def to_openai_schema(self):
        return {"type": "function", "function": {"name": self.name}}

    def needs_approval(self, args):
        return True

    async def executor(self, args, ctx):
        self._calls.append(ctx.yolo)
        return f"ok: {self.name}"


class _FreeTool:
    """Tool без approval, read_only — control."""

    read_only = True
    requires_approval = False

    def __init__(self, name: str, calls: list):
        self.name = name
        self._calls = calls

    def to_openai_schema(self):
        return {"type": "function", "function": {"name": self.name}}

    def needs_approval(self, args):
        return False

    async def executor(self, args, ctx):
        self._calls.append(ctx.yolo)
        return f"ok: {self.name}"


class _Reg:
    def __init__(self, tools: dict):
        self._tools = tools

    def get(self, name):
        return self._tools.get(name)

    def is_call_allowed(self, mode, tool, args):
        return True


def _confirmed():
    return {"status": "confirmed", "text": "A", "evidence": "a.py:1", "run_id": "r1"}


def _session() -> Session:
    s = Session.new(workspace="/tmp", model="m", mode=Mode.ACT)
    s.verify_results = [_confirmed()]
    return s


def _tool_call(name: str) -> ToolCall:
    return ToolCall(id="t1", name=name, arguments={})


@pytest.mark.asyncio
async def test_approval_tool_rejected_without_yolo(monkeypatch):
    """Без yolo: approval-tool не исполняется, ошибка в tool-result."""
    import agent_coder.agent.patch as patch_mod

    # Инструмент должен быть в PATCH_TOOLS, иначе reject на whitelist-уровне.
    monkeypatch.setattr(patch_mod, "PATCH_TOOLS", ("read_file", "my_approval_tool"))
    calls: list = []
    reg = _Reg(
        {
            "read_file": _FreeTool("read_file", calls),
            "my_approval_tool": _ApprovalTool("my_approval_tool", calls),
        }
    )
    client = _ScriptedClient(
        [
            _FakeResponse("", tool_calls=[_tool_call("my_approval_tool")]),
            _FakeResponse("готово"),
        ]
    )
    res = await run_patch(client=client, session=_session(), registry=reg, yolo=False)
    # tool не вызывался
    assert calls == [], calls
    assert res.iterations >= 1


@pytest.mark.asyncio
async def test_approval_tool_allowed_with_yolo(monkeypatch):
    """С yolo=True: approval-tool исполняется (backward-compat для CLI --yolo)."""
    import agent_coder.agent.patch as patch_mod

    monkeypatch.setattr(patch_mod, "PATCH_TOOLS", ("read_file", "my_approval_tool"))
    calls: list = []
    reg = _Reg(
        {
            "read_file": _FreeTool("read_file", calls),
            "my_approval_tool": _ApprovalTool("my_approval_tool", calls),
        }
    )
    client = _ScriptedClient(
        [
            _FakeResponse("", tool_calls=[_tool_call("my_approval_tool")]),
            _FakeResponse("готово"),
        ]
    )
    await run_patch(client=client, session=_session(), registry=reg, yolo=True)
    # Смысл теста: approval-tool исполнен (ctx.yolo=True) — не блокирован.
    assert calls == [True], calls


@pytest.mark.asyncio
async def test_free_tool_works_without_yolo():
    """Control: tool без approval исполняется всегда."""
    calls: list = []
    reg = _Reg({"read_file": _FreeTool("read_file", calls)})
    client = _ScriptedClient(
        [
            _FakeResponse("", tool_calls=[_tool_call("read_file")]),
            _FakeResponse("готово"),
        ]
    )
    res = await run_patch(client=client, session=_session(), registry=reg, yolo=False)
    assert calls == [False], calls
    assert res.error == ""


def test_patch_tools_no_run_command():
    """M4: `run_command` убран из PATCH_TOOLS (заменён на run_checks)."""
    assert "run_command" not in PATCH_TOOLS
    assert "run_checks" in PATCH_TOOLS
    assert "read_file" in PATCH_TOOLS
    assert "write_file" in PATCH_TOOLS
    assert "edit_file" in PATCH_TOOLS
