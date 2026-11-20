"""Стабильный префикс запроса: кэш провайдера не должен промахиваться.

Проблема (2026-11-20): блоки [WORK_STATE] и [ПРОЧИТАННЫЕ ФАЙЛЫ] собирались
внутри system prompt, то есть в messages[0]. Любой update_work_state или
read_file менял начало запроса → prefix-кэш OpenAI промахивался на всём
контексте. Замер по логам за 7 дней: 20% запросов с cache=0, 36.9M
сожжённых токенов.

Фикс: system prompt содержит только стабильный текст (prompt режима,
правило structured memory, AGENTS.md). Волатильные блоки уходят в
хвостовое сообщение, которое подмешивается ТОЛЬКО в запрос и не
персистится в историю сессии.
"""

from __future__ import annotations

from agent_coder.agent.prompts import Mode, system_prompt, volatile_block


class _Handle:
    """Минимальный стенд WorkStateHandle: отдаёт _state."""

    def __init__(self, state: dict | None = None) -> None:
        self._state = state if state is not None else {}


def test_system_prompt_takes_no_volatile_arguments() -> None:
    """Сигнатура не принимает work_state/read_paths — волатильного в system нет."""
    out = system_prompt(Mode.ACT, "/tmp/ws")
    assert "[WORK_STATE]" not in out
    assert "[ПРОЧИТАННЫЕ ФАЙЛЫ]" not in out
    # Правило про structured memory остаётся: это стабильный текст.
    assert "update_work_state" in out


def test_system_prompt_is_deterministic() -> None:
    """Два вызова с теми же аргументами дают байт-идентичный текст."""
    for mode in (Mode.PLAN, Mode.ACT, Mode.AUDIT):
        assert system_prompt(mode, "/tmp/ws") == system_prompt(mode, "/tmp/ws")


def test_volatile_block_contains_both_sections() -> None:
    block = volatile_block(
        _Handle({"findings": ["факт А"], "todo": ["шаг Б"]}),
        {"b.py": "b.py (2): def bar"},
    )
    assert "[WORK_STATE]" in block
    assert "факт А" in block
    assert "шаг Б" in block
    assert "[ПРОЧИТАННЫЕ ФАЙЛЫ]" in block
    assert "b.py" in block


def test_volatile_block_empty_when_nothing_to_say() -> None:
    assert volatile_block(_Handle(), None) == ""
    assert volatile_block(None, {}) == ""
    assert volatile_block(_Handle(), {}) == ""


def test_volatile_block_partial() -> None:
    """Есть только read_paths — блок всё равно собирается."""
    block = volatile_block(None, {"c.py": "c.py (1): x"})
    assert "[ПРОЧИТАННЫЕ ФАЙЛЫ]" in block
    assert "[WORK_STATE]" not in block
