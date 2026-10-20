"""R13: read_file полного чтения по уже прочитанному — reject.

Только offset/limit. Причина: na_shtile с2 — read_file 28/32, cost 8.19₽.
"""

from __future__ import annotations

import pytest

from agent_coder.agent.prompts import Mode
from agent_coder.tools.read_file import MAX_BYTES, _execute
from agent_coder.tools.registry import ToolContext


def _ctx(tmp_path, read_paths=None) -> ToolContext:
    return ToolContext(
        workspace=str(tmp_path),
        mode=Mode.AUDIT,
        read_paths=read_paths,
    )


@pytest.mark.asyncio
async def test_full_read_of_proven_rejected(tmp_path) -> None:
    f = tmp_path / "a.py"
    f.write_text("line1\nline2\nline3\n", encoding="utf-8")
    out = await _execute({"path": "a.py"}, _ctx(tmp_path, {"a.py": "digest"}))
    assert "уже прочитан" in out
    assert "offset" in out.lower()


@pytest.mark.asyncio
async def test_full_read_of_proven_with_dot_slash_rejected(tmp_path) -> None:
    (tmp_path / "a.py").write_text("x\n", encoding="utf-8")
    out = await _execute({"path": "./a.py"}, _ctx(tmp_path, {"a.py": "d"}))
    assert "уже прочитан" in out


@pytest.mark.asyncio
async def test_slice_read_of_proven_allowed(tmp_path) -> None:
    f = tmp_path / "a.py"
    f.write_text("line1\nline2\nline3\n", encoding="utf-8")
    out = await _execute(
        {"path": "a.py", "offset": 1, "limit": 1},
        _ctx(tmp_path, {"a.py": "d"}),
    )
    assert "line2" in out
    assert "уже прочитан" not in out


@pytest.mark.asyncio
async def test_unproven_full_read_allowed(tmp_path) -> None:
    f = tmp_path / "a.py"
    f.write_text("line1\n", encoding="utf-8")
    out = await _execute({"path": "a.py"}, _ctx(tmp_path, {}))
    assert "line1" in out


@pytest.mark.asyncio
async def test_large_file_offset_zero_still_rejected(tmp_path) -> None:
    """offset=0 без limit — не slice, MAX_BYTES применяется (фикс обхода)."""
    big = tmp_path / "big.py"
    big.write_text("x" * (MAX_BYTES + 1000), encoding="utf-8")
    out = await _execute({"path": "big.py", "offset": 0}, _ctx(tmp_path))
    assert "слишком большой" in out
