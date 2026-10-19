"""Смена режима ставит рекомендованную модель (фикс бага 2026-10-19).

Баг: /mode audit менял только session.mode, session.model оставался
deepseek-v4-flash (от прошлого режима). UI показывал deepseek, а роутер
при первом запуске подменял на kimi → рассинхрон UI и истории.
"""

from __future__ import annotations

import pytest

from agent_coder.agent.prompts import Mode
from agent_coder.agent.session import Session
from agent_coder.config import config
from agent_coder.telegram.handlers.commands import _apply_recommended_model


def _session(mode: Mode = Mode.ACT, model: str | None = None) -> Session:
    return Session.new(
        workspace="/tmp",
        model=model or config.MODEL_MAIN,
        mode=mode,
    )


def test_recommended_map_covers_all_modes() -> None:
    """Каждый режим имеет рекомендацию, и она в CURATED."""
    for mode in Mode:
        assert mode.value in config.RECOMMENDED_MODEL_BY_MODE, mode.value
        assert config.RECOMMENDED_MODEL_BY_MODE[mode.value] in (
            config.CURATED_MODEL_IDS
        )


def test_audit_recommended_is_cheap_deepseek() -> None:
    """Аудит по умолчанию — deepseek (дёшево); kimi — для сложного вручную."""
    assert config.RECOMMENDED_MODEL_BY_MODE["audit"] == "deepseek/deepseek-v4-flash"
    assert config.RECOMMENDED_MANUAL_BY_MODE["audit"] == "moonshotai/kimi-k2.7-code"


def test_apply_on_mode_change_sets_recommended() -> None:
    """ACT → AUDIT: модель становится deepseek (дешёвая рекомендация)."""
    s = _session(Mode.ACT, model="anthropic/claude-sonnet-5.5")
    s.mode = Mode.AUDIT
    _apply_recommended_model(s)
    assert s.model == "deepseek/deepseek-v4-flash"


def test_apply_on_audit_to_act_returns_to_coding() -> None:
    """AUDIT → ACT: модель возвращается к coding-рекомендации."""
    s = _session(Mode.AUDIT, model="deepseek/deepseek-v4-flash")
    s.mode = Mode.ACT
    _apply_recommended_model(s)
    assert s.model == config.RECOMMENDED_MODEL_BY_MODE["act"]


def test_manual_choice_not_overridden() -> None:
    """model_manual=True — рекомендация НЕ перекрывает ручной выбор."""
    s = _session(Mode.ACT, model="anthropic/claude-sonnet-5.5")
    s.model_manual = True
    s.mode = Mode.AUDIT
    _apply_recommended_model(s)
    assert s.model == "anthropic/claude-sonnet-5.5"  # выбор пользователя


def test_new_session_uses_recommended_for_mode() -> None:
    """Session.new в project_utils — по режиму, не MODEL_MAIN."""
    # проверяем логику, которую использует project_utils
    for mode in Mode:
        initial = config.RECOMMENDED_MODEL_BY_MODE.get(mode.value, config.MODEL_MAIN)
        s = Session.new(workspace="/tmp", model=initial, mode=mode)
        assert s.model == config.RECOMMENDED_MODEL_BY_MODE[mode.value]


def test_models_keyboard_marks_recommended() -> None:
    """models_keyboard: ⭐ на RECOMMENDED_MODEL_BY_MODE, 🔧 на RECOMMENDED_MANUAL.

    Модели, доступные в UI, совпадают с MODEL_IDS (catalog.all);
    пометки — из config (единое ядро, AGENTS.md).
    """
    from agent_coder.telegram.keyboards import models_keyboard

    kb = models_keyboard(current_model="__none__")
    texts = [
        row[0].text
        for row in kb.inline_keyboard
        if row and row[0].callback_data.startswith("info:")
    ]
    assert any("⭐ act/plan/chat/audit" in t for t in texts), (
        "нет пометки ⭐ с mode-кодами (RECOMMENDED_MODEL_BY_MODE)"
    )
    assert any("🔧 audit" in t for t in texts), (
        "нет пометки 🔧 audit (RECOMMENDED_MANUAL_BY_MODE)"
    )


def test_session_new_model_manual_kwarg() -> None:
    """Session.new(model_manual=True) — явный выбор не перекрывается роутером.

    Регресс: CLI --model раньше не ставил model_manual, и router.resolve
    подменял модель на RECOMMENDED_MODEL_BY_MODE (AGENTS.md, B1).
    """
    s = Session.new(
        workspace="/tmp",
        model="vendor/custom",
        mode=Mode.ACT,
        model_manual=True,
    )
    assert s.model == "vendor/custom"
    assert s.model_manual is True

    default = Session.new(
        workspace="/tmp",
        model=config.DEFAULT_MODEL_ID,
        mode=Mode.ACT,
    )
    assert default.model_manual is False


@pytest.mark.asyncio
async def test_cli_audit_run_uses_recommended_not_group() -> None:
    """CLI-путь: run() ставит recommended (deepseek), не группу (kimi).

    Регресс сессии 95190cc2: /audit в CLI шёл через router.resolve →
    group_for_mode("audit") → kimi (27.51₽ за 21 итерацию). A10
    применялся только к Telegram (_apply_recommended_model).
    """
    from agent_coder.agent.loop import AgentLoop, LoopEvents
    from agent_coder.llm.types import AssistantMessage, ChatResponse, Usage

    class _EmptyRegistry:
        def openai_schemas(self, mode):
            return []

        def get(self, name):
            return None

        def is_call_allowed(self, mode, tool, args):
            return True

    def _events() -> LoopEvents:
        async def request_approval(tc):
            return True

        return LoopEvents(
            on_assistant_text=lambda *a: None,
            on_tool_call=lambda *a: None,
            on_tool_result=lambda *a: None,
            on_usage=lambda *a: None,
            on_error=lambda *a: None,
            request_approval=request_approval,
        )

    class _RecordingClient:
        """Запоминает модель, с которой его вызвали; отвечает финалом."""

        def __init__(self):
            self.models: list[str] = []

        async def chat(self, messages, *, model, **kwargs):
            self.models.append(model)
            return ChatResponse(
                message=AssistantMessage(content="готово", tool_calls=[]),
                usage=Usage(input_tokens=10, output_tokens=5, cost=0.0),
            )

    # Сессия в AUDIT с kimi (как после старого бага) — run() должен
    # переключить на recommended до первого запроса к LLM.
    s = Session.new(
        workspace="/tmp", model="moonshotai/kimi-k2.7-code", mode=Mode.AUDIT
    )
    s.messages = [{"role": "system", "content": "S"}]
    client = _RecordingClient()
    loop = AgentLoop(
        session=s,
        client=client,  # type: ignore[arg-type]
        registry=_EmptyRegistry(),  # type: ignore[arg-type]
        events=_events(),
    )
    await loop.run("аудит модуля")

    assert client.models == ["deepseek/deepseek-v4-flash"], (
        f"CLI-аудит должен идти на recommended (deepseek), не на группе "
        f"(kimi); фактически: {client.models}"
    )
    assert s.model == "deepseek/deepseek-v4-flash"
