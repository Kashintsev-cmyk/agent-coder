"""Тесты agent_coder.agent.patch (B9.2: /patch по confirmed)."""

from __future__ import annotations

from agent_coder.agent import patch as patch_mod
from agent_coder.agent.patch import (
    PATCH_TOOLS,
    _collect_confirmed,
    run_patch,
)
from agent_coder.agent.prompts import Mode
from agent_coder.agent.session import Session
from agent_coder.llm.types import ToolCall, Usage


class _FakeMessage:
    def __init__(self, content: str, tool_calls: list[ToolCall] | None = None):
        self.content = content
        self.tool_calls = tool_calls or []


class _FakeResponse:
    def __init__(
        self, content: str, tool_calls: list[ToolCall] | None = None, cost: float = 0.01
    ):
        self.message = _FakeMessage(content, tool_calls)
        self.usage = Usage(input_tokens=100, output_tokens=50, cost=cost)


class _ScriptedClient:
    """Возвращает ответы по очереди, считает calls."""

    def __init__(self, responses: list[_FakeResponse]):
        self.responses = responses
        self.calls = 0
        self.last_messages: list[dict] = []

    async def chat(self, messages, **kwargs):
        self.calls += 1
        self.last_messages = messages
        idx = min(self.calls - 1, len(self.responses) - 1)
        return self.responses[idx]


class _FakeTool:
    def __init__(self, name: str, read_only: bool = True, behavior: str = "ok"):
        self.name = name
        self.read_only = read_only
        self.behavior = behavior

    def to_openai_schema(self) -> dict:
        return {"type": "function", "function": {"name": self.name}}

    async def executor(self, args, ctx):
        if self.behavior == "error":
            return "Ошибка: simulated"
        return f"ok: {self.name}"


class _FakeRegistry:
    def __init__(self, tools: dict[str, _FakeTool] | None = None):
        self._tools = tools or {}

    def get(self, name):
        return self._tools.get(name)


def _mk_session() -> Session:
    return Session.new(workspace="/tmp", model="m", mode=Mode.ACT)


def _confirmed(text: str, evidence: str = "a.py:1") -> dict:
    return {"status": "confirmed", "text": text, "evidence": evidence, "run_id": "r1"}


def _registry_with_all() -> _FakeRegistry:
    return _FakeRegistry({name: _FakeTool(name) for name in PATCH_TOOLS})


def test_collect_confirmed_filters_and_dedups() -> None:
    """Только confirmed; дубли по (text, evidence) схлопываются."""
    results = [
        _confirmed("A", "a.py:1"),
        _confirmed("A", "a.py:1"),  # дубль
        {"status": "rejected", "text": "B", "evidence": "b.py:2"},
        {"status": "uncertain", "text": "C", "evidence": "c.py:3"},
        _confirmed("D", "d.py:4"),
    ]
    out = _collect_confirmed(results)
    assert [r["text"] for r in out] == ["A", "D"]


def test_patch_tools_no_git_commit() -> None:
    """git_commit НЕ в whitelist — коммит делает пользователь."""
    assert "git_commit" not in PATCH_TOOLS
    assert "write_file" in PATCH_TOOLS
    assert "edit_file" in PATCH_TOOLS
    assert "run_command" in PATCH_TOOLS


async def test_run_patch_no_confirmed() -> None:
    """Нет confirmed → no_confirmed=True, LLM не вызывается."""
    s = _mk_session()
    s.verify_results = [{"status": "rejected", "text": "X", "evidence": "x.py:1"}]
    client = _ScriptedClient([_FakeResponse("should not be called")])
    res = await run_patch(client, s, registry=_registry_with_all())
    assert res.no_confirmed
    assert "нет confirmed" in res.error
    assert client.calls == 0


async def test_run_patch_task_contains_only_confirmed() -> None:
    """В user-message — только confirmed, без rejected/uncertain."""
    s = _mk_session()
    s.verify_results = [
        _confirmed("flaw A", "a.py:10"),
        {"status": "rejected", "text": "false B", "evidence": "b.py:20"},
        _confirmed("flaw C", "c.py:30"),
    ]
    client = _ScriptedClient([_FakeResponse("готово")])
    res = await run_patch(client, s, registry=_registry_with_all())

    user_msg = client.last_messages[1]["content"]
    assert "flaw A" in user_msg
    assert "flaw C" in user_msg
    assert "false B" not in user_msg
    assert res.ok


async def test_run_patch_records_files_changed() -> None:
    """write_file/edit_file добавляют путь в files_changed."""
    s = _mk_session()
    s.verify_results = [_confirmed("A", "a.py:1")]
    client = _ScriptedClient(
        [
            _FakeResponse(
                "",
                tool_calls=[
                    ToolCall(
                        id="c1",
                        name="write_file",
                        arguments={"path": "a.py", "content": "x"},
                    )
                ],
            ),
            _FakeResponse("готово"),
        ]
    )
    res = await run_patch(client, s, registry=_registry_with_all())
    assert "a.py" in res.files_changed
    assert len(s.patch_results) == 1
    assert s.patch_results[0]["run_id"] == res.run_id


async def test_run_patch_budget_exceeded(monkeypatch) -> None:
    """При превышении MAX_COST_PATCH — budget_exceeded=True, break."""
    monkeypatch.setattr(patch_mod.config, "MAX_COST_PATCH", 0.05)
    s = _mk_session()
    s.verify_results = [_confirmed("A", "a.py:1")]
    # 3 итерации по 0.05₽ — упрёмся на второй
    client = _ScriptedClient(
        [
            _FakeResponse(
                "",
                tool_calls=[
                    ToolCall(id="c1", name="read_file", arguments={"path": "x"})
                ],
                cost=0.05,
            ),
            _FakeResponse(
                "",
                tool_calls=[
                    ToolCall(id="c2", name="read_file", arguments={"path": "y"})
                ],
                cost=0.05,
            ),
            _FakeResponse("готово", cost=0.05),
        ]
    )
    res = await run_patch(client, s, registry=_registry_with_all())
    assert res.budget_exceeded
    assert res.iterations <= 3


async def test_run_patch_max_iterations(monkeypatch) -> None:
    """Зацикливание на tool_calls → break на MAX_PATCH_ITERATIONS."""
    monkeypatch.setattr(patch_mod.config, "MAX_PATCH_ITERATIONS", 2)
    s = _mk_session()
    s.verify_results = [_confirmed("A", "a.py:1")]
    loop_response = _FakeResponse(
        "",
        tool_calls=[ToolCall(id="cX", name="read_file", arguments={"path": "x"})],
    )
    client = _ScriptedClient([loop_response] * 10)
    res = await run_patch(client, s, registry=_registry_with_all())
    assert res.iterations == 2
    assert client.calls == 2
