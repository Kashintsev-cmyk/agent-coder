"""F26: tool пропускает agent-coder и мета-репо (score=skipped)."""

from __future__ import annotations

import asyncio
import json

import pytest

from agent_coder.agent.prompts import Mode
from agent_coder.tools.check_project_standard import (
    EXCLUDED_NAMES,
    _execute,
)
from agent_coder.tools.registry import ToolContext


def _ctx(workspace: str) -> ToolContext:
    return ToolContext(workspace=workspace, mode=Mode.AUDIT)


@pytest.mark.parametrize("name", sorted(EXCLUDED_NAMES))
def test_excluded_returns_skipped(tmp_path, name: str) -> None:
    target = tmp_path / name
    target.mkdir()
    raw = asyncio.run(_execute({"path": name}, _ctx(str(tmp_path))))
    data = json.loads(raw)
    assert data["score"] == "skipped"
    assert data["majors"] == 0 and data["minors"] == 0
    assert data["issues"] == []
    assert "tool/meta" in data["reason"]


def test_non_excluded_still_checked(tmp_path) -> None:
    target = tmp_path / "regular-project"
    target.mkdir()
    raw = asyncio.run(_execute({"path": "regular-project"}, _ctx(str(tmp_path))))
    data = json.loads(raw)
    assert data["score"] in {"ok", "warnings", "failures"}
    assert "reason" not in data
