"""Совместимость суммаризатора с порогом сжатия — до старта, а не в рантайме.

Проблема (аудит): ``config.validate()`` проверял только «SUMMARY_MODEL не
пуст». Но суммаризатору при сжатии уходит старый блок истории размером до
``MAX_HISTORY_TOKENS`` (recent может быть маленьким). Модель с меньшим
context падает на 400 context-length уже в работе — после успешной
валидации конфига. Отдельный сценарий: опечатка в SUMMARY_MODEL → каталог
отдаёт placeholder с нулевой ценой → сжатие молча не диагностируется и не
считается в деньгах.

Тесты держат ``compression.summary_model_problem()`` — единственную точку
истины, которую вызывает ``config.validate()``.
"""

from __future__ import annotations

from dataclasses import replace

import pytest

from agent_coder.agent import compression
from agent_coder.config import Config, config
from agent_coder.model_catalog import catalog


def _entry(model_id: str):
    """Запись каталога или None (для id, которого нет нигде)."""
    return catalog.get(model_id)


def test_current_config_is_valid(monkeypatch):
    """Сегодняшний конфиг проходит (haiku 1M против потолка 90K).

    ROUTERAI_API_KEY — deployment-секрет (в .gitignore, в CI-sim нет).
    Тест проверяет инварианты конфига, не наличие ключа; monkeypatch
    делает его env-independent (иначе CI-sim клон красный, локально зелёный).
    """
    monkeypatch.setattr(Config, "ROUTERAI_API_KEY", "test-key-for-validation")
    assert Config.validate() is True
    assert compression.summary_model_problem(config.SUMMARY_MODEL) is None


def test_empty_summary_model_reported():
    assert compression.summary_model_problem("") == "SUMMARY_MODEL пуст"


def test_unknown_model_reported():
    """Чужой id → placeholder с нулевыми ценами: сжатие не посчитать."""
    problem = compression.summary_model_problem("нет/такой-модели-12345")
    assert problem is not None
    assert "нет в каталоге" in problem


def test_small_context_model_rejected(monkeypatch):
    """Суммаризатор с context меньше потолка истории — ошибка конфига."""
    entry = _entry(config.SUMMARY_MODEL)
    assert entry is not None
    # Окно 64K против MAX_HISTORY_TOKENS=90000: old не влезет.
    small = replace(entry, context=64_000)
    monkeypatch.setattr(catalog, "get", lambda mid: small)
    problem = compression.summary_model_problem(config.SUMMARY_MODEL)
    assert problem is not None
    assert "context" in problem
    assert "MAX_HISTORY_TOKENS" in problem


def test_model_at_exact_ratio_accepted(monkeypatch):
    """Ровно на пороге ×SUMMARY_MIN_CONTEXT_RATIO — ещё допустимо."""
    entry = _entry(config.SUMMARY_MODEL)
    assert entry is not None
    need = int(config.MAX_HISTORY_TOKENS * compression.SUMMARY_MIN_CONTEXT_RATIO)
    monkeypatch.setattr(catalog, "get", lambda mid: replace(entry, context=need))
    assert compression.summary_model_problem(config.SUMMARY_MODEL) is None


def test_one_token_below_ratio_rejected(monkeypatch):
    """На один токен меньше — уже нет."""
    entry = _entry(config.SUMMARY_MODEL)
    assert entry is not None
    need = int(config.MAX_HISTORY_TOKENS * compression.SUMMARY_MIN_CONTEXT_RATIO)
    monkeypatch.setattr(catalog, "get", lambda mid: replace(entry, context=need - 1))
    assert compression.summary_model_problem(config.SUMMARY_MODEL) is not None


def test_zero_price_model_rejected(monkeypatch):
    """Цена 0 → сжатие не посчитать: это конфиг-ошибка, а не «бесплатно»."""
    entry = _entry(config.SUMMARY_MODEL)
    assert entry is not None
    monkeypatch.setattr(catalog, "get", lambda mid: replace(entry, input_price=0.0))
    problem = compression.summary_model_problem(config.SUMMARY_MODEL)
    assert problem is not None
    assert "цена" in problem


def test_explicit_soft_overrides_config(monkeypatch):
    """Явный soft (например, для модели с малым context) ужимает требование."""
    entry = _entry(config.SUMMARY_MODEL)
    assert entry is not None
    small = replace(entry, context=80_000)
    monkeypatch.setattr(catalog, "get", lambda mid: small)
    # При MAX_HISTORY_TOKENS=90000 — не проходит.
    assert compression.summary_model_problem(config.SUMMARY_MODEL) is not None
    # При soft=60000 требуется 75000 — 80000 хватает.
    assert compression.summary_model_problem(config.SUMMARY_MODEL, soft=60_000) is None


def test_validate_reports_summary_problem(monkeypatch):
    """Ошибка суммаризатора попадает в общий список validate()."""

    class _Broken(Config):
        SUMMARY_MODEL = "нет/такой-модели-12345"

    with pytest.raises(ValueError) as exc:
        _Broken.validate()
    assert "SUMMARY_MODEL" in str(exc.value)


def test_min_context_ratio_covers_estimate_error():
    """Запас 1.25 перекрывает расхождение эвристики и провайдера.

    На 115 живых сессиях median ratio провайдер/эвристика = 1.105, значит
    реальный old может быть на ~10% больше эвристического. 1.25 — с запасом.
    """
    assert compression.SUMMARY_MIN_CONTEXT_RATIO >= 1.105
    need = config.MAX_HISTORY_TOKENS * compression.SUMMARY_MIN_CONTEXT_RATIO
    assert need < _entry(config.SUMMARY_MODEL).context
