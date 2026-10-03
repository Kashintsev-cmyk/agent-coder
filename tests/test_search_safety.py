"""Тесты безопасности search: path внутри workspace."""
from __future__ import annotations

import pytest

from agent_coder.agent.prompts import Mode
from agent_coder.tools.registry import ToolContext
from agent_coder.tools.search import _execute


@pytest.fixture
def ctx(tmp_path):
    return ToolContext(workspace=str(tmp_path), mode=Mode.ACT, yolo=False)


@pytest.mark.asyncio
async def test_path_outside_workspace_rejected(ctx):
    result = await _execute({"query": "x", "path": "/etc"}, ctx)
    assert "вне workspace" in result


@pytest.mark.asyncio
async def test_path_traversal_rejected(ctx):
    result = await _execute({"query": "x", "path": "../../etc"}, ctx)
    assert "вне workspace" in result


@pytest.mark.asyncio
async def test_path_inside_workspace_ok(ctx, tmp_path):
    (tmp_path / "a.txt").write_text("hello")
    result = await _execute({"query": "hello", "path": "."}, ctx)
    assert "вне workspace" not in result
