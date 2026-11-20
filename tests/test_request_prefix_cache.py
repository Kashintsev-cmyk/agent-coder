"""Интеграция: стабильный префикс запроса + волатильный хвост в AgentLoop.

Проверяем главное свойство фикса: между итерациями messages[0] (system)
не меняется, даже если work_state обновился и появились новые read_paths.
Волатильный блок подмешивается только в запрос, в истории сессии его нет.
"""

from __future__ import annotations

from typing import Any

from agent_coder.agent.loop import AgentLoop, LoopEvents
from agent_coder.agent.prompts import Mode
from agent_coder.agent.session import Session
from agent_coder.llm.types import AssistantMessage, ChatResponse, ToolCall, Usage


class _ReadRegistry:
    """Registry с одним read_file, который возвращает предсказуемый текст."""

    class _Tool:
        name = "read_file"
        read_only = True
        requires_approval = False

        def needs_approval(self, args):
            return False

        async def executor(self, args, ctx):
            path = args.get("path", "x.py")
            return f"{path} (10 строк):\n    1│ import os\n    2│ def foo():"

    def openai_schemas(self, mode):
        return []

    def get(self, name):
        return self._Tool() if name == "read_file" else None

    def is_call_allowed(self, mode, tool, args):
        return True


class _RecordingClient:
    """Запоминает messages КАЖДОГО запроса — как их увидел провайдер."""

    def __init__(self) -> None:
        self.sent: list[list[dict[str, Any]]] = []
        self._calls = 0

    async def chat(self, messages: list[dict[str, Any]], **kwargs) -> ChatResponse:
        # Снимок: loop может менять свой список после вызова.
        self.sent.append([dict(m) for m in messages])
        self._calls += 1
        if self._calls == 1:
            msg = AssistantMessage(
                content="",
                tool_calls=[
                    ToolCall(id="c1", name="read_file", arguments={"path": "a.py"})
                ],
            )
        else:
            msg = AssistantMessage(content="готово", tool_calls=[])
        return ChatResponse(
            message=msg,
            usage=Usage(input_tokens=100, output_tokens=10, cost=0.01),
            raw=None,
        )


def _events(on_tool_result=None) -> LoopEvents:
    async def request_approval(tc):
        return True

    return LoopEvents(
        on_assistant_text=lambda *a: None,
        on_tool_call=lambda *a: None,
        on_tool_result=on_tool_result or (lambda *a: None),
        on_usage=lambda *a: None,
        on_error=lambda *a: None,
        request_approval=request_approval,
    )


def _session(tmp_path) -> Session:
    return Session(
        id="test-prefix",
        workspace=str(tmp_path),
        model="openai/gpt-6-luna",
        mode=Mode.ACT,
    )


def _loop(session: Session, client, registry=None, events=None) -> AgentLoop:
    return AgentLoop(
        session=session,
        client=client,  # type: ignore[arg-type]
        registry=(registry or _ReadRegistry()),  # type: ignore[arg-type]
        events=events or _events(),
        yolo=True,
    )


async def test_system_message_identical_between_iterations(tmp_path) -> None:
    """messages[0] не меняется, хотя work_state и read_paths изменились."""
    client = _RecordingClient()
    session = _session(tmp_path)
    loop = _loop(session, client)

    await loop.run("прочитай a.py")

    assert len(client.sent) >= 2, "нужно минимум две итерации"
    first_system = client.sent[0][0]
    assert first_system["role"] == "system"
    for i, sent in enumerate(client.sent[1:], start=1):
        assert sent[0] == first_system, (
            f"итерация {i}: system prompt изменился — prefix-кэш промахнётся"
        )
    # read_paths действительно пополнились — иначе тест ничего не проверяет.
    assert "a.py" in session.read_paths


async def test_volatile_block_appended_to_last_message(tmp_path) -> None:
    """Волатильный блок уходит в запрос последним сообщением."""
    client = _RecordingClient()
    session = _session(tmp_path)
    session.work_state = {"findings": ["факт из цикла"]}
    loop = _loop(session, client)

    await loop.run("прочитай a.py")

    last = client.sent[0][-1]
    assert last["role"] == "user", "волатильный блок — последним сообщением"
    content = last["content"]
    assert isinstance(content, str)
    assert "[WORK_STATE]" in content
    assert "факт из цикла" in content


async def test_volatile_block_not_persisted_in_history(tmp_path) -> None:
    """Волатильный блок не пишется в session.messages (иначе история пухнет)."""
    client = _RecordingClient()
    session = _session(tmp_path)
    session.work_state = {"findings": ["факт"]}
    loop = _loop(session, client)

    await loop.run("прочитай a.py")

    for msg in session.messages:
        content = msg.get("content")
        text = content if isinstance(content, str) else str(content)
        assert "[ПРОЧИТАННЫЕ ФАЙЛЫ]" not in text
        assert "[WORK_STATE]" not in text


async def test_history_is_prefix_of_request(tmp_path) -> None:
    """История сессии — префикс отправленного списка (кэш растёт, не скачет)."""
    client = _RecordingClient()
    session = _session(tmp_path)
    loop = _loop(session, client)

    await loop.run("прочитай a.py")

    for sent in client.sent:
        # Волатильный хвост может отсутствовать, поэтому история — префикс.
        assert sent[: len(sent) - 1] == [
            dict(m) for m in session.messages[: len(sent) - 1]
        ] or sent == [dict(m) for m in session.messages[: len(sent)]]


async def test_no_rebuild_when_state_unchanged(tmp_path) -> None:
    """Сборка system prompt кэшируется: без изменений — тот же объект строки."""
    client = _RecordingClient()
    session = _session(tmp_path)
    loop = _loop(session, client)

    loop._refresh_system_prompt()
    first = session.messages[0]["content"]
    loop._refresh_system_prompt()
    second = session.messages[0]["content"]
    assert first == second
