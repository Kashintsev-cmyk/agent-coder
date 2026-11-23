"""Per-model sampling profiles.

Источник правды - документация вендоров. Пустой профиль = не отправлять
sampling-параметры вообще (работают вендорские дефолты). Это официальная
рекомендация для закрытых reasoning-моделей (Claude 4.7+, GPT-5/6).

Проверено по докам: 2026-11-04. При смене моделей - перепроверять ссылки.
"""

from __future__ import annotations

from typing import Any

MODEL_PROFILES: dict[str, dict[str, Any]] = {
    "deepseek/deepseek-v4.1-flash-1017": {
        "reasoning_effort": "high",
    },
    # Claude 4.7+ / API 2026-10-21: temperature/top_p/top_k не поддерживаются,
    # не-дефолтное значение = 400. Для GPT-6 действует тот же контракт.
    "anthropic/claude-haiku-5.5": {},
    "openai/gpt-6-luna": {},
    "deepseek/deepseek-v4-pro-0813": {
        "reasoning_effort": "high",
    },
    "openai/gpt-6.1-terra": {},
    "anthropic/claude-sonnet-5.5": {},
    "openai/gpt-6.1-sol": {},
    "anthropic/claude-opus-5.5": {},
    # Legacy IDs retained for existing sessions and manual /model choices.
    "deepseek/deepseek-v4.1-flash": {
        "reasoning_effort": "high",
    },
    "qwen/qwen3.7-flash": {
        "temperature": 0.6,
        "top_p": 0.95,
        "top_k": 20,
    },
    "z-ai/glm-5.3-flash": {
        "temperature": 1.0,
        "top_p": 0.95,
        "reasoning_effort": "max",
    },
    "mistralai/mistral-nemo": {
        "temperature": 0.35,
    },
}


def get_profile(model_id: str) -> dict[str, Any] | None:
    """Профиль модели или None, если модели нет в реестре.

    Возвращаемое значение:
    - dict (возможно пустой) — модель в MODEL_PROFILES, профиль
      авторитетен для sampling-параметров. Пустой {} = не отправлять
      sampling вообще (закрытые reasoning-модели: Claude 4.7+, GPT-5/6).
    - None — модель вне реестра, вызывающий использует legacy-поведение
      (явный temperature уходит как есть).

    Возвращается копия: мутация не задевает глобальный MODEL_PROFILES.
    """
    profile = MODEL_PROFILES.get(model_id)
    return dict(profile) if profile is not None else None
