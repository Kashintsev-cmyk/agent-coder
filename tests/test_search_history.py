"""Тесты инструмента search_history (поиск по архиву сжатой истории)."""

from __future__ import annotations

import pytest

from agent_coder.agent import history_archive
from agent_coder.agent.prompts import Mode
from agent_coder.tools.registry import ToolContext
from agent_coder.tools.search_history import search_history_tool


@pytest.fixture
def ctx(tmp_path, monkeypatch):
    monkeypatch.setattr(
        type(history_archive.config), "SESSIONS_DIR", tmp_path, raising=False
    )
    return ToolContext(workspace=str(tmp_path), mode=Mode.ACT, session_id="s1")


def _seed(session_id: str, msgs: list[dict]) -> None:
    history_archive.append_messages(session_id, msgs)


@pytest.mark.asyncio
async def test_empty_archive_message(ctx):
    out = await search_history_tool.executor({"query": "foo"}, ctx)
    assert "не сжималась" in out


@pytest.mark.asyncio
async def test_finds_in_archive(ctx):
    _seed(
        "s1",
        [
            {"role": "user", "content": "исправь функцию calculate_total в billing.py"},
            {"role": "assistant", "content": "готово, поправил calculate_total"},
        ],
    )
    out = await search_history_tool.executor({"query": "calculate_total"}, ctx)
    assert "billing.py" in out
    assert "[user]" in out
    assert "[assistant]" in out


@pytest.mark.asyncio
async def test_no_match(ctx):
    _seed("s1", [{"role": "user", "content": "что-то другое"}])
    out = await search_history_tool.executor({"query": "несуществующее"}, ctx)
    assert "не найдено" in out


@pytest.mark.asyncio
async def test_empty_query(ctx):
    out = await search_history_tool.executor({"query": "  "}, ctx)
    assert "Ошибка" in out


@pytest.mark.asyncio
async def test_tool_call_rendered(ctx):
    _seed(
        "s1",
        [
            {
                "role": "assistant",
                "content": "",
                "tool_calls": [{"function": {"name": "read_file"}}],
            }
        ],
    )
    out = await search_history_tool.executor({"query": "read_file"}, ctx)
    assert "вызвал: read_file" in out


@pytest.mark.asyncio
async def test_multimodal_content(ctx):
    _seed(
        "s1",
        [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "секретный_маркер"},
                    {"type": "image_url", "image_url": {"url": "data:..."}},
                ],
            }
        ],
    )
    out = await search_history_tool.executor({"query": "секретный_маркер"}, ctx)
    assert "секретный_маркер" in out


def test_registered_and_read_only():
    from agent_coder.tools import build_registry

    reg = build_registry()
    tool = reg.get("search_history")
    assert tool is not None
    assert tool.read_only
    # Доступен в PLAN/AUDIT (read_only) и ACT
    assert tool in reg.for_mode(Mode.ACT)
    assert tool in reg.for_mode(Mode.PLAN)
