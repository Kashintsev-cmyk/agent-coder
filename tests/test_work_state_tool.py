"""Тесты tool update_work_state (tools/work_state.py)."""

from __future__ import annotations

import pytest

from agent_coder.agent.prompts import Mode
from agent_coder.agent.work_state import WorkStateHandle
from agent_coder.tools.registry import ToolContext
from agent_coder.tools.work_state import _execute


def _ctx(handle: WorkStateHandle | None = None) -> ToolContext:
    return ToolContext(
        workspace="/tmp",
        mode=Mode.ACT,
        work_state=handle,
    )


@pytest.mark.asyncio
async def test_no_handle_returns_error() -> None:
    out = await _execute({"patch": {"findings": ["a"]}}, _ctx(None))
    assert "Ошибка" in out
    assert "work_state недоступен" in out


@pytest.mark.asyncio
async def test_empty_patch_rejected() -> None:
    out = await _execute({"patch": {}}, _ctx(WorkStateHandle({})))
    assert "Ошибка" in out
    assert "непустым" in out


@pytest.mark.asyncio
async def test_missing_patch_rejected() -> None:
    out = await _execute({}, _ctx(WorkStateHandle({})))
    assert "Ошибка" in out


@pytest.mark.asyncio
async def test_add_findings() -> None:
    h = WorkStateHandle({})
    out = await _execute({"patch": {"findings+": ["a", "b"]}}, _ctx(h))
    assert "findings: 0->2 (+2)" in out
    assert h.get()["findings"] == ["a", "b"]


@pytest.mark.asyncio
async def test_set_todo() -> None:
    h = WorkStateHandle({"todo": ["old"]})
    out = await _execute({"patch": {"todo": ["new1", "new2"]}}, _ctx(h))
    assert "todo: 1->2" in out
    assert h.get()["todo"] == ["new1", "new2"]


@pytest.mark.asyncio
async def test_remove_done() -> None:
    h = WorkStateHandle({"done": ["a", "b", "c"]})
    out = await _execute({"patch": {"done-": ["b"]}}, _ctx(h))
    assert "done: 3->2 (-1)" in out
    assert h.get()["done"] == ["a", "c"]


@pytest.mark.asyncio
async def test_no_change_reports_unchanged() -> None:
    h = WorkStateHandle({"todo": ["a"]})
    # добавляем уже существующее — дедуп, без изменений
    out = await _execute({"patch": {"todo+": ["a"]}}, _ctx(h))
    assert "без изменений" in out
