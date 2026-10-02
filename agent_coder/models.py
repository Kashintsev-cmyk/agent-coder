"""Описание модели (`ModelInfo`) и аварийный снапшот-каталог моделей RouterAI.

Этот модуль — НЕ источник curated-списка. Единый источник правды о моделях —
`agent_coder.model_catalog.catalog` (цепочка fallback → cache → api).
Здесь остаются:

- `ModelInfo` — dataclass-схема метаданных одной модели;
- `_FALLBACK_CATALOG` — аварийный snapshot, который `model_catalog` использует,
  когда нет ни API, ни дискового кэша;
- `_make_placeholder`, `CAPABILITIES`, `REASONING_LEVELS`, `DEFAULT_REASONING`.

Какие модели показывать в UI и какая является дефолтной, определяет
`.env` (`CURATED_MODEL_IDS` и `DEFAULT_MODEL_ID`, см. `agent_coder.config`),
но доступ к ним идёт через `model_catalog.catalog` (методы `all()`/`get()`/
`default()`), а хелперы `find_model`/`get_model_name`/`supports`/
`capabilities` ниже делегируют туда.

Поле `pricing` — в рублях за 1M токенов (точные цены с routerai.ru).

Разница между `reasoning` и `reasoning_effort`:
- `reasoning`        — модель умеет «думать» (chain-of-thought) вообще.
- `reasoning_effort` — модель принимает ГРАДАЦИЮ усилий (`none`..`ultra`),
  т.е. позволяет управлять глубиной размышлений. Инвариант: если
  `reasoning_effort=True`, то и `reasoning=True` (градация без самого
  reasoning-режима невозможна).
"""
from __future__ import annotations

import logging
from dataclasses import dataclass

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ModelInfo:
    """Описание одной модели RouterAI.

    Capability-флаги:
    - `vision`              — модель принимает `image_url` в messages
      (есть "image" в input_modalities: умеет смотреть картинки).
    - `tools`               — модель умеет вызывать НАШИ инструменты
      (function calling; поддерживает "tools" в supported_parameters).
    - `reasoning`           — модель умеет «думать» (chain-of-thought).
    - `reasoning_effort`    — модель принимает ГРАДАЦИЮ усилий reasoning
      ("reasoning_effort" в supported_parameters). Это отличие от простого
      `reasoning`: градация vs просто вкл-выкл. Инвариант: если True, то
      `reasoning` тоже True.
    - `web_search_builtin`  — у модели есть ВСТРОЕННЫЙ web search на стороне
      провайдера (`pricing.web_search` или "web_search_options" в params).
      Не путать с нашим инструментом `web_search`: `tools` — «может звать
      наши инструменты», `web_search_builtin` — «у модели есть свой поиск».
    """

    id: str
    name: str
    context: int
    reasoning: bool
    vision: bool = False
    tools: bool = True
    reasoning_effort: bool = False
    web_search_builtin: bool = False
    verbosity: bool = False
    input_price: float = 0.0   # ₽ за 1M
    output_price: float = 0.0  # ₽ за 1M
    tag: str = ""

    def __post_init__(self) -> None:
        if self.reasoning_effort and not self.reasoning:
            raise ValueError(
                f"ModelInfo {self.id!r}: reasoning_effort=True требует "
                "reasoning=True (градация без reasoning-режима невозможна)"
            )


# Статический каталог (fallback): по одной записи на id. Здесь — ТОЛЬКО данные
# о моделях. Что показывать в UI — решает model_catalog.catalog (all()).
#
# Аварийный источник. Используется только когда model_catalog не смог получить
# данные ни из API, ни из кэша. Обновлять цены/context здесь не нужно — их
# тянет model_catalog при старте.
_FALLBACK_CATALOG: dict[str, ModelInfo] = {
    # --- Модели с явно заданными capability-флагами ---
    "deepseek/deepseek-v4.1-flash": ModelInfo(
        id="deepseek/deepseek-v4.1-flash",
        name="DeepSeek V4.1 Flash",
        context=1_048_576,
        reasoning=True,
        vision=True,
        tools=True,
        reasoning_effort=True,
        web_search_builtin=False,
        input_price=2.39,
        output_price=47.0,
        tag="быстрая, дешёвая",
    ),
    "openai/gpt-5.6-luna": ModelInfo(
        id="openai/gpt-5.6-luna",
        name="GPT-5.6 Luna",  # TODO(meta): сверить name/context/pricing с дампом
        context=0,
        reasoning=True,
        vision=True,
        tools=True,
        reasoning_effort=True,
        web_search_builtin=True,
        input_price=0.0,
        output_price=0.0,
        tag="luna",
    ),
    "z-ai/glm-5.3-flash": ModelInfo(
        id="z-ai/glm-5.3-flash",
        name="GLM 5.3 Flash",
        context=1_310_720,
        reasoning=True,
        vision=True,
        tools=True,
        reasoning_effort=True,
        web_search_builtin=False,
        input_price=1.3,
        output_price=4.1,
        tag="дешёвая",
    ),
    "google/gemini-3.8-flash": ModelInfo(
        id="google/gemini-3.8-flash",
        name="Gemini 3.8 Flash",  # TODO(meta): сверить с дампом
        context=0,
        reasoning=True,
        vision=True,
        tools=True,
        reasoning_effort=True,
        web_search_builtin=True,
        input_price=0.0,
        output_price=0.0,
        tag="gemini-flash",
    ),
    "moonshotai/kimi-k2.7-code": ModelInfo(
        id="moonshotai/kimi-k2.7-code",
        name="Kimi K2.7 Code",
        context=262_144,
        reasoning=True,
        vision=True,
        tools=True,
        reasoning_effort=False,
        web_search_builtin=False,
        input_price=19.0,
        output_price=80.0,
        tag="для кода",
    ),
    "anthropic/claude-sonnet-5.5": ModelInfo(
        id="anthropic/claude-sonnet-5.5",
        name="Claude Sonnet 5.5",  # TODO(meta): сверить с дампом
        context=0,
        reasoning=True,
        vision=True,
        tools=True,
        reasoning_effort=True,
        web_search_builtin=True,
        input_price=0.0,
        output_price=0.0,
        tag="sonnet",
    ),
    "x-ai/grok-4.7": ModelInfo(
        id="x-ai/grok-4.7",
        name="Grok 4.7",  # TODO(meta): сверить с дампом
        context=0,
        reasoning=True,
        vision=True,
        tools=True,
        reasoning_effort=True,
        web_search_builtin=True,
        input_price=0.0,
        output_price=0.0,
        tag="grok",
    ),
    # --- Прочие ранее известные модели (сохранены, чтобы старые id остались
    # валидными, если пользователь вернёт их в CURATED_MODEL_IDS). ---
    "deepseek/deepseek-v4-pro": ModelInfo(
        id="deepseek/deepseek-v4-pro",
        name="DeepSeek V4 Pro",
        context=1_048_576,
        reasoning=True,
        vision=False,             # TODO(capabilities)
        tools=False,              # TODO(capabilities)
        reasoning_effort=False,   # TODO(capabilities)
        web_search_builtin=False,  # TODO(capabilities)
        input_price=35.0,
        output_price=70.0,
        tag="умнее flash",
    ),
    "anthropic/claude-sonnet-4.6": ModelInfo(
        id="anthropic/claude-sonnet-4.6",
        name="Claude Sonnet 4.6",
        context=1_000_000,
        reasoning=True,
        vision=False,             # TODO(capabilities)
        tools=False,              # TODO(capabilities)
        reasoning_effort=False,   # TODO(capabilities)
        web_search_builtin=False,  # TODO(capabilities)
        input_price=80.0,
        output_price=400.0,
        tag="топ для кода",
    ),
    "anthropic/claude-haiku-4.5": ModelInfo(
        id="anthropic/claude-haiku-4.5",
        name="Claude Haiku 4.5",
        context=200_000,
        reasoning=True,
        vision=False,             # TODO(capabilities)
        tools=False,              # TODO(capabilities)
        reasoning_effort=False,   # TODO(capabilities)
        web_search_builtin=False,  # TODO(capabilities)
        input_price=27.0,
        output_price=135.0,
        tag="быстрая Anthropic",
    ),
    "openai/gpt-5-nano": ModelInfo(
        id="openai/gpt-5-nano",
        name="GPT-5 Nano",
        context=400_000,
        reasoning=True,
        vision=False,             # TODO(capabilities)
        tools=False,              # TODO(capabilities)
        reasoning_effort=False,   # TODO(capabilities)
        web_search_builtin=False,  # TODO(capabilities)
        input_price=1.4,
        output_price=11.0,
        tag="очень дешёвая",
    ),
    "openai/gpt-5.4-mini": ModelInfo(
        id="openai/gpt-5.4-mini",
        name="GPT-5.4 Mini",
        context=400_000,
        reasoning=True,
        vision=False,             # TODO(capabilities)
        tools=False,              # TODO(capabilities)
        reasoning_effort=False,   # TODO(capabilities)
        web_search_builtin=False,  # TODO(capabilities)
        input_price=20.0,
        output_price=120.0,
        tag="баланс цена/качество",
    ),
    "openai/gpt-5.4": ModelInfo(
        id="openai/gpt-5.4",
        name="GPT-5.4",
        context=1_050_000,
        reasoning=True,
        vision=False,             # TODO(capabilities)
        tools=False,              # TODO(capabilities)
        reasoning_effort=False,   # TODO(capabilities)
        web_search_builtin=False,  # TODO(capabilities)
        input_price=68.0,
        output_price=400.0,
        tag="топ OpenAI",
    ),
    "google/gemini-3.5-flash": ModelInfo(
        id="google/gemini-3.5-flash",
        name="Gemini 3.5 Flash",
        context=1_048_576,
        reasoning=True,
        vision=False,             # TODO(capabilities)
        tools=False,              # TODO(capabilities)
        reasoning_effort=False,   # TODO(capabilities)
        web_search_builtin=False,  # TODO(capabilities)
        input_price=40.0,
        output_price=240.0,
        tag="длинный контекст",
    ),
    "google/gemini-2.5-flash": ModelInfo(
        id="google/gemini-2.5-flash",
        name="Gemini 2.5 Flash",
        context=1_048_576,
        reasoning=True,
        vision=False,             # TODO(capabilities)
        tools=False,              # TODO(capabilities)
        reasoning_effort=False,   # TODO(capabilities)
        web_search_builtin=False,  # TODO(capabilities)
        input_price=8.0,
        output_price=67.0,
        tag="дешёвый Gemini",
    ),
    "qwen/qwen3-coder-next": ModelInfo(
        id="qwen/qwen3-coder-next",
        name="Qwen3 Coder Next",
        context=262_144,
        reasoning=False,
        vision=False,             # TODO(capabilities)
        tools=False,              # TODO(capabilities)
        reasoning_effort=False,   # TODO(capabilities)
        web_search_builtin=False,  # TODO(capabilities)
        input_price=3.3,
        output_price=22.0,
        tag="для кода",
    ),
}


def _make_placeholder(model_id: str) -> ModelInfo:
    """Заглушка для id, которого нет в `_FALLBACK_CATALOG`.

    Нужна, чтобы CURATED_MODEL_IDS из .env не роняло импорт при опечатке:
    модель попадёт в список с нулевыми метаданными и «безопасными» флагами.
    Используется `model_catalog.ModelCatalog`.
    """
    logger.warning(
        "Model %r не найдена в _FALLBACK_CATALOG — подставлен placeholder "
        "(нет метаданных и capability-флагов)",
        model_id,
    )
    return ModelInfo(
        id=model_id,
        name=model_id,
        context=0,
        reasoning=False,
        vision=False,
        tools=True,
        reasoning_effort=False,
        web_search_builtin=False,
        input_price=0.0,
        output_price=0.0,
        tag="unknown",
    )


def find_model(model_id: str) -> ModelInfo | None:
    """Модель по id из ``model_catalog.catalog`` (fallback → cache → api)."""
    from agent_coder.model_catalog import catalog
    return catalog.get(model_id)


def get_model_name(model_id: str) -> str:
    """Имя модели из ``model_catalog.catalog`` или сам id, если нет."""
    from agent_coder.model_catalog import catalog
    m = catalog.get(model_id)
    return m.name if m else model_id


# Набор известных capability-флагов (соответствуют полям ModelInfo).
CAPABILITIES: tuple[str, ...] = (
    "vision",
    "tools",
    "web_search_builtin",
    "reasoning",
    "reasoning_effort",
    "verbosity",
)


def supports(model_id: str, cap: str) -> bool:
    """Вернёт True, если у модели с данным id включён флаг cap.

    Источник данных — ``model_catalog.catalog`` (fallback → cache → api).
    cap ∈ {"vision", "tools", "web_search_builtin", "reasoning",
    "reasoning_effort"}.
    Для НЕизвестной модели (нет в каталоге) — False.
    Неизвестный cap → ValueError.
    """
    from agent_coder.model_catalog import catalog
    if cap not in CAPABILITIES:
        raise ValueError(f"unknown capability: {cap!r}")
    m = catalog.get(model_id)
    if m is None:
        return False
    return bool(getattr(m, cap))


def capabilities(model_id: str) -> list[str]:
    """Вернёт отсортированный список включённых capability-имён для модели.

    Источник данных — ``model_catalog.catalog`` (fallback → cache → api).
    Для неизвестной модели — пустой список.
    """
    from agent_coder.model_catalog import catalog
    if catalog.get(model_id) is None:
        return []
    return sorted(c for c in CAPABILITIES if supports(model_id, c))


# Полный набор уровней reasoning, который принимает routerai
def feature_badges(m: ModelInfo | None) -> str:
    """Строка с эмодзи-бейджами возможностей модели.

    Порядок: 👁 vision, 🌐 web_search_builtin,
    ⚙/🧠 reasoning (⚙ если reasoning_effort),
    🔧 tools. Возвращает "" для None.
    """
    if m is None:
        return ""
    parts: list[str] = []
    if m.vision:
        parts.append("👁")
    if m.web_search_builtin:
        parts.append("🌐")
    if m.reasoning:
        parts.append("⚙" if m.reasoning_effort else "🧠")
    if m.tools:
        parts.append("🔧")
    return "".join(parts)


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

# Уровни verbosity (длина ответа). RouterAI: low..max.
VERBOSITY_LEVELS = ["low", "medium", "high", "xhigh", "max"]
DEFAULT_VERBOSITY = "medium"
