from __future__ import annotations

import pytest

from agent_coder.agent.loop import AgentLoop, LoopEvents
from agent_coder.agent.prompts import Mode
from agent_coder.agent.session import Session
from agent_coder.config import config
from agent_coder.llm.types import AssistantMessage, ChatResponse, ToolCall, Usage


class _ReadRegistry:
    class _Tool:
        read_only = True
        requires_approval = False

        def needs_approval(self, args):
            return False

        async def executor(self, args, ctx):
            return "file content"

    def openai_schemas(self, mode):
        return []

    def get(self, name):
        return self._Tool() if name == "read_file" else None

    def is_call_allowed(self, mode, tool, args):
        return True


class _Client:
    def __init__(self, *, cost: float):
        self.calls = 0
        self.cost = cost

    async def chat(self, messages, **kwargs):
        self.calls += 1
        return ChatResponse(
            message=AssistantMessage(
                content="",
                tool_calls=[
                    ToolCall(
                        id=f"call-{self.calls}",
                        name="read_file",
                        arguments={"path": "src/module.py"},
                    )
                ],
            ),
            usage=Usage(input_tokens=10, output_tokens=5, cost=self.cost),
        )


def _events() -> LoopEvents:
    async def approve(tc):
        return True

    return LoopEvents(
        on_assistant_text=lambda *args: None,
        on_tool_call=lambda *args: None,
        on_tool_result=lambda *args: None,
        on_usage=lambda *args: None,
        on_error=lambda *args: None,
        request_approval=approve,
    )


def _loop(session: Session, client: _Client) -> AgentLoop:
    return AgentLoop(
        client=client,
        registry=_ReadRegistry(),
        session=session,
        events=_events(),
    )


@pytest.mark.asyncio
async def test_task_budget_survives_continuation_and_new_request_resets_it(
    monkeypatch, tmp_path
) -> None:
    monkeypatch.setattr(config, "MAX_ITERATIONS_ACT", 1)
    monkeypatch.setattr(config, "MAX_COST_PER_TASK", 10.0)
    monkeypatch.setattr(config, "MAX_COST_PER_SESSION", 1000.0)
    session = Session.new(workspace=str(tmp_path), model="m", mode=Mode.ACT)
    session.messages = [{"role": "system", "content": "system"}]
    client = _Client(cost=6.0)

    first = await _loop(session, client).run("initial request")
    assert first.limit_reached
    first_task_id = session.task_state["id"]

    # Simulate Telegram loading the saved session for the continuation button.
    resumed = Session.from_dict(session.to_dict())
    continuation = await _loop(resumed, client).run("продолжи", continuation=True)
    assert continuation.cost_limit_reached
    assert continuation.task_cost == pytest.approx(12.0)
    assert resumed.task_state["id"] == first_task_id

    calls_at_budget_stop = client.calls
    repeated = await _loop(resumed, client).run("продолжи", continuation=True)
    assert repeated.cost_limit_reached
    assert client.calls == calls_at_budget_stop

    new_request = await _loop(resumed, client).run("new request")
    assert new_request.limit_reached
    assert resumed.task_state["id"] != first_task_id
    assert resumed.task_state["started_cost"] == pytest.approx(12.0)


@pytest.mark.asyncio
async def test_no_progress_watchdog_survives_continuation(
    monkeypatch, tmp_path
) -> None:
    monkeypatch.setattr(config, "MAX_ITERATIONS_ACT", 1)
    monkeypatch.setattr(config, "MAX_ITERATIONS_NO_PROGRESS", 1)
    # Первый запуск (1 чтение) должен пройти до лимита итераций; продолжение
    # доводит read-only счётчик до 2 и останавливает цикл по no_progress.
    monkeypatch.setattr(config, "MAX_READ_ONLY_ITERATIONS_ACT", 2)
    monkeypatch.setattr(config, "MAX_IDENTICAL_CALLS", 100)
    monkeypatch.setattr(config, "MAX_COST_PER_TASK", 100.0)
    monkeypatch.setattr(config, "MAX_COST_PER_SESSION", 1000.0)
    session = Session.new(workspace=str(tmp_path), model="m", mode=Mode.ACT)
    session.messages = [{"role": "system", "content": "system"}]
    client = _Client(cost=0.0)

    first = await _loop(session, client).run("audit files")
    assert first.limit_reached

    resumed = Session.from_dict(session.to_dict())
    continuation = await _loop(resumed, client).run("продолжи", continuation=True)
    assert continuation.no_progress
