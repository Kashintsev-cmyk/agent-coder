"""Watchdog «нет прогресса»: loop на чтении останавливается."""

from __future__ import annotations

import pytest

from agent_coder.agent import loop as loop_mod
from agent_coder.agent.loop import AgentLoop, LoopEvents
from agent_coder.agent.prompts import Mode
from agent_coder.agent.session import Session
from agent_coder.llm.types import AssistantMessage, ChatResponse, ToolCall, Usage


class _ReadFileRegistry:
    """read_file — read_only; всё остальное отсутствует."""

    class _Tool:
        name = "read_file"
        read_only = True
        requires_approval = False

        def needs_approval(self, args):
            return False

        async def executor(self, args, ctx):
            return "file content (same every time)"

    def openai_schemas(self, mode):
        return []

    def get(self, name):
        return self._Tool() if name == "read_file" else None

    def is_call_allowed(self, mode, tool, args):
        return True


def _events() -> LoopEvents:
    async def request_approval(tc):
        return True

    return LoopEvents(
        on_assistant_text=lambda *a: None,
        on_tool_call=lambda *a: None,
        on_tool_result=lambda *a: None,
        on_usage=lambda *a: None,
        on_error=lambda *a: None,
        request_approval=request_approval,
    )


class _AlwaysReadClient:
    """Всегда просит read_file — loop без прогресса."""

    def __init__(self):
        self.calls = 0

    async def chat(self, messages, **kwargs):
        self.calls += 1
        return ChatResponse(
            message=AssistantMessage(
                content="",
                tool_calls=[
                    ToolCall(id=f"c{self.calls}", name="read_file", arguments={})
                ],
            ),
            usage=Usage(input_tokens=10, output_tokens=5, cost=0.0),
        )


@pytest.mark.asyncio
async def test_no_progress_stops_read_loop(monkeypatch) -> None:
    """read_file без изменения work_state → стоп через N итераций."""
    monkeypatch.setattr(loop_mod.config, "MAX_ITERATIONS_NO_PROGRESS", 5)
    monkeypatch.setattr(loop_mod.config, "MAX_COST_PER_TASK", 10_000.0)
    monkeypatch.setattr(loop_mod.config, "MAX_COST_PER_SESSION", 10_000.0)
    s = Session.new(workspace="/tmp", model="m", mode=Mode.ACT)
    s.messages = [{"role": "system", "content": "S"}]
    client = _AlwaysReadClient()
    loop = AgentLoop(
        client=client, registry=_ReadFileRegistry(), session=s, events=_events()
    )
    res = await loop.run("аудит")
    assert res.no_progress
    assert "прогресса" in res.error_message
    assert client.calls == 5  # ровно MAX_ITERATIONS_NO_PROGRESS, не 300


@pytest.mark.asyncio
async def test_progress_via_work_state_resets_counter(monkeypatch) -> None:
    """update_work_state (меняет state) — прогресс, счётчик сбрасывается."""
    monkeypatch.setattr(loop_mod.config, "MAX_ITERATIONS_NO_PROGRESS", 3)

    class _Tool:
        name = "update_work_state"
        read_only = True
        requires_approval = False

        def needs_approval(self, args):
            return False

        async def executor(self, args, ctx):
            # Уникальный факт на итерацию, но < MAX_ITEMS_PER_FIELD (50),
            # иначе normalize отбросит новые и state замрёт (это отдельный
            # корректный сценарий — см. test_no_progress_stops_read_loop).
            n = len(ctx.work_state.get()["findings"])
            ctx.work_state.update({"findings+": [f"f{n}"]})
            return "ok"

    class _Registry:
        def openai_schemas(self, mode):
            return []

        def get(self, name):
            return _Tool() if name == "update_work_state" else None

        def is_call_allowed(self, mode, tool, args):
            return True

    class _Client:
        calls = 0

        async def chat(self, messages, **kwargs):
            self.calls += 1
            # 5 итераций с прогрессом (update_work_state), потом — финал.
            if self.calls > 5:
                return ChatResponse(
                    message=AssistantMessage(content="готово", tool_calls=[]),
                    usage=Usage(input_tokens=10, output_tokens=5, cost=0.0),
                )
            return ChatResponse(
                message=AssistantMessage(
                    content="",
                    tool_calls=[
                        ToolCall(
                            id=f"c{self.calls}",
                            name="update_work_state",
                            arguments={"patch": {"findings+": ["x"]}},
                        )
                    ],
                ),
                usage=Usage(input_tokens=10, output_tokens=5, cost=0.0),
            )

    s = Session.new(workspace="/tmp", model="m", mode=Mode.ACT)
    s.messages = [{"role": "system", "content": "S"}]
    client = _Client()
    loop = AgentLoop(client=client, registry=_Registry(), session=s, events=_events())
    res = await loop.run("задача")
    # Прогресс был (work_state менялся) → no_progress False, задача завершена.
    assert not res.no_progress
    assert not res.limit_reached
    assert res.iterations_used == 6  # 5 с tool_call + 1 финальный текст
