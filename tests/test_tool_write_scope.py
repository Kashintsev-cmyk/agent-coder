"""B1b (2026-11-07): write_file/edit_file уважают ctx.audit_scopes.

Регресс на `8f0cd848`/B1b: patch правил любые файлы, даже при scoped-audit.
Тест прямой: `_execute(args, ToolContext(audit_scopes=...))`.
"""

from __future__ import annotations

import pytest

from agent_coder.agent.prompts import Mode
from agent_coder.tools.edit_file import _execute as edit_exec
from agent_coder.tools.registry import ToolContext
from agent_coder.tools.write_file import _execute as write_exec


def _ctx(workspace: str, scopes: list[str] | None) -> ToolContext:
    return ToolContext(workspace=workspace, mode=Mode.ACT, audit_scopes=scopes)


@pytest.mark.asyncio
async def test_write_file_in_scope_ok(tmp_path):
    ctx = _ctx(str(tmp_path), ["bot/"])
    out = await write_exec({"path": "bot/x.py", "content": "print(1)\n"}, ctx)
    assert "создан" in out, out
    assert (tmp_path / "bot" / "x.py").exists()


@pytest.mark.asyncio
async def test_write_file_out_of_scope_rejected(tmp_path):
    ctx = _ctx(str(tmp_path), ["bot/"])
    out = await write_exec({"path": ".env", "content": "SECRET=1\n"}, ctx)
    assert "вне области аудита" in out, out
    assert not (tmp_path / ".env").exists()


@pytest.mark.asyncio
async def test_write_file_dotdot_bypass_rejected(tmp_path):
    """M2 `f1ccf91`: `bot/../.env` → normalize(None) → False."""
    ctx = _ctx(str(tmp_path), ["bot/"])
    out = await write_exec({"path": "bot/../.env", "content": "x\n"}, ctx)
    assert "вне области аудита" in out, out
    assert not (tmp_path / ".env").exists()


@pytest.mark.asyncio
async def test_write_file_unscoped_ok(tmp_path):
    """None scopes = без ограничений (ACT/PLAN, contract)."""
    ctx = _ctx(str(tmp_path), None)
    out = await write_exec({"path": ".env", "content": "x\n"}, ctx)
    assert "создан" in out, out


@pytest.mark.asyncio
async def test_edit_file_in_scope_ok(tmp_path):
    target = tmp_path / "bot" / "x.py"
    target.parent.mkdir(parents=True)
    target.write_text("old\n", encoding="utf-8")
    ctx = _ctx(str(tmp_path), ["bot/"])
    out = await edit_exec(
        {"path": "bot/x.py", "old_string": "old", "new_string": "new"}, ctx
    )
    assert "Заменено" in out, out
    assert target.read_text(encoding="utf-8") == "new\n"


@pytest.mark.asyncio
async def test_edit_file_out_of_scope_rejected_before_exists(tmp_path):
    """B1b: scope-gate ДО exists() — «не найден» не маскирует «вне scope»."""
    ctx = _ctx(str(tmp_path), ["bot/"])
    out = await edit_exec({"path": ".env", "old_string": "x", "new_string": "y"}, ctx)
    assert "вне области аудита" in out, out
    assert "не найден" not in out
