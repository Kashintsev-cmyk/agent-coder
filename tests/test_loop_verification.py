"""ACT must verify changed files before closing a task."""

from __future__ import annotations

from pathlib import Path

import pytest

from agent_coder.agent.loop import (
    AgentLoop,
    LoopEvents,
    _is_verification_call,
)
from agent_coder.agent.prompts import Mode
from agent_coder.agent.session import Session
from agent_coder.llm.types import AssistantMessage, ChatResponse, ToolCall, Usage
from agent_coder.tools.registry import ToolExecutionResult, ToolStatus


class _Client:
    def __init__(self, calls: list[tuple[str, dict] | None]) -> None:
        self.calls = calls
        self.index = 0

    async def chat(self, messages, **kwargs):
        call = self.calls[self.index]
        self.index += 1
        tool_calls = (
            [ToolCall(id=f"call-{self.index}", name=call[0], arguments=call[1])]
            if call
            else []
        )
        return ChatResponse(
            message=AssistantMessage(content="готово", tool_calls=tool_calls),
            usage=Usage(input_tokens=10, output_tokens=5, cost=0.0),
        )


class _Registry:
    def __init__(
        self,
        check_status: ToolStatus = ToolStatus.SUCCESS_NO_CHANGE,
        command_status: ToolStatus = ToolStatus.SUCCESS_NO_CHANGE,
        command_exit_code: int = 0,
        command_mutates: bool = False,
    ) -> None:
        self.check_status = check_status
        self.command_status = command_status
        self.command_exit_code = command_exit_code
        self.command_mutates = command_mutates

    def openai_schemas(self, mode):
        return []

    def get(self, name):
        if name not in {"edit_file", "run_checks", "run_command"}:
            return None
        registry = self

        class _Tool:
            read_only = name == "run_checks"
            requires_approval = False

            def needs_approval(self, args):
                return False

            async def executor(self, args, ctx):
                if name == "edit_file":
                    return ToolExecutionResult(
                        "updated",
                        ToolStatus.SUCCESS_CHANGED,
                        changed_paths=("src/example.py",),
                    )
                if name == "run_checks":
                    return ToolExecutionResult(
                        "checks done",
                        registry.check_status,
                        exit_code=(
                            None
                            if registry.check_status == ToolStatus.SUCCESS_NO_CHANGE
                            else 1
                        ),
                    )
                if registry.command_mutates:
                    path = Path(ctx.workspace) / "src" / "shell_changed.py"
                    path.parent.mkdir(parents=True, exist_ok=True)
                    path.write_text("changed", encoding="utf-8")
                return ToolExecutionResult(
                    "command done",
                    registry.command_status,
                    exit_code=registry.command_exit_code,
                )

        return _Tool()

    def is_call_allowed(self, mode, tool, args):
        return True


def _events() -> LoopEvents:
    async def request_approval(tc):
        return True

    return LoopEvents(
        on_assistant_text=lambda *args: None,
        on_tool_call=lambda *args: None,
        on_tool_result=lambda *args: None,
        on_usage=lambda *args: None,
        on_error=lambda *args: None,
        request_approval=request_approval,
    )


def _session(workspace: str = "/tmp") -> Session:
    session = Session.new(workspace=workspace, model="test", mode=Mode.ACT)
    session.messages = [{"role": "system", "content": "system"}]
    return session


@pytest.mark.asyncio
async def test_changed_files_need_successful_verification() -> None:
    session = _session()
    client = _Client(
        [
            ("edit_file", {}),
            None,  # rejected as final until verification is run
            ("run_checks", {}),
            None,
        ]
    )
    loop = AgentLoop(
        client=client,
        registry=_Registry(),
        session=session,
        events=_events(),
    )

    result = await loop.run("исправь код")

    assert not result.no_progress
    assert session.task_state == {}
    assert client.index == 4
    assert any(
        "src/example.py" in str(message.get("content", ""))
        and "ПРОВЕРКА ИЗМЕНЕНИЙ" in str(message.get("content", ""))
        for message in session.messages
    )


@pytest.mark.asyncio
async def test_failed_check_does_not_close_changed_task() -> None:
    session = _session()
    client = _Client(
        [
            ("edit_file", {}),
            ("run_checks", {}),
            None,
            None,
        ]
    )
    loop = AgentLoop(
        client=client,
        registry=_Registry(check_status=ToolStatus.ERROR),
        session=session,
        events=_events(),
    )

    result = await loop.run("исправь код")

    assert result.no_progress
    assert "src/example.py" in result.error_message
    assert session.task_state["changed_paths"] == ["src/example.py"]
    assert session.task_state["verification_passed"] is False


@pytest.mark.asyncio
async def test_task_without_file_changes_needs_no_verification() -> None:
    session = _session()
    client = _Client([None])
    loop = AgentLoop(
        client=client,
        registry=_Registry(),
        session=session,
        events=_events(),
    )

    result = await loop.run("объясни этот код")

    assert not result.no_progress
    assert session.task_state == {}
    assert client.index == 1


@pytest.mark.parametrize(
    ("command", "expected"),
    [("echo pytest", False), ("pytest -q", True)],
)
def test_verification_command_classification(command: str, expected: bool) -> None:
    call = ToolCall(id="check", name="run_command", arguments={"command": command})

    assert _is_verification_call(call, "запусти pytest -q") is expected


def test_explicit_criterion_requires_exact_command() -> None:
    request = "Критерий проверки: `custom-check --strict`"
    exact = ToolCall(
        id="check", name="run_command", arguments={"command": "custom-check --strict"}
    )
    wrapper = ToolCall(
        id="wrapper",
        name="run_command",
        arguments={"command": "echo custom-check --strict"},
    )

    assert _is_verification_call(exact, request)
    assert not _is_verification_call(wrapper, request)


@pytest.mark.asyncio
async def test_nonzero_exit_code_does_not_verify_changed_files(tmp_path) -> None:
    session = _session(str(tmp_path))
    client = _Client(
        [
            ("edit_file", {}),
            ("run_command", {"command": "pytest -q"}),
            None,
            None,
        ]
    )
    loop = AgentLoop(
        client=client,
        registry=_Registry(command_exit_code=1),
        session=session,
        events=_events(),
    )

    result = await loop.run("исправь код")

    assert result.no_progress
    assert session.task_state["verification_passed"] is False


@pytest.mark.asyncio
async def test_explicit_criterion_can_verify_when_exact_command_succeeds(
    tmp_path,
) -> None:
    session = _session(str(tmp_path))
    client = _Client(
        [
            ("edit_file", {}),
            ("run_command", {"command": "custom-check --strict"}),
            None,
        ]
    )
    loop = AgentLoop(
        client=client,
        registry=_Registry(),
        session=session,
        events=_events(),
    )

    result = await loop.run("исправь код. Критерий проверки: `custom-check --strict`")

    assert not result.no_progress
    assert session.task_state == {}


@pytest.mark.asyncio
async def test_explicit_criterion_with_nonzero_exit_code_does_not_verify() -> None:
    session = _session()
    client = _Client(
        [
            ("edit_file", {}),
            ("run_command", {"command": "custom-check --strict"}),
            None,
            None,
        ]
    )
    loop = AgentLoop(
        client=client,
        registry=_Registry(command_exit_code=1),
        session=session,
        events=_events(),
    )

    result = await loop.run("исправь код. Критерий проверки: `custom-check --strict`")

    assert result.no_progress
    assert session.task_state["verification_passed"] is False


@pytest.mark.asyncio
async def test_run_command_workspace_mutation_requires_verification(tmp_path) -> None:
    session = _session(str(tmp_path))
    client = _Client(
        [
            ("run_command", {"command": "custom-mutate"}),
            None,
            None,
        ]
    )
    loop = AgentLoop(
        client=client,
        registry=_Registry(
            command_status=ToolStatus.SUCCESS_EFFECT_UNKNOWN,
            command_mutates=True,
        ),
        session=session,
        events=_events(),
    )

    result = await loop.run("выполни команду")

    assert result.no_progress
    assert session.task_state["changed_paths"] == ["src/shell_changed.py"]
    assert session.task_state["possible_mutation"] is False
    assert any(
        "ПРОВЕРКА ИЗМЕНЕНИЙ" in str(message.get("content", ""))
        for message in session.messages
    )


@pytest.mark.asyncio
async def test_run_command_without_workspace_mutation_needs_no_verification(
    tmp_path,
) -> None:
    session = _session(str(tmp_path))
    client = _Client([("run_command", {"command": "custom-noop"}), None])
    loop = AgentLoop(
        client=client,
        registry=_Registry(command_status=ToolStatus.SUCCESS_EFFECT_UNKNOWN),
        session=session,
        events=_events(),
    )

    result = await loop.run("выполни команду")

    assert not result.no_progress
    assert session.task_state == {}
    assert not any(
        "ПРОВЕРКА ИЗМЕНЕНИЙ" in str(message.get("content", ""))
        for message in session.messages
    )
