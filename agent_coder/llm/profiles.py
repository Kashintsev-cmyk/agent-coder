"""Per-model sampling profiles.

Источник правды - документация вендоров. Пустой профиль = не отправлять
sampling-параметры вообще (работают вендорские дефолты). Это официальная
рекомендация для закрытых reasoning-моделей (Claude 4.7+, GPT-5/6).

Проверено по докам: 2026-11-04. При смене моделей - перепроверять ссылки.
"""

from __future__ import annotations

from typing import Any, ClassVar

MODEL_PROFILES: ClassVar[dict[str, dict[str, Any]]] = {
    # DeepSeek: в thinking mode temperature/penalties игнорируются,
    # top_p зажат в 0.95-1.0. https://api-docs.deepseek.com/guides/thinking_mode
    "deepseek/deepseek-v4.1-flash": {
        "reasoning_effort": "high",
    },
    # Qwen3: официальные значения для thinking-режима.
    # Non-thinking: temperature 0.7, top_p 0.8, top_k 20.
    "qwen/qwen3.7-flash": {
        "temperature": 0.6,
        "top_p": 0.95,
        "top_k": 20,
    },
    # GLM-5.3-Flash: thinking.type поддерживает только enabled (не отключить).
    # https://docs.z.ai/guides/vlm/glm-5.3-flash  (Recommended Settings)
    # Если OpenRouter отклонит effort "max" - заменить на "high".
    "z-ai/glm-5.3-flash": {
        "temperature": 1.0,
        "top_p": 0.95,
        "reasoning_effort": "max",
    },
    # Claude 4.7+ / API 2026-10-21: temperature/top_p/top_k не поддерживаются,
    # не-дефолтное значение = 400. Детерминизм - промптом и structured output.
    # https://platform.claude.com/docs/en/build-with-claude/working-with-messages
    "anthropic/claude-haiku-5.5": {},
    # GPT-5/6: temperature/top_p не поддерживаются; управление через
    # reasoning_effort. https://developers.openai.com/api/docs/guides/latest-model
    "openai/gpt-6-luna": {},
    "mistralai/mistral-nemo": {
        "temperature": 0.35,
    },
}


def get_profile(model_id: str) -> dict[str, Any]:
    """Профиль модели (копия; изменение не заденет глобальный словарь)."""
    return dict(MODEL_PROFILES.get(model_id, {}))
