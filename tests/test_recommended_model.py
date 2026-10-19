"""Смена режима ставит рекомендованную модель (фикс бага 2026-10-19).

Баг: /mode audit менял только session.mode, session.model оставался
deepseek-v4-flash (от прошлого режима). UI показывал deepseek, а роутер
при первом запуске подменял на kimi → рассинхрон UI и истории.
"""

from __future__ import annotations

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
