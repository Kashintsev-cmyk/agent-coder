"""Инварианты deploy/agent-coder-bot.service (M6+M7).

Статический тест unit-файла: без systemd, только grep по содержимому.
Защищает от регрессий hardening при правках unit.
"""

from __future__ import annotations

from pathlib import Path

UNIT = Path(__file__).resolve().parent.parent / "deploy" / "agent-coder-bot.service"


def _unit() -> str:
    return UNIT.read_text(encoding="utf-8")


def test_unit_exists():
    assert UNIT.is_file()


def _active_lines() -> str:
    """Unit без комментариев: только действующие директивы."""
    text = _unit()
    return "\n".join(ln for ln in text.splitlines() if not ln.lstrip().startswith("#"))


def test_m6_no_docker_supplementary_group():
    """M6: docker-группа = root-эквивалент через socket, не должна быть."""
    assert "SupplementaryGroups=docker" not in _active_lines()
    assert "M6" in _unit()  # комментарий-объяснение остаётся


def test_m7_code_readonly():
    """M7: код бота — ReadOnlyPaths (перечисление подкаталогов)."""
    text = _unit()
    for path in [
        "/home/botuser/bots/agent-coder/agent_coder",
        "/home/botuser/bots/agent-coder/tests",
        "/home/botuser/bots/agent-coder/deploy",
        "/home/botuser/bots/agent-coder/docs",
        "/home/botuser/bots/agent-coder/pyproject.toml",
        "/home/botuser/bots/agent-coder/.env",
    ]:
        assert f"ReadOnlyPaths={path}\n" in text, f"missing ReadOnlyPaths={path}"


def test_m7_sessions_still_writable():
    """M7: sessions/ остаётся RW (иначе бот не сможет писать историю)."""
    text = _unit()
    assert "ReadWritePaths=/home/botuser/bots/agent-coder/sessions\n" in text


def test_m7_git_and_venv_not_readonly():
    """M7: .git/ и .venv/ НЕ в ReadOnlyPaths (git_commit / рантайм)."""
    text = _unit()
    assert "/home/botuser/bots/agent-coder/.git\n" not in text
    assert "/home/botuser/bots/agent-coder/.venv\n" not in text
