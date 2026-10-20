"""B9.2c: force_final в verify — tools=[] на последних итерациях."""

from __future__ import annotations

from agent_coder.agent import verify as verify_mod
from agent_coder.agent.prompts import Mode
from agent_coder.agent.session import Session
from agent_coder.agent.verify import run_verify
from agent_coder.llm.types import ToolCall, Usage


class _Msg:
    def __init__(self, content, tool_calls=None):
        self.content = content
        self.tool_calls = tool_calls or []


class _Resp:
    def __init__(self, content, tool_calls=None, cost=0.01):
        self.message = _Msg(content, tool_calls)
        self.usage = Usage(input_tokens=100, output_tokens=50, cost=cost)


class _Client:
    """Первые N — tool_calls read_file, потом текст-вердикт."""

    def __init__(self, tool_iters: int, verdict_text: str):
        self.tool_iters = tool_iters
        self.verdict_text = verdict_text
        self.calls = 0
        self.tools_passed: list[list] = []

    async def chat(self, messages, tools=None, **kwargs):
        self.calls += 1
        self.tools_passed.append(tools if tools is not None else [])
        if self.calls <= self.tool_iters and tools:
            return _Resp(
                "",
                tool_calls=[
                    ToolCall(
                        id=f"c{self.calls}",
                        name="read_file",
                        arguments={"path": "x.py"},
                    )
                ],
            )
        return _Resp(self.verdict_text)


class _Tool:
    name = "read_file"
    read_only = True

    def to_openai_schema(self):
        return {"type": "function", "function": {"name": "read_file"}}

    async def executor(self, args, ctx):
        return "x.py:1 def f()"


class _Reg:
    def get(self, name):
        return _Tool() if name == "read_file" else None


def _mk_session():
    s = Session.new(workspace="/tmp", model="m", mode=Mode.AUDIT)
    s.work_state = {"findings": ["a.py:1 — bug", "b.py:2 — bug2"]}
    return s


async def test_force_final_disables_tools(monkeypatch):
    """На последней итерации tools=[] — модель вынуждена ответить.

    Клиент возвращает tool_calls, пока tools непуст. На первой
    force_final-итерации (tools=[]) получает текстовый ответ и
    завершает цикл. Значит: первая итерация — с tools, последняя —
    с tools=[], вердикт распарсен.
    """
    monkeypatch.setattr(verify_mod, "MAX_VERIFY_ITERATIONS", 5)
    monkeypatch.setattr(verify_mod, "FINAL_FORCE_ITERATIONS", 2)
    s = _mk_session()
    client = _Client(tool_iters=10, verdict_text="CONFIRMED: a.py:1 — bug | a.py:1")
    res = await run_verify(client, s, registry=_Reg())

    # Первая итерация — tools непустые (обычный цикл).
    assert client.tools_passed[0] != []
    # Последняя итерация — tools=[] (force_final).
    assert client.tools_passed[-1] == []
    # Останавливается на первой force_final (4-я), не уходит на 5-ю.
    assert client.calls == 4
    # Вердикт распарсен.
    assert res.counts.get("confirmed", 0) >= 1


async def test_total_usage_updated(monkeypatch):
    """verify обновляет session.total_usage, не только res.cost."""
    monkeypatch.setattr(verify_mod, "MAX_VERIFY_ITERATIONS", 3)
    monkeypatch.setattr(verify_mod, "FINAL_FORCE_ITERATIONS", 1)
    s = _mk_session()
    before = s.total_usage.cost
    client = _Client(tool_iters=1, verdict_text="CONFIRMED: a | a.py:1")
    res = await run_verify(client, s, registry=_Reg())
    assert s.total_usage.cost > before
    assert s.total_usage.cost >= res.cost
