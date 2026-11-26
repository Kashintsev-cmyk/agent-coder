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
from agent_coder.agent.work_state import (
    PROMPT_CONTEXT_MAX_CHARS,
    render_prompt_context,
)


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


def test_prompt_context_projects_fields_by_mode() -> None:
    state = {
        "user_requests": ["request"],
        "findings": ["finding"],
        "done": ["completed"],
        "todo": ["next step"],
        "open_questions": ["question"],
    }

    plan = render_prompt_context(state, None, Mode.PLAN)
    act = render_prompt_context(state, None, Mode.ACT)
    audit = render_prompt_context(state, None, Mode.AUDIT)

    assert "request" in plan and "finding" in plan and "next step" in plan
    assert "completed" not in plan
    assert "completed" in act
    assert "finding" in audit and "question" in audit
    assert "completed" not in audit


def test_dynamic_prompt_context_obeys_total_budget() -> None:
    state = {"findings": [f"finding {index}: " + "x" * 450 for index in range(100)]}
    paths = {f"src/file_{index}.py": "digest " + "y" * 180 for index in range(60)}

    context = render_prompt_context(state, paths, Mode.AUDIT)

    assert len(context) <= PROMPT_CONTEXT_MAX_CHARS
    assert "[WORK_STATE]" in context
    assert "[ПРОЧИТАННЫЕ ФАЙЛЫ]" in context
    assert "src/file_59.py" in context
    assert "src/file_0.py" not in context
    assert "сокращён по лимиту prompt" in context


def test_mode_projection_can_render_empty_state() -> None:
    assert (
        render_prompt_context({"done": ["only ACT-relevant value"]}, None, Mode.PLAN)
        == ""
    )
