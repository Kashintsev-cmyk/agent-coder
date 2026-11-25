"""Интеграция work_state в AgentLoop: handle в ToolContext, refresh в цикле."""

from __future__ import annotations

import pytest

from agent_coder.agent import loop as loop_mod
from agent_coder.agent.loop import AgentLoop, LoopEvents
from agent_coder.agent.prompts import Mode
from agent_coder.agent.session import Session
from agent_coder.llm.types import AssistantMessage, ChatResponse, ToolCall, Usage


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


def test_refresh_system_prompt_excludes_work_state() -> None:
    """System стабилен: work_state в него не попадает, он в хвосте запроса."""
    s = _session()
    s.work_state = {"findings": ["src/x.py"], "todo": ["fix y"]}
    loop = _loop(s)
    loop._refresh_system_prompt()
    system = s.messages[0]
    assert system["role"] == "system"
    assert "[WORK_STATE]" not in system["content"]
    assert "src/x.py" not in system["content"]
    tail = loop._request_messages()[-1]
    assert tail["role"] == "user"
    assert "[WORK_STATE]" in tail["content"]
    assert "src/x.py" in tail["content"]
    assert "fix y" in tail["content"]


def test_refresh_system_prompt_empty_state_no_block() -> None:
    s = _session()
    loop = _loop(s)
    loop._refresh_system_prompt()
    assert "[WORK_STATE]" not in s.messages[0]["content"]
    assert loop._request_messages() == s.messages


def test_refresh_updates_existing_system_message() -> None:
    """Повторный refresh не дублирует system; волатильное меняется только в хвосте."""
    s = _session()
    loop = _loop(s)
    loop._refresh_system_prompt()
    n_before = len(s.messages)
    system_before = s.messages[0]["content"]
    s.work_state = {"findings": ["a"]}
    loop._refresh_system_prompt()
    assert len(s.messages) == n_before  # не добавилось второе system
    assert s.messages[0]["content"] == system_before  # стабильный префикс цел
    assert "[WORK_STATE]" in loop._request_messages()[-1]["content"]
    assert "[WORK_STATE]" not in s.messages[-1].get("content", "")


# --- B9.1: reminder + post-audit guard ---------------------------------


class _WsRegistry:
    """read_file + update_work_state для B9.1-тестов."""

    class _ReadTool:
        name = "read_file"
        read_only = True
        requires_approval = False

        def needs_approval(self, args):
            return False

        async def executor(self, args, ctx):
            path = args.get("path", "")
            if path == "missing.py":
                return "Ошибка: файл не найден"
            return f"content of {path}"

    class _WsTool:
        name = "update_work_state"
        read_only = True
        requires_approval = False

        def needs_approval(self, args):
            return False

        async def executor(self, args, ctx):
            return str(ctx.work_state.update(args.get("patch") or {}))

    def openai_schemas(self, mode):
        return []

    def get(self, name):
        if name == "read_file":
            return self._ReadTool()
        if name == "update_work_state":
            return self._WsTool()
        return None

    def is_call_allowed(self, mode, tool, args):
        return True


def _usage() -> Usage:
    return Usage(input_tokens=10, output_tokens=5, cost=0.0)


def _read(path: str) -> ChatResponse:
    return ChatResponse(
        message=AssistantMessage(
            content="",
            tool_calls=[
                ToolCall(id=f"r-{path}", name="read_file", arguments={"path": path})
            ],
        ),
        usage=_usage(),
    )


def _reads(*paths: str) -> ChatResponse:
    return ChatResponse(
        message=AssistantMessage(
            content="",
            tool_calls=[
                ToolCall(
                    id=f"r-{index}-{path}", name="read_file", arguments={"path": path}
                )
                for index, path in enumerate(paths)
            ],
        ),
        usage=_usage(),
    )


def _tool_calls(*calls: tuple[str, dict]) -> ChatResponse:
    return ChatResponse(
        message=AssistantMessage(
            content="",
            tool_calls=[
                ToolCall(id=f"tool-{index}", name=name, arguments=arguments)
                for index, (name, arguments) in enumerate(calls)
            ],
        ),
        usage=_usage(),
    )


def _update(patch: dict) -> ChatResponse:
    return ChatResponse(
        message=AssistantMessage(
            content="",
            tool_calls=[
                ToolCall(
                    id="ws-1", name="update_work_state", arguments={"patch": patch}
                )
            ],
        ),
        usage=_usage(),
    )


def _done(text: str = "готово") -> ChatResponse:
    return ChatResponse(
        message=AssistantMessage(content=text, tool_calls=[]),
        usage=_usage(),
    )


class _ScriptedClient:
    """Клиент по сценарию: список ChatResponse, затем fallback."""

    def __init__(
        self, responses: list[ChatResponse], fallback: ChatResponse | None = None
    ):
        self.responses = responses
        self.fallback = fallback or _done("готово (fallback)")
        self.calls = 0
        self.requests: list[list[dict]] = []

    async def chat(self, messages, **kwargs):
        self.calls += 1
        self.requests.append(list(messages))
        if self.calls <= len(self.responses):
            return self.responses[self.calls - 1]
        return self.fallback


def _audit_session() -> Session:
    s = Session.new(workspace="/tmp", model="m", mode=Mode.AUDIT)
    s.messages = [{"role": "system", "content": "S"}]
    return s


def _act_session() -> Session:
    s = Session.new(workspace="/tmp", model="m", mode=Mode.ACT)
    s.messages = [{"role": "system", "content": "S"}]
    return s


@pytest.mark.asyncio
async def test_audit_reminder_after_5_unique_files_across_tool_calls(
    monkeypatch,
) -> None:
    """AUDIT reminder is based on unique read_file paths, not model turns."""
    monkeypatch.setattr(loop_mod.config, "MAX_ITERATIONS_AUDIT", 50)
    monkeypatch.setattr(loop_mod.config, "MAX_ITERATIONS_NO_PROGRESS", 100)

    # Five unique files arrive in only two model turns; the duplicate f4.py
    # must not count twice. The reminder is injected before the third turn.
    responses = [
        _reads("f1.py", "f2.py", "f3.py"),
        _reads("f4.py", "f4.py", "f5.py"),
        _done(),
    ]
    client = _ScriptedClient(responses)

    s = _audit_session()
    loop = AgentLoop(client=client, registry=_WsRegistry(), session=s, events=_events())
    res = await loop.run("аудит")

    reminders = [
        m["content"]
        for m in s.messages
        if m.get("role") == "user" and "[НАПОМИНАНИЕ B9.1]" in (m.get("content") or "")
    ]
    assert len(reminders) == 1
    assert "5 новых уникальных файлов через read_file" in reminders[0]
    assert "итерац" not in reminders[0]
    # 2 turns with reads + done + post-audit guard
    assert client.calls == 4
    assert res.work_state_updates == 0
    assert res.iterations_used == 3


@pytest.mark.asyncio
async def test_audit_reminder_resets_after_work_state_update(monkeypatch) -> None:
    """New reminder threshold starts over; aliases and failed reads do not count."""
    monkeypatch.setattr(loop_mod.config, "MAX_ITERATIONS_AUDIT", 50)
    monkeypatch.setattr(loop_mod.config, "MAX_ITERATIONS_NO_PROGRESS", 100)

    responses = [
        _reads("a.py", "b.py", "c.py", "d.py", "e.py"),
        _update({"findings+": ["a.py:1 — проверено"]}),
        # 4 unique successful paths: g.py and ./g.py are aliases. The failed
        # read must not make this batch reach the five-file reminder threshold.
        _reads("g.py", "./g.py", "h.py", "i.py", "j.py", "missing.py"),
        _read("k.py"),
        _done(),
    ]
    client = _ScriptedClient(responses)

    s = _audit_session()
    loop = AgentLoop(client=client, registry=_WsRegistry(), session=s, events=_events())
    res = await loop.run("аудит")

    def reminder_count(messages: list[dict]) -> int:
        return sum(
            1
            for message in messages
            if message.get("role") == "user"
            and "[НАПОМИНАНИЕ B9.1]" in (message.get("content") or "")
        )

    # Initial five files trigger the first reminder; the state update resets
    # the count. Alias + failed read leave only four files, so k.py is the fifth.
    assert [reminder_count(request) for request in client.requests] == [0, 1, 1, 1, 2]
    reminders = [
        message
        for message in s.messages
        if message.get("role") == "user"
        and "[НАПОМИНАНИЕ B9.1]" in (message.get("content") or "")
    ]
    assert len(reminders) == 2
    assert all(
        "5 новых уникальных файлов через read_file" in m["content"] for m in reminders
    )
    assert s.work_state["findings"] == ["a.py:1 — проверено"]
    assert res.work_state_updates == 1
    assert res.iterations_used == 5
    assert client.calls == 5


@pytest.mark.asyncio
async def test_audit_reminder_waits_for_five_unique_files(monkeypatch) -> None:
    """Repeated reads do not trigger the AUDIT reminder a second time."""
    monkeypatch.setattr(loop_mod.config, "MAX_ITERATIONS_AUDIT", 50)
    monkeypatch.setattr(loop_mod.config, "MAX_ITERATIONS_NO_PROGRESS", 100)

    responses = [
        _reads("a.py", "b.py", "c.py", "d.py", "d.py"),
        _done(),
    ]
    client = _ScriptedClient(responses)

    s = _audit_session()
    loop = AgentLoop(client=client, registry=_WsRegistry(), session=s, events=_events())
    await loop.run("аудит")

    reminders = [
        m["content"]
        for m in s.messages
        if m.get("role") == "user" and "[НАПОМИНАНИЕ B9.1]" in (m.get("content") or "")
    ]
    assert reminders == []


@pytest.mark.asyncio
async def test_audit_reminder_not_injected_in_act(monkeypatch) -> None:
    """B9.1: в ACT напоминания нет — модель активнее пишет."""
    monkeypatch.setattr(loop_mod.config, "MAX_ITERATIONS_ACT", 50)
    monkeypatch.setattr(loop_mod.config, "MAX_ITERATIONS_NO_PROGRESS", 100)

    responses = [_read(f"f{i}.py") for i in range(1, 8)] + [_done()]
    client = _ScriptedClient(responses)

    s = _act_session()
    loop = AgentLoop(client=client, registry=_WsRegistry(), session=s, events=_events())
    await loop.run("работа")

    reminder_msgs = [
        m
        for m in s.messages
        if m.get("role") == "user" and "[НАПОМИНАНИЕ B9.1]" in (m.get("content") or "")
    ]
    assert reminder_msgs == [], "в ACT напоминание не должно инжектиться"
    # 7 read + 1 done = 8; guard не для ACT
    assert client.calls == 8


@pytest.mark.asyncio
async def test_post_audit_guard_forces_findings(monkeypatch) -> None:
    """B9.1: AUDIT без findings → guard добавляет итерацию, findings появляются."""
    monkeypatch.setattr(loop_mod.config, "MAX_ITERATIONS_AUDIT", 50)
    monkeypatch.setattr(loop_mod.config, "MAX_ITERATIONS_NO_PROGRESS", 100)

    responses = [
        _read("a.py"),
        _read("b.py"),
        _done(),  # модель говорит «готово» без findings
        _update({"findings+": ["a.py:10 — баг"]}),  # guard → модель пишет
    ]
    client = _ScriptedClient(responses, fallback=_done("готово (guard-fallback)"))

    s = _audit_session()
    loop = AgentLoop(client=client, registry=_WsRegistry(), session=s, events=_events())
    res = await loop.run("аудит")

    assert client.calls == 4, "guard должен сделать ровно один дополнительный chat"
    assert s.work_state["findings"] == ["a.py:10 — баг"]
    assert res.work_state_updates == 1
    assert res.iterations_used == 3  # 2 read + done, guard не инкрементит iteration


@pytest.mark.asyncio
async def test_post_audit_guard_skips_when_findings_present(monkeypatch) -> None:
    """B9.1: findings уже записаны — guard не срабатывает."""
    monkeypatch.setattr(loop_mod.config, "MAX_ITERATIONS_AUDIT", 50)
    monkeypatch.setattr(loop_mod.config, "MAX_ITERATIONS_NO_PROGRESS", 100)

    responses = [
        _update({"findings+": ["x.py:1 — проблема"]}),
        _read("a.py"),
        _read("b.py"),
        _done(),
    ]
    client = _ScriptedClient(responses)

    s = _audit_session()
    loop = AgentLoop(client=client, registry=_WsRegistry(), session=s, events=_events())
    res = await loop.run("аудит")

    assert client.calls == 4, "guard не должен добавить chat"
    assert res.work_state_updates == 1
    assert s.work_state["findings"] == ["x.py:1 — проблема"]


@pytest.mark.asyncio
async def test_post_audit_guard_skips_when_open_questions_present(monkeypatch) -> None:
    """Пункт 2: модель уже записала итог в open_questions — guard не добавляет chat."""
    monkeypatch.setattr(loop_mod.config, "MAX_ITERATIONS_AUDIT", 50)
    monkeypatch.setattr(loop_mod.config, "MAX_ITERATIONS_NO_PROGRESS", 100)

    responses = [
        _read("a.py"),
        _read("b.py"),
        _update({"open_questions+": ["проблем не найдено"]}),
        _done(),
    ]
    client = _ScriptedClient(responses)

    s = _audit_session()
    loop = AgentLoop(client=client, registry=_WsRegistry(), session=s, events=_events())
    await loop.run("аудит")

    assert client.calls == 4, (
        "guard не должен добавить chat при непустом open_questions"
    )
    assert s.work_state.get("findings") in (None, [])


@pytest.mark.asyncio
async def test_work_state_updates_counter_in_act(monkeypatch) -> None:
    """B9.1: LoopResult.work_state_updates считает итерации с изменением state."""
    monkeypatch.setattr(loop_mod.config, "MAX_ITERATIONS_ACT", 50)
    monkeypatch.setattr(loop_mod.config, "MAX_ITERATIONS_NO_PROGRESS", 100)

    responses = [
        _update({"findings+": ["f1"]}),
        _update({"findings+": ["f2"]}),
        _done(),
    ]
    client = _ScriptedClient(responses)

    s = _act_session()
    loop = AgentLoop(client=client, registry=_WsRegistry(), session=s, events=_events())
    res = await loop.run("работа")

    assert res.work_state_updates == 2
    assert res.iterations_used == 3
    assert s.work_state["findings"] == ["f1", "f2"]


# --- B9.1e: PLAN reminder --------------------------------------------


@pytest.mark.asyncio
async def test_plan_does_not_inject_reminder_after_seven_reads(monkeypatch) -> None:
    """PLAN research never gets the periodic AUDIT reminder."""
    monkeypatch.setattr(loop_mod.config, "MAX_ITERATIONS_PLAN", 50)
    monkeypatch.setattr(loop_mod.config, "MAX_ITERATIONS_NO_PROGRESS", 100)

    responses = [_read(f"f{i}.py") for i in range(1, 8)] + [_done()]
    client = _ScriptedClient(responses)

    s = Session.new(workspace="/tmp", model="m", mode=Mode.PLAN)
    s.messages = [{"role": "system", "content": "S"}]
    loop = AgentLoop(client=client, registry=_WsRegistry(), session=s, events=_events())
    res = await loop.run("план")

    reminder_msgs = [
        m
        for m in s.messages
        if m.get("role") == "user" and "[НАПОМИНАНИЕ B9.1]" in (m.get("content") or "")
    ]
    assert reminder_msgs == []
    assert res.work_state_updates == 0


@pytest.mark.asyncio
async def test_plan_guard_not_triggered(monkeypatch) -> None:
    """B9.1e: guard в PLAN не срабатывает (только AUDIT)."""
    monkeypatch.setattr(loop_mod.config, "MAX_ITERATIONS_PLAN", 50)
    monkeypatch.setattr(loop_mod.config, "MAX_ITERATIONS_NO_PROGRESS", 100)

    responses = [
        _read("a.py"),
        _read("b.py"),
        _done(),
    ]
    client = _ScriptedClient(responses, fallback=_update({"todo+": ["bug"]}))

    s = Session.new(workspace="/tmp", model="m", mode=Mode.PLAN)
    s.messages = [{"role": "system", "content": "S"}]
    loop = AgentLoop(client=client, registry=_WsRegistry(), session=s, events=_events())
    res = await loop.run("план")

    # 2 read + 1 done = 3; guard не для PLAN → 4-го вызова нет
    assert client.calls == 3
    assert res.work_state_updates == 0
