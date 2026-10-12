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
        for f in (h.filters or []):
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
