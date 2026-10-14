"""Тесты режима CHAT и правил истории."""
from __future__ import annotations

from agent_coder.agent.prompts import Mode
from agent_coder.config import config
from agent_coder.telegram.handlers import _should_preserve_history
from agent_coder.tools import build_registry


def test_mode_chat_exists():
    assert Mode.CHAT.value == "chat"


def test_chat_max_iterations_config():
    assert config.MAX_ITERATIONS_CHAT >= 1


def test_chat_tools_are_readonly_subset():
    r = build_registry()
    names = {t.name for t in r.for_mode(Mode.CHAT)}
    assert names == {"read_file", "list_files", "search"}


def test_chat_call_allowed_only_three():
    r = build_registry()
    read_tool = r.get("read_file")
    write_tool = r.get("write_file")
    run_tool = r.get("run_command")
    assert r.is_call_allowed(Mode.CHAT, read_tool, {"path": "x"}) is True
    assert r.is_call_allowed(Mode.CHAT, write_tool, {"path": "x"}) is False
    assert r.is_call_allowed(Mode.CHAT, run_tool, {"command": "ls"}) is False


# --- preserve history ---

def test_history_preserved_around_chat():
    assert _should_preserve_history(Mode.ACT, Mode.CHAT) is True
    assert _should_preserve_history(Mode.CHAT, Mode.ACT) is True
    assert _should_preserve_history(Mode.PLAN, Mode.CHAT) is True
    assert _should_preserve_history(Mode.CHAT, Mode.AUDIT) is True


def test_history_cleared_between_non_chat():
    assert _should_preserve_history(Mode.PLAN, Mode.ACT) is False
    assert _should_preserve_history(Mode.ACT, Mode.AUDIT) is False
    assert _should_preserve_history(Mode.PLAN, Mode.AUDIT) is False


# --- cmd_chat: создание сессии ---

def test_create_session_accepts_mode_and_title(tmp_path, monkeypatch):
    """Проверяем сигнатуру _create_session — поддерживает mode и title."""
    import inspect

    from agent_coder.telegram.handlers.project_utils import _create_session

    sig = inspect.signature(_create_session)
    params = sig.parameters
    assert "mode" in params
    assert "title" in params
    assert params["mode"].default == Mode.ACT
    assert params["title"].default is None
