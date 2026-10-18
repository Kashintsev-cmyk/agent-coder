"""Тест: все команды из BOT_COMMANDS имеют зарегистрированный хендлер.

Ловит класс багов, когда декоратор вешают не на ту функцию
(как было с /mode — Command("mode") висел на helper'е).
"""

from __future__ import annotations

from agent_coder.telegram import handlers
from agent_coder.telegram.bot import BOT_COMMANDS


def _registered_command_names() -> set[str]:
    """Все Command(...) в router.message."""
    names: set[str] = set()
    for h in handlers.router.message.handlers:
        for f in h.filters or []:
            # aiogram хранит фильтры в разных обёртках; достаём Command
            cmd = getattr(f, "commands", None)
            if cmd is None:
                # возможна обёртка FilterObject: попробуем через callback
                cb = getattr(f, "callback", None)
                cmd = getattr(cb, "commands", None)
            if cmd:
                if isinstance(cmd, str):
                    names.add(cmd)
                else:
                    names.update(cmd)
    return names


def test_all_bot_commands_registered():
    registered = _registered_command_names()
    declared = {c.command for c in BOT_COMMANDS}
    missing = declared - registered
    assert not missing, (
        f"Команды объявлены в BOT_COMMANDS, но не зарегистрированы в router: "
        f"{sorted(missing)}"
    )


def test_yolo_is_declared_in_bot_menu():
    """Регресс: /yolo должна быть в меню команд, иначе о ней легко забыть."""
    declared = {c.command for c in BOT_COMMANDS}
    assert "yolo" in declared
    assert "yolo" in _registered_command_names()


def test_yolo_is_documented_in_help():
    """Регресс: /yolo упомянута в тексте справки /start (он же /help)."""
    import inspect

    from agent_coder.telegram.handlers import commands

    src = inspect.getsource(commands.cmd_start)
    assert "/yolo" in src
