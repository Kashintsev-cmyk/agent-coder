"""Тесты режима CHAT и правил истории."""
from __future__ import annotations

from agent_coder.agent.prompts import Mode
from agent_coder.config import config
from agent_coder.telegram.handlers import _should_preserve_history
from agent_coder.tools import build_registry


def test_mode_chat_exists():
    assert Mode.CHAT.value == "chat"


def test_chat_no_own_iteration_limit():
    """В CHAT нет собственного лимита итераций: нет tools → нет цикла.

    Chat использует общий MAX_ITERATIONS как страховку, но реально
    всегда 1 итерация (LLM отвечает и завершает).
    """
    assert not hasattr(config, "MAX_ITERATIONS_CHAT")
    assert config.MAX_ITERATIONS >= 1


def test_chat_tools_are_empty():
    """CHAT работает без инструментов.

    Причина: с урезанным набором tools DeepSeek уходил в нативный
    DSML-формат вместо OpenAI tool_calls, и сырой XML утекал в ответ.
    """
    r = build_registry()
    assert r.for_mode(Mode.CHAT) == []


def test_chat_call_allowed_nothing():
    """В CHAT не разрешён ни один инструмент (их там и нет)."""
    r = build_registry()
    read_tool = r.get("read_file")
    write_tool = r.get("write_file")
    run_tool = r.get("run_command")
    assert r.is_call_allowed(Mode.CHAT, read_tool, {"path": "x"}) is False
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


# --- continue_keyboard ---

def test_continue_keyboard_structure():
    from agent_coder.telegram import keyboards
    kb = keyboards.continue_keyboard("abc12345")
    cbs = [b.callback_data for row in kb.inline_keyboard for b in row]
    assert any(cb.startswith("continue_task:abc12345") for cb in cbs)
    assert any(cb.startswith("mode:chat:abc12345") for cb in cbs)
    assert any(cb.startswith("mode:plan:abc12345") for cb in cbs)


# --- loop result contract ---

def test_loop_result_defaults():
    from agent_coder.agent.loop import LoopResult
    r = LoopResult()
    assert r.iterations_used == 0
    assert r.limit_reached is False
    assert r.watchdog_triggered is False
    assert r.consecutive_errors == 0


def test_auto_continue_config_exists():
    from agent_coder.config import config
    assert config.MAX_AUTO_CONTINUE >= 1
    assert config.MAX_CONSECUTIVE_ERRORS >= 1


# --- YOLO ---

def test_session_yolo_default_false():
    from agent_coder.agent.session import Session
    from agent_coder.agent.prompts import Mode
    s = Session.new(workspace="/tmp", model="x", mode=Mode.ACT)
    assert s.yolo is False


def test_session_yolo_roundtrip():
    from agent_coder.agent.session import Session
    from agent_coder.agent.prompts import Mode
    s = Session.new(workspace="/tmp", model="x", mode=Mode.ACT)
    s.yolo = True
    s2 = Session.from_dict(s.to_dict())
    assert s2.yolo is True


def test_session_yolo_backward_compat():
    from agent_coder.agent.session import Session
    from agent_coder.agent.prompts import Mode
    s = Session.new(workspace="/tmp", model="x", mode=Mode.ACT)
    d = s.to_dict()
    d.pop("yolo", None)
    s2 = Session.from_dict(d)
    assert s2.yolo is False


def test_hard_block_catches_destructive():
    from agent_coder.tools.run_command import _HARD_BLOCK
    for cmd in [
        "rm -rf /",
        "rm -rf /*",
        "rm -rf /etc",
        "mkfs.ext4 /dev/sda",
        "dd if=/dev/zero of=/dev/sda",
        ":(){ :|:& };:",
        "wipefs /dev/sda",
    ]:
        assert _HARD_BLOCK.search(cmd), f"не поймал: {cmd}"


def test_hard_block_allows_safe():
    from agent_coder.tools.run_command import _HARD_BLOCK
    for cmd in [
        "ls -la",
        "rm /tmp/mytest",
        "rm -rf ~/projects/old",
        "dd if=/dev/zero of=/tmp/test.img bs=1M count=10",
        "git status",
    ]:
        assert not _HARD_BLOCK.search(cmd), f"ложное срабатывание: {cmd}"
