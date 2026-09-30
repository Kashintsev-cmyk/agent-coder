"""Кураторский список моделей RouterAI для выбора в UI.

Файл `routerai_models.json` содержит ~400 моделей. Здесь — 12 самых
полезных для кодинга и общих задач.

Поле `pricing` — в рублях за 1M токенов (точные цены с routerai.ru).
Поле `reasoning` — модель принимает `reasoning_effort`.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ModelInfo:
    id: str
    name: str
    context: int
    reasoning: bool
    input_price: float   # ₽ за 1M
    output_price: float  # ₽ за 1M
    tag: str = ""


CURATED_MODELS: list[ModelInfo] = [
    ModelInfo(
        id="deepseek/deepseek-v4.1-flash",
        name="DeepSeek V4.1 Flash",
        context=1_048_576,
        reasoning=True,
        input_price=2.39,
        output_price=47.0,
        tag="быстрая, дешёвая",
    ),
    ModelInfo(
        id="deepseek/deepseek-v4-pro",
        name="DeepSeek V4 Pro",
        context=1_048_576,
        reasoning=True,
        input_price=35.0,
        output_price=70.0,
        tag="умнее flash",
    ),
    ModelInfo(
        id="anthropic/claude-sonnet-4.6",
        name="Claude Sonnet 4.6",
        context=1_000_000,
        reasoning=True,
        input_price=80.0,
        output_price=400.0,
        tag="топ для кода",
    ),
    ModelInfo(
        id="anthropic/claude-haiku-4.5",
        name="Claude Haiku 4.5",
        context=200_000,
        reasoning=True,
        input_price=27.0,
        output_price=135.0,
        tag="быстрая Anthropic",
    ),
    ModelInfo(
        id="openai/gpt-5-nano",
        name="GPT-5 Nano",
        context=400_000,
        reasoning=True,
        input_price=1.4,
        output_price=11.0,
        tag="очень дешёвая",
    ),
    ModelInfo(
        id="openai/gpt-5.4-mini",
        name="GPT-5.4 Mini",
        context=400_000,
        reasoning=True,
        input_price=20.0,
        output_price=120.0,
        tag="баланс цена/качество",
    ),
    ModelInfo(
        id="openai/gpt-5.4",
        name="GPT-5.4",
        context=1_050_000,
        reasoning=True,
        input_price=68.0,
        output_price=400.0,
        tag="топ OpenAI",
    ),
    ModelInfo(
        id="google/gemini-3.5-flash",
        name="Gemini 3.5 Flash",
        context=1_048_576,
        reasoning=True,
        input_price=40.0,
        output_price=240.0,
        tag="длинный контекст",
    ),
    ModelInfo(
        id="google/gemini-2.5-flash",
        name="Gemini 2.5 Flash",
        context=1_048_576,
        reasoning=True,
        input_price=8.0,
        output_price=67.0,
        tag="дешёвый Gemini",
    ),
    ModelInfo(
        id="qwen/qwen3-coder-next",
        name="Qwen3 Coder Next",
        context=262_144,
        reasoning=False,
        input_price=3.3,
        output_price=22.0,
        tag="для кода",
    ),
    ModelInfo(
        id="moonshotai/kimi-k2.7-code",
        name="Kimi K2.7 Code",
        context=262_144,
        reasoning=False,
        input_price=19.0,
        output_price=80.0,
        tag="для кода",
    ),
    ModelInfo(
        id="z-ai/glm-5.3-flash",
        name="GLM 5.3 Flash",
        context=1_310_720,
        reasoning=True,
        input_price=1.3,
        output_price=4.1,
        tag="дешёвая",
    ),
]


def find_model(model_id: str) -> ModelInfo | None:
    for m in CURATED_MODELS:
        if m.id == model_id:
            return m
    return None


def get_model_name(model_id: str) -> str:
    m = find_model(model_id)
    return m.name if m else model_id


# Полный набор уровней reasoning, который принимает routerai
REASONING_LEVELS = [
    "none",
    "minimal",
    "low",
    "medium",
    "high",
    "xhigh",
    "max",
    "ultra",
]
DEFAULT_REASONING = "medium"