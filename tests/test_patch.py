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

    def needs_approval(self, args) -> bool:
        # M4 (8f0cd848): patch-цикл теперь зовёт needs_approval — fake обязан.
        return False

    async def executor(self, args, ctx):
        if self.behavior == "error":
            return "Ошибка: simulated"
        return f"ok: {self.name}"


class _FakeRegistry:
    def __init__(self, tools: dict[str, _FakeTool] | None = None):
        self._tools = tools or {}

    def get(self, name):
        return self._tools.get(name)

    def is_call_allowed(self, mode, tool, args):
        # M4 (8f0cd848): patch-цикл зовёт reg.is_call_allowed — fake
        # должен повторять контракт ToolRegistry.
        return True


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
    # M4 (8f0cd848): run_command убран из patch (заменён на run_checks,
    # pytest через --noconftest + env-clean, см. defac89).
    assert "run_checks" in PATCH_TOOLS
    assert "run_command" not in PATCH_TOOLS


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


def test_build_task_includes_previous_runs() -> None:
    """_build_task показывает files_changed/error прошлых прогонов (B9.2g.3)."""
    s = _mk_session()
    s.patch_results = [
        {
            "run_id": "r1",
            "files_changed": ["bot/x.py"],
            "error": "",
            "findings": [{"text": "a", "evidence": "a.py:1"}],
        },
        {
            "run_id": "r2",
            "files_changed": [],
            "error": "бюджет",
            "findings": [],
        },
    ]
    task = patch_mod._build_task([{"text": "A", "evidence": "a.py:1"}], s)
    assert "ПРОШЛЫЕ ПРОГОНЫ" in task
    assert "bot/x.py" in task
    assert "Прогон 1" in task
    assert "Прогон 2" in task
    assert "НЕ ПОВТОРЯЙ" in task
    assert "бюджет" in task


async def test_run_patch_rejects_tests_write() -> None:
    """write_file на tests/ — reject, path не в files_changed (B9.2g.1)."""
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
                        arguments={"path": "tests/test_x.py", "content": "x"},
                    )
                ],
            ),
            _FakeResponse("готово"),
        ]
    )
    res = await run_patch(client, s, registry=_registry_with_all())

    assert res.files_changed == [], "tests/ не должен попасть в files_changed"
    tool_msgs = [m for m in client.last_messages if m.get("role") == "tool"]
    assert any("tests/ не патчим" in m["content"] for m in tool_msgs), (
        "ожидали reject-сообщение в tool-message"
    )


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


async def test_run_patch_excludes_prev_run_findings() -> None:
    """B9.2e: findings из прошлых patch_results не берутся повторно."""
    s = _mk_session()
    s.verify_results = [
        _confirmed("flaw A", "a.py:10"),
        _confirmed("flaw B", "b.py:20"),
    ]
    # Прошлый прогон уже обработал A
    s.patch_results = [
        {
            "run_id": "old",
            "findings": [{"text": "flaw A", "evidence": "a.py:10"}],
        }
    ]
    client = _ScriptedClient([_FakeResponse("готово")])
    res = await run_patch(client, s, registry=_registry_with_all())

    user_msg = client.last_messages[1]["content"]
    assert "flaw B" in user_msg
    assert "flaw A" not in user_msg
    assert res.ok


async def test_run_patch_all_already_patched() -> None:
    """B9.2e: все findings уже патчились → no_confirmed, LLM не зовётся."""
    s = _mk_session()
    s.verify_results = [_confirmed("flaw A", "a.py:10")]
    s.patch_results = [
        {"run_id": "old", "findings": [{"text": "flaw A", "evidence": "a.py:10"}]}
    ]
    client = _ScriptedClient([_FakeResponse("should not be called")])
    res = await run_patch(client, s, registry=_registry_with_all())
    assert res.no_confirmed
    assert "все confirmed уже были" in res.error
    assert client.calls == 0


def test_is_tests_path_resolve_bypass(tmp_path) -> None:
    """B9.2g.1-fix: `src/../tests/x.py` и абсолютный путь — обход закрыт."""
    from agent_coder.agent.patch import _is_tests_path

    ws = tmp_path / "ws"
    ws.mkdir()
    (ws / "tests").mkdir()
    (ws / "src").mkdir()
    assert _is_tests_path("tests/x.py", str(ws)) is True
    assert _is_tests_path("src/../tests/x.py", str(ws)) is True
    assert _is_tests_path(str(ws / "tests" / "x.py"), str(ws)) is True
    assert _is_tests_path("src/x.py", str(ws)) is False
    assert _is_tests_path(str(ws / "src" / "x.py"), str(ws)) is False
    # вне workspace — не tests (write_file отвергнет отдельно)
    assert _is_tests_path("/tmp/tests/x.py", str(ws)) is False


async def test_run_patch_error_not_in_files_changed() -> None:
    """B9.2g.3: write с ошибкой не попадает в files_changed."""
    from agent_coder.tools import build_registry
    from agent_coder.tools.registry import Tool

    async def _err_executor(args, ctx):
        return "Ошибка: path outside workspace"

    reg = build_registry()
    orig = reg.get("write_file")
    assert orig is not None, "write_file должен быть в build_registry"
    reg._tools["write_file"] = Tool(
        name=orig.name,
        description=orig.description,
        parameters=orig.parameters,
        executor=_err_executor,
        requires_approval=False,
        read_only=False,
    )

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
    res = await run_patch(client, s, registry=reg)

    assert res.files_changed == [], "error-write не должен попасть в files_changed"
    tool_msgs = [m for m in client.last_messages if m.get("role") == "tool"]
    assert any("Ошибка" in m["content"] for m in tool_msgs), (
        "ожидали error-сообщение в tool-message"
    )
