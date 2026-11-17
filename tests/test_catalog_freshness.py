"""_FALLBACK_CATALOG не должен содержать мёртвые/неполные записи."""
from __future__ import annotations

from pathlib import Path

from agent_coder.config import Config
from agent_coder.models import _FALLBACK_CATALOG

# Осознанно оставлены для обратной совместимости сессий.
# Добавлять сюда только с комментарием почему.
LEGACY_IDS: set[str] = {
    "anthropic/claude-haiku-4.5",
    "anthropic/claude-sonnet-4.6",
    "anthropic/claude-sonnet-5.5",
    "deepseek/deepseek-v4-flash",
    "deepseek/deepseek-v4-pro",
    "google/gemini-2.5-flash",
    "google/gemini-4-argon",
    "moonshotai/kimi-k2.7-code",
    "openai/gpt-6.1-sol",
    "qwen/qwen3-coder-next",
    "x-ai/grok-4.20",
    "z-ai/glm-5.3",
}


def test_no_stray_models() -> None:
    stray = set(_FALLBACK_CATALOG) - set(Config.MODEL_IDS) - LEGACY_IDS
    assert not stray, f"модели вне MODEL_IDS/LEGACY: {sorted(stray)}"


def test_no_zero_context() -> None:
    bad = sorted(m for m, v in _FALLBACK_CATALOG.items() if v.context <= 0)
    assert not bad, f"context<=0: {bad}"


def test_no_zero_prices() -> None:
    bad = sorted(
        m for m, v in _FALLBACK_CATALOG.items()
        if v.input_price <= 0 or v.output_price <= 0
    )
    assert not bad, f"нулевые цены: {bad}"


def test_no_todo_in_catalog() -> None:
    src = Path("agent_coder/models.py").read_text(encoding="utf-8")
    lines = src.splitlines()
    in_fb = False
    bad: list[int] = []
    for i, line in enumerate(lines, 1):
        if "_FALLBACK_CATALOG" in line and "=" in line:
            in_fb = True
        elif in_fb and line.startswith("}"):
            break
        elif in_fb and "TODO" in line:
            bad.append(i)
    assert not bad, f"TODO в проде, строки: {bad}"
