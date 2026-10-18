"""Интеграция work_state в AgentLoop: handle в ToolContext, refresh в цикле."""

from __future__ import annotations

from agent_coder.agent.loop import AgentLoop, LoopEvents
from agent_coder.agent.prompts import Mode
from agent_coder.agent.session import Session


class _EmptyRegistry:
    def openai_schemas(self, mode):
        return []

    def get(self, name):
        return None


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


def _session() -> Session:
    return Session(id="test-ws", workspace="/tmp", model="m", mode=Mode.ACT)


def _loop(session: Session) -> AgentLoop:
    return AgentLoop(
        session=session,
        client=None,  # type: ignore[arg-type]
        registry=_EmptyRegistry(),  # type: ignore[arg-type]
        events=_events(),
    )


def test_work_state_handle_shares_live_dict() -> None:
    """handle видит изменения, сделанные через session.work_state напрямую."""
    s = _session()
    loop = _loop(s)
    h = loop._work_state_handle()
    s.work_state["findings"] = ["a"]
    assert h.get()["findings"] == ["a"]


def test_refresh_system_prompt_includes_work_state() -> None:
    s = _session()
    s.work_state = {"findings": ["src/x.py"], "todo": ["fix y"]}
    loop = _loop(s)
    loop._refresh_system_prompt()
    system = s.messages[0]
    assert system["role"] == "system"
    assert "[WORK_STATE]" in system["content"]
    assert "src/x.py" in system["content"]
    assert "fix y" in system["content"]


def test_refresh_system_prompt_empty_state_no_block() -> None:
    s = _session()
    loop = _loop(s)
    loop._refresh_system_prompt()
    assert "[WORK_STATE]" not in s.messages[0]["content"]


def test_refresh_updates_existing_system_message() -> None:
    """Повторный refresh (после update_work_state) обновляет, не дублирует."""
    s = _session()
    loop = _loop(s)
    loop._refresh_system_prompt()
    n_before = len(s.messages)
    s.work_state = {"findings": ["a"]}
    loop._refresh_system_prompt()
    assert len(s.messages) == n_before  # не добавилось второе system
    assert "[WORK_STATE]" in s.messages[0]["content"]
