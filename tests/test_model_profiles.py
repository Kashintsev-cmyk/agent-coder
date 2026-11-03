"""Тесты per-model sampling profiles (agent_coder/llm/profiles.py).

Контракты, зафиксированные здесь (см. roadmap, задача per-model profiles):
- sampling-параметры — per-model, профиль важнее явного temperature;
- reasoning_effort — явный выбор пользователя важнее baseline профиля;
- модель вне реестра (get_profile → None) — legacy-поведение.
"""

from __future__ import annotations

from agent_coder.config import config
from agent_coder.llm.client import _build_request_kwargs
from agent_coder.llm.profiles import MODEL_PROFILES, get_profile

MSG = [{"role": "user", "content": "hi"}]


class TestRegistry:
    def test_all_profile_keys_in_model_ids(self) -> None:
        """Нет мёртвых профилей: каждый ключ MODEL_PROFILES ∈ MODEL_IDS."""
        unknown = set(MODEL_PROFILES) - set(config.MODEL_IDS)
        assert not unknown, (
            f"Профили вне MODEL_IDS: {sorted(unknown)}. "
            f"Либо добавь модель в MODEL_IDS, либо удали профиль."
        )

    def test_every_model_id_has_profile(self) -> None:
        """Каждая модель из MODEL_IDS имеет профиль (возможно пустой)."""
        missing = [mid for mid in config.MODEL_IDS if mid not in MODEL_PROFILES]
        assert not missing, (
            f"MODEL_IDS без профиля: {missing}. "
            f"Добавь в MODEL_PROFILES (пустой dict = не отправлять sampling)."
        )

    def test_get_profile_returns_copy(self) -> None:
        """get_profile возвращает копию — мутация не задевает MODEL_PROFILES."""
        p1 = get_profile("deepseek/deepseek-v4.1-flash")
        assert p1 is not None
        p1["_mutated"] = True
        p2 = get_profile("deepseek/deepseek-v4.1-flash")
        assert p2 is not None
        assert "_mutated" not in p2

    def test_get_profile_unknown_returns_none(self) -> None:
        """Модель вне реестра → None (legacy-поведение в _build_request_kwargs)."""
        assert get_profile("some/model-not-in-registry") is None


class TestHaiku5_5:
    """Claude 4.7+: temperature/top_p/top_k не поддерживаются, не-дефолт = 400."""

    def test_profile_is_empty(self) -> None:
        assert MODEL_PROFILES["anthropic/claude-haiku-5.5"] == {}

    def test_no_sampling_params_in_request(self) -> None:
        # compression.py:510 передаёт именно temperature=0.0 — не должен уйти.
        kwargs = _build_request_kwargs(
            "anthropic/claude-haiku-5.5", MSG, temperature=0.0
        )
        assert "temperature" not in kwargs
        assert "top_p" not in kwargs
        assert "top_k" not in kwargs

    def test_reasoning_effort_passes_through(self) -> None:
        kwargs = _build_request_kwargs(
            "anthropic/claude-haiku-5.5", MSG, reasoning_effort="medium"
        )
        assert kwargs["reasoning_effort"] == "medium"

    def test_reasoning_effort_none_omitted(self) -> None:
        # verify.py передаёт session.reasoning_effort; при "none" — не уходит.
        kwargs = _build_request_kwargs(
            "anthropic/claude-haiku-5.5", MSG, reasoning_effort="none"
        )
        assert "reasoning_effort" not in kwargs


class TestDeepSeekV4_1Flash:
    """Thinking mode: temperature/penalties игнорируются, только reasoning_effort."""

    def test_profile_has_no_sampling(self) -> None:
        profile = MODEL_PROFILES["deepseek/deepseek-v4.1-flash"]
        assert "temperature" not in profile
        assert "top_p" not in profile
        assert "top_k" not in profile

    def test_baseline_reasoning_effort_applied(self) -> None:
        kwargs = _build_request_kwargs("deepseek/deepseek-v4.1-flash", MSG)
        assert kwargs["reasoning_effort"] == "high"

    def test_explicit_reasoning_overrides_profile(self) -> None:
        kwargs = _build_request_kwargs(
            "deepseek/deepseek-v4.1-flash", MSG, reasoning_effort="low"
        )
        assert kwargs["reasoning_effort"] == "low"

    def test_explicit_temperature_ignored(self) -> None:
        # loop.py передаёт 0.2 — для thinking mode это игнор, профиль важнее.
        kwargs = _build_request_kwargs(
            "deepseek/deepseek-v4.1-flash", MSG, temperature=0.2
        )
        assert "temperature" not in kwargs


class TestQwen3_7Flash:
    def test_profile_sampling_applied_even_with_explicit(self) -> None:
        # loop.py передаёт temperature=0.2 — профиль важнее (0.6).
        kwargs = _build_request_kwargs("qwen/qwen3.7-flash", MSG, temperature=0.2)
        assert kwargs["temperature"] == 0.6
        assert kwargs["top_p"] == 0.95
        assert kwargs["top_k"] == 20


class TestGlm5_3Flash:
    def test_profile_applied(self) -> None:
        kwargs = _build_request_kwargs("z-ai/glm-5.3-flash", MSG)
        assert kwargs["temperature"] == 1.0
        assert kwargs["top_p"] == 0.95
        assert kwargs["reasoning_effort"] == "max"

    def test_explicit_reasoning_overrides_max(self) -> None:
        # Пользователь выбрал "medium" — не зажимаем на "max".
        kwargs = _build_request_kwargs(
            "z-ai/glm-5.3-flash", MSG, reasoning_effort="medium"
        )
        assert kwargs["reasoning_effort"] == "medium"


class TestMistralNemo:
    def test_profile_temperature(self) -> None:
        kwargs = _build_request_kwargs("mistralai/mistral-nemo", MSG)
        assert kwargs["temperature"] == 0.35


class TestUnknownModel:
    """Модели вне MODEL_PROFILES — legacy-поведение."""

    def test_explicit_temperature_passes_through(self) -> None:
        kwargs = _build_request_kwargs("some/unknown-model", MSG, temperature=0.2)
        assert kwargs["temperature"] == 0.2

    def test_no_explicit_temperature_omits_key(self) -> None:
        kwargs = _build_request_kwargs("some/unknown-model", MSG)
        assert "temperature" not in kwargs

    def test_explicit_reasoning_passes_through(self) -> None:
        kwargs = _build_request_kwargs(
            "some/unknown-model", MSG, reasoning_effort="low"
        )
        assert kwargs["reasoning_effort"] == "low"
