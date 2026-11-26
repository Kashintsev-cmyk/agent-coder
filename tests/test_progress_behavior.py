"""Behavioral tests for progress detection in AgentLoop."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

import pytest

from agent_coder.agent import loop as loop_mod
from agent_coder.agent.loop import AgentLoop, LoopEvents
from agent_coder.agent.prompts import Mode
from agent_coder.agent.session import Session
from agent_coder.llm.types import AssistantMessage, ChatResponse, ToolCall, Usage


class _Tool:
    def __init__(
        self,
        name: str,
        executor: Callable[[dict[str, Any], Any], Awaitable[str]],
        *,
        read_only: bool,
        needs_approval: bool = False,
    ) -> None:
        self.name = name
        self.executor = executor
        self.read_only = read_only
        self.requires_approval = needs_approval
        self._needs_approval = needs_approval

    def needs_approval(self, args: dict[str, Any]) -> bool:
        return self._needs_approval


class _Registry:
    def __init__(self, *tools: _Tool) -> None:
        self.tools = {tool.name: tool for tool in tools}

    def openai_schemas(self, mode: Mode) -> list[dict[str, Any]]:
        return []

    def get(self, name: str) -> _Tool | None:
        return self.tools.get(name)

    def is_call_allowed(self, mode: Mode, tool: _Tool, args: dict[str, Any]) -> bool:
        return True


class _ScriptedClient:
    def __init__(self, calls: list[ToolCall | None]) -> None:
        self.calls = calls
        self.chat_calls = 0

    async def chat(self, messages: list[dict[str, Any]], **kwargs: Any) -> ChatResponse:
        self.chat_calls += 1
        call = self.calls[self.chat_calls - 1]
        return ChatResponse(
            message=AssistantMessage(
                content="done" if call is None else "",
                tool_calls=[] if call is None else [call],
            ),
            usage=Usage(input_tokens=10, output_tokens=5, cost=0.0),
        )


def _tool_call(name: str, arguments: dict[str, Any], call_id: str = "call") -> ToolCall:
    return ToolCall(id=call_id, name=name, arguments=arguments)


def _events(*, approved: bool = True) -> LoopEvents:
    async def request_approval(tc: ToolCall) -> bool:
        return approved

    return LoopEvents(
        on_assistant_text=lambda *args: None,
        on_tool_call=lambda *args: None,
        on_tool_result=lambda *args: None,
        on_usage=lambda *args: None,
        on_error=lambda *args: None,
        request_approval=request_approval,
    )


async def _run(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Any,
    calls: list[ToolCall | None],
    registry: _Registry,
    *,
    approved: bool = True,
) -> tuple[Any, _ScriptedClient, Session]:
    monkeypatch.setattr(loop_mod.config, "MAX_ITERATIONS_NO_PROGRESS", 1)
    monkeypatch.setattr(loop_mod.config, "MAX_IDENTICAL_CALLS", 10)
    monkeypatch.setattr(loop_mod.config, "MAX_COST_PER_TASK", 10_000.0)
    monkeypatch.setattr(loop_mod.config, "MAX_COST_PER_SESSION", 10_000.0)

    session = Session.new(workspace=str(tmp_path), model="m", mode=Mode.ACT)
    session.messages = [{"role": "system", "content": "S"}]
    client = _ScriptedClient(calls)
    agent_loop = AgentLoop(
        client=client,
        registry=registry,
        session=session,
        events=_events(approved=approved),
    )
    result = await agent_loop.run("test progress")
    return result, client, session


@pytest.mark.asyncio
async def test_new_read_path_counts_as_progress(monkeypatch, tmp_path) -> None:
    async def read_file(args: dict[str, Any], ctx: Any) -> str:
        return "file contents"

    result, client, _ = await _run(
        monkeypatch,
        tmp_path,
        [
            _tool_call("read_file", {"path": "src/new.py"}),
            None,
        ],
        _Registry(_Tool("read_file", read_file, read_only=True)),
    )

    assert not result.no_progress
    assert client.chat_calls == 2


@pytest.mark.asyncio
async def test_repeated_read_path_does_not_count_as_progress(
    monkeypatch, tmp_path
) -> None:
    async def read_file(args: dict[str, Any], ctx: Any) -> str:
        return "file contents"

    result, client, _ = await _run(
        monkeypatch,
        tmp_path,
        [
            _tool_call("read_file", {"path": "src/same.py"}, "first"),
            _tool_call("read_file", {"path": "src/same.py"}, "second"),
            None,
        ],
        _Registry(_Tool("read_file", read_file, read_only=True)),
    )

    assert result.no_progress
    assert client.chat_calls == 2


@pytest.mark.asyncio
async def test_user_rejection_does_not_count_as_progress(monkeypatch, tmp_path) -> None:
    async def write_file(args: dict[str, Any], ctx: Any) -> str:
        return "written"

    result, client, session = await _run(
        monkeypatch,
        tmp_path,
        [_tool_call("write_file", {"path": "src/change.py"})],
        _Registry(
            _Tool("write_file", write_file, read_only=False, needs_approval=True)
        ),
        approved=False,
    )

    assert result.no_progress
    assert client.chat_calls == 1
    assert any(
        message.get("content") == "Отклонено пользователем"
        for message in session.messages
        if message.get("role") == "tool"
    )


@pytest.mark.asyncio
async def test_tool_error_does_not_count_as_progress(monkeypatch, tmp_path) -> None:
    async def fail_write(args: dict[str, Any], ctx: Any) -> str:
        raise RuntimeError("write failed")

    result, client, session = await _run(
        monkeypatch,
        tmp_path,
        [_tool_call("write_file", {"path": "src/change.py"})],
        _Registry(_Tool("write_file", fail_write, read_only=False)),
    )

    assert result.no_progress
    assert client.chat_calls == 1
    assert any(
        str(message.get("content", "")).startswith("Ошибка выполнения инструмента")
        for message in session.messages
        if message.get("role") == "tool"
    )


@pytest.mark.asyncio
async def test_work_state_change_counts_as_progress(monkeypatch, tmp_path) -> None:
    async def update_work_state(args: dict[str, Any], ctx: Any) -> str:
        ctx.work_state.update({"findings+": ["new fact"]})
        return "updated"

    result, client, session = await _run(
        monkeypatch,
        tmp_path,
        [_tool_call("update_work_state", {"patch": {"findings+": ["new fact"]}}), None],
        _Registry(_Tool("update_work_state", update_work_state, read_only=True)),
    )

    assert not result.no_progress
    assert client.chat_calls == 2
    assert session.work_state["findings"] == ["new fact"]
