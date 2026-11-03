"""Совместимость kwargs _build_request_kwargs с AsyncCompletions.create.

Регресс на баг bfff1ca: qwen-профиль клал top_k в top-level kwargs,
AsyncCompletions.create отклонял → TypeError на любом вызове qwen
(verify, loop.py:270/660, plan/act fallback). 954 passed это не ловили:
tests/test_model_profiles.py проверял содержимое kwargs, но не
совместимость с сигнатурой SDK.

Здесь ловится весь класс: любой top-level kwarg, которого нет в
сигнатуре AsyncCompletions.create, — падение (SDK не имеет **kwargs).
"""

from __future__ import annotations

import inspect

from openai.resources.chat.completions import AsyncCompletions

from agent_coder.config import config
from agent_coder.llm.client import _build_request_kwargs

MSG = [{"role": "user", "content": "hi"}]

# Расширения RouterAI, которых SDK не знает, но прокси принимает.
# plugins — top-level (web_search=True). Проверено на проде до bfff1ca.
KNOWN_EXTENSIONS = {"plugins"}


def test_async_completions_create_has_no_kwargs() -> None:
    """Гигиена теста: если SDK обзаведётся **kwargs — тест перестанет ловить."""
    sig = inspect.signature(AsyncCompletions.create)
    has_var_kw = any(p.kind == p.VAR_KEYWORD for p in sig.parameters.values())
    assert not has_var_kw, (
        "AsyncCompletions.create получил **kwargs — тест выше не поймает "
        "неизвестные ключи. Нужен mock create + проверка kwargs."
    )


def test_all_top_level_kwargs_in_sdk_signature() -> None:
    """Все top-level kwargs для всех MODEL_IDS ∈ сигнатура SDK (+ ext)."""
    allowed = set(inspect.signature(AsyncCompletions.create).parameters)
    allowed.add("extra_body")  # RouterAI-extension, SDK принимает отдельно
    allowed |= KNOWN_EXTENSIONS

    bad: dict[str, list[str]] = {}
    for model in config.MODEL_IDS:
        kwargs = _build_request_kwargs(model, MSG)
        unknown = set(kwargs) - allowed
        if unknown:
            bad[model] = sorted(unknown)
    assert not bad, (
        f"kwargs вне сигнатуры AsyncCompletions.create: {bad}. "
        f"SDK упадёт с TypeError. Провайдерские параметры — через extra_body."
    )


def test_top_k_in_extra_body_for_qwen() -> None:
    """qwen: top_k не в top-level (SDK отвергнет), а в extra_body."""
    kwargs = _build_request_kwargs("qwen/qwen3.7-flash", MSG)
    assert "top_k" not in kwargs
    assert kwargs["extra_body"]["top_k"] == 20
