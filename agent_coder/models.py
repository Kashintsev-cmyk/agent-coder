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

Цены для моделей из `MODEL_IDS` синхронизированы с API и покрыты регрессом
`tests/test_fallback_prices.py`. Для моделей вне `MODEL_IDS` (доступны только
через ручной `/model`) цены в `_FALLBACK_CATALOG` могут устаревать — это
приемлемо: они не участвуют в бюджетах и рекомендациях, а при необходимости
подтягиваются из API-кэша `model_catalog`.

Разница между `reasoning` и `reasoning_effort`:
- `reasoning`        — модель умеет «думать» (chain-of-thought) вообще.
- `reasoning_effort` — модель принимает ГРАДАЦИЮ усилий (`none`..`ultra`),
  т.е. позволяет управлять глубиной размышлений. Инвариант: если
  `reasoning_effort=True`, то и `reasoning=True` (градация без самого
  reasoning-режима невозможна).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

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
    description: str = ""  # краткое описание от RouterAI (RU)
    input_price: float = 0.0  # ₽ за 1M
    output_price: float = 0.0  # ₽ за 1M
    # Цена чтения из кэша / записи в кэш, ₽ за 1M. 0.0 — кэш у модели не
    # тарифицируется (или RouterAI не отдал цену).
    cache_read_price: float = 0.0
    cache_write_price: float = 0.0
    # Long-context тир провайдера (RouterAI: variable_pricings,
    # type="prompt-threshold"). Выше порога пересчитывается ВСЯ ставка, а не
    # только превышение. Источник — /models/{id}/endpoints, в /models тиров нет.
    #   None  — данных нет (endpoints недоступны) → legacy-константы;
    #   0     — тира у модели нет (перепроверено по API);
    #   >0    — порог в токенах + множители для входа и выхода.
    tier_threshold: int | None = None
    tier_input_factor: float = 1.0
    tier_output_factor: float = 1.0
    # Лимит выхода провайдера (endpoints.max_completion_tokens). 0 — неизвестен.
    max_completion_tokens: int = 0
    # Лимит ВХОДА провайдера (endpoints.max_prompt_tokens). 0 — неизвестен.
    # Может быть меньше context_length: у gpt-6* context=1_050_000, а
    # max_prompt=922_000 — 128K зарезервировано под ответ.
    max_prompt_tokens: int = 0
    # Тариф «время суток» (RouterAI: variable_pricings, type="time-of-day").
    # Окно: (дни_utc | None, час_от, час_до, вход_множитель, выход_множитель,
    #        база_провайдера_₽/1M). None в днях = все дни; час_до=24 = до конца
    # суток. База нужна потому, что окна принадлежат КОНКРЕТНОМУ провайдеру
    # (BytePlus/DeepSeek/Alibaba), а цена в /models — часто другого
    # (в 2026-11-21 базы не совпали ни у одной из 8 моделей). Множитель
    # применяется только к своей базе, иначе получилась бы выдуманная цифра.
    tod_windows: tuple[
        tuple[tuple[str, ...] | None, int, int, float, float, float], ...
    ] = ()
    # B7: ставки ВСЕХ провайдеров модели из /models/{id}/endpoints:
    # provider_name -> (вход, выход, кэш-чтение) в ₽/1M.
    # Зачем: `input_price` выше — цена из /models, она принадлежит ОДНОМУ
    # провайдеру (часто не тому, кто отвечает). RouterAI кладёт фактического
    # провайдера в тело ответа (`provider`, probe 2026-11-22: BytePlus), и
    # только по этой карте можно взять ЕГО ставку. Окна `tod_windows`
    # сопоставляются с провайдером по базе (шестое поле окна).
    provider_prices: dict[str, tuple[float, float, float]] = field(default_factory=dict)
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
    "deepseek/deepseek-v4-flash": ModelInfo(
        id="deepseek/deepseek-v4-flash",
        name="DeepSeek V4 Flash",
        context=1_048_576,
        reasoning=True,
        vision=True,
        tools=True,
        reasoning_effort=True,
        web_search_builtin=False,
        # 2026-11-03: не в MODEL_IDS — ручной /model. Цены сверены с API.
        input_price=10.00,
        output_price=20.00,
        tag="ручной /model (не в MODEL_IDS)",
    ),
    "deepseek/deepseek-v4.1-flash": ModelInfo(
        id="deepseek/deepseek-v4.1-flash",
        name="DeepSeek V4.1 Flash",
        context=1_048_576,
        reasoning=True,
        vision=True,  # input_modalities: text+image
        tools=True,
        reasoning_effort=True,
        web_search_builtin=False,
        # 2026-11-03: DEFAULT + PATCH + audit fallback. Цены сверены с API.
        input_price=3.82,
        output_price=15.28,
        tag="default, patch, audit fallback",
    ),
    "z-ai/glm-5.3-flash": ModelInfo(
        id="z-ai/glm-5.3-flash",
        name="GLM 5.3 Flash",
        context=1_048_576,
        reasoning=True,
        vision=True,  # input_modalities: text+image+video
        tools=True,
        reasoning_effort=True,
        web_search_builtin=False,
        input_price=5.21,
        output_price=17.37,
        tag="дешёвая",
    ),
    "qwen/qwen3-coder-next": ModelInfo(
        id="qwen/qwen3-coder-next",
        name="Qwen3 Coder Next",
        context=262_144,
        reasoning=False,
        vision=False,  # input_modalities: text
        tools=True,  # supported_parameters: tools
        reasoning_effort=False,
        web_search_builtin=False,
        input_price=13.07,
        output_price=87.11,
        tag="для кода",
    ),
    "z-ai/glm-5.3": ModelInfo(
        id="z-ai/glm-5.3",
        name="GLM 5.3",
        context=1_048_576,
        reasoning=True,
        vision=False,
        tools=True,
        reasoning_effort=True,
        web_search_builtin=False,
        input_price=54.44,
        output_price=163.88,
        tag="рассуждения, код",
    ),
    "moonshotai/kimi-k2.7-code": ModelInfo(
        id="moonshotai/kimi-k2.7-code",
        name="Kimi K2.7 Code",
        context=262_144,
        reasoning=True,
        vision=True,  # input_modalities: text+image
        tools=True,
        reasoning_effort=False,
        web_search_builtin=False,
        input_price=77.58,
        output_price=326.66,
        tag="для кода",
    ),
    "openai/gpt-6.1-sol": ModelInfo(
        id="openai/gpt-6.1-sol",
        name="GPT-6.1 Sol",
        context=1_050_000,
        reasoning=True,
        vision=True,
        tools=True,
        reasoning_effort=True,
        web_search_builtin=True,
        verbosity=True,
        input_price=213.59,
        output_price=1067.97,
        tag="агентный кодинг",
    ),
    "openai/gpt-6.1-terra": ModelInfo(
        id="openai/gpt-6.1-terra",
        name="GPT-6.1 Terra",
        context=1_050_000,
        reasoning=True,
        vision=True,
        tools=True,
        reasoning_effort=False,
        web_search_builtin=True,
        verbosity=True,
        input_price=106.84843,
        output_price=534.24215,
        cache_read_price=5.342422,
        # prompt-threshold >272K: вход ×2, выход ×1.5 (как у terra-pro).
        tier_threshold=272_000,
        tier_input_factor=2.0,
        tier_output_factor=1.5,
        tag="chat_bot balanced-tier",
    ),
    # --- Прочие ранее известные модели (сохранены, чтобы старые id остались
    # валидными, если пользователь вернёт их в CURATED_MODEL_IDS). ---
    "deepseek/deepseek-v4.1-flash-1017": ModelInfo(
        id="deepseek/deepseek-v4.1-flash-1017",
        name="DeepSeek V4.1 Flash",
        context=1_048_576,
        reasoning=True,
        vision=True,
        tools=True,
        # 2026-11-21: /endpoints — reasoning_effort 11/49. Частичная поддержка:
        # профиль (profiles.py) намеренно шлёт "high", часть провайдеров его
        # применяет. Флаг не трогаем, чтобы не сломать калибровку B9.2e.
        reasoning_effort=True,
        web_search_builtin=False,
        input_price=5.288997,
        output_price=29.383318,
        cache_read_price=0.293833,
        # 0 = тиров нет (проверено по 49 endpoints). Legacy ×5 неприменим.
        tier_threshold=0,
        tag="chat_bot fast-tier",
    ),
    "anthropic/claude-haiku-5.5": ModelInfo(
        id="anthropic/claude-haiku-5.5",
        name="Claude Haiku 5.5",
        context=1_000_000,
        reasoning=True,
        vision=True,
        tools=True,
        # 2026-11-21: /endpoints — reasoning_effort 0/5. Агрегат /models врёт.
        reasoning_effort=False,
        web_search_builtin=True,
        verbosity=True,
        input_price=10.684843,
        output_price=53.424215,
        cache_read_price=1.068484,
        # prompt-threshold >100K: вход и выход ×5.
        tier_threshold=100_000,
        tier_input_factor=5.0,
        tier_output_factor=5.0,
        tag="chat_bot fast-tier; audit/verify/summary",
    ),
    "openai/gpt-6-luna-pro": ModelInfo(
        id="openai/gpt-6-luna-pro",
        name="GPT-6 Luna Pro",
        context=1_050_000,
        reasoning=True,
        vision=True,
        tools=True,
        # 2026-11-21: /endpoints — reasoning_effort 0/4. Агрегат /models врёт.
        reasoning_effort=False,
        web_search_builtin=True,
        verbosity=True,
        input_price=10.684843,
        output_price=53.424215,
        cache_read_price=1.068484,
        # prompt-threshold >272K: вход ×2, выход ×1.5.
        tier_threshold=272_000,
        tier_input_factor=2.0,
        tier_output_factor=1.5,
        tag="chat_bot fast-tier",
    ),
    "deepseek/deepseek-v4-pro-0813": ModelInfo(
        id="deepseek/deepseek-v4-pro-0813",
        name="DeepSeek V4 Pro",
        context=1_048_576,
        reasoning=True,
        vision=False,
        tools=True,
        # 2026-11-21: /endpoints — reasoning_effort 1/20. Оставляем True ради
        # профиля, но фактически применим лишь на единичных провайдерах.
        reasoning_effort=True,
        web_search_builtin=False,
        input_price=19.74559,
        output_price=59.23677,
        cache_read_price=0.628269,
        # 0 = тиров нет (проверено по 20 endpoints).
        tier_threshold=0,
        tag="chat_bot balanced-tier",
    ),
    "openai/gpt-6.1-terra-pro": ModelInfo(
        id="openai/gpt-6.1-terra-pro",
        name="GPT-6.1 Terra Pro",
        context=1_050_000,
        reasoning=True,
        vision=True,
        tools=True,
        # 2026-11-21: /endpoints — reasoning_effort 0/4. Агрегат /models врёт.
        reasoning_effort=False,
        web_search_builtin=True,
        verbosity=True,
        input_price=106.84843,
        output_price=534.24215,
        cache_read_price=5.342422,
        # prompt-threshold >272K: вход ×2, выход ×1.5.
        tier_threshold=272_000,
        tier_input_factor=2.0,
        tier_output_factor=1.5,
        tag="chat_bot balanced-tier",
    ),
    "anthropic/claude-sonnet-5.5": ModelInfo(
        id="anthropic/claude-sonnet-5.5",
        name="Claude Sonnet 5.5",
        context=1_000_000,
        reasoning=True,
        vision=True,
        tools=True,
        # 2026-11-21: /endpoints — reasoning_effort 0/5. Агрегат /models врёт.
        reasoning_effort=False,
        web_search_builtin=True,
        verbosity=True,
        input_price=213.69686,
        output_price=1068.4843,
        cache_read_price=10.684843,
        # 0 = тиров нет (проверено по 5 endpoints).
        tier_threshold=0,
        tag="chat_bot balanced-tier",
    ),
    "openai/gpt-6.1-sol-pro": ModelInfo(
        id="openai/gpt-6.1-sol-pro",
        name="GPT-6.1 Sol Pro",
        context=1_050_000,
        reasoning=True,
        vision=True,
        tools=True,
        # 2026-11-21: /endpoints — reasoning_effort 0/4. Агрегат /models врёт.
        reasoning_effort=False,
        web_search_builtin=True,
        verbosity=True,
        input_price=213.69686,
        output_price=1068.4843,
        cache_read_price=10.684843,
        # prompt-threshold >272K: вход ×2, выход ×1.5.
        tier_threshold=272_000,
        tier_input_factor=2.0,
        tier_output_factor=1.5,
        tag="chat_bot balanced-tier",
    ),
    "anthropic/claude-opus-5.5": ModelInfo(
        id="anthropic/claude-opus-5.5",
        name="Claude Opus 5.5",
        context=1_000_000,
        reasoning=True,
        vision=True,
        tools=True,
        # 2026-11-21: /endpoints — reasoning_effort 0/6. Агрегат /models врёт.
        reasoning_effort=False,
        web_search_builtin=True,
        verbosity=True,
        input_price=427.39372,
        output_price=2136.9686,
        cache_read_price=21.369686,
        # 0 = тиров нет (проверено по 6 endpoints).
        tier_threshold=0,
        tag="chat_bot premium-tier",
    ),
    "google/gemini-4-argon": ModelInfo(
        id="google/gemini-4-argon",
        name="Google: Gemini 4 Argon",
        context=1_048_576,
        reasoning=True,
        vision=True,
        tools=True,
        reasoning_effort=True,
        web_search_builtin=False,
        verbosity=False,
        input_price=210.93,
        output_price=1054.63,
        tag="аудит (second-pass security)",
    ),
    # 2026-11-03: дешёвые fallback'и из каталога RouterAI (probe OK).
    "qwen/qwen3.7-flash": ModelInfo(
        id="qwen/qwen3.7-flash",
        name="Qwen 3.7 Flash",
        context=1_000_000,
        reasoning=True,
        vision=False,
        tools=True,
        reasoning_effort=True,
        web_search_builtin=False,
        verbosity=False,
        input_price=3.16,
        output_price=13.68,
        tag="plan/act fallback (1M ctx)",
    ),
    "mistralai/mistral-nemo": ModelInfo(
        id="mistralai/mistral-nemo",
        name="Mistral Nemo",
        context=131_072,
        reasoning=False,
        vision=False,
        tools=False,
        reasoning_effort=False,
        web_search_builtin=False,
        verbosity=False,
        input_price=1.74,
        output_price=2.27,
        tag="ручной /model (дешёвый выход)",
    ),
    "openai/gpt-6-luna": ModelInfo(
        id="openai/gpt-6-luna",
        name="OpenAI GPT-6 Luna",
        context=1_050_000,
        reasoning=False,
        vision=False,
        tools=True,
        reasoning_effort=False,
        web_search_builtin=False,
        verbosity=False,
        input_price=10.684843,
        output_price=53.424215,
        cache_read_price=1.068484,
        tag="plan/act fallback (drop-in Haiku)",
    ),
    "x-ai/grok-4.20": ModelInfo(
        id="x-ai/grok-4.20",
        name="SpaceXAI: Grok 4.20",
        context=2_000_000,
        reasoning=True,
        vision=True,
        tools=True,
        reasoning_effort=False,
        web_search_builtin=True,
        description=(
            "Grok 4.20 — новая флагманская модель xAI: высокая скорость, "
            "агентский вызов инструментов, низкий уровень галлюцинаций."
        ),
        input_price=135.8787625,
        output_price=271.757525,
        tag="grok",
    ),
    "deepseek/deepseek-v4-pro": ModelInfo(
        id="deepseek/deepseek-v4-pro",
        name="DeepSeek V4 Pro",
        context=1_048_576,
        reasoning=True,
        vision=False,
        tools=False,
        reasoning_effort=False,
        web_search_builtin=False,
        input_price=35.0,
        output_price=70.0,
        tag="умнее flash",
    ),
    "anthropic/claude-sonnet-4.6": ModelInfo(
        id="anthropic/claude-sonnet-4.6",
        name="Claude Sonnet 4.6",
        context=1_000_000,
        reasoning=True,
        vision=True,
        tools=True,
        reasoning_effort=True,
        web_search_builtin=True,
        input_price=320.39,
        output_price=1601.96,
        tag="топ для кода",
    ),
    "anthropic/claude-haiku-4.5": ModelInfo(
        id="anthropic/claude-haiku-4.5",
        name="Claude Haiku 4.5",
        context=200_000,
        reasoning=True,
        vision=True,
        tools=True,
        reasoning_effort=False,
        web_search_builtin=True,
        input_price=106.8,
        output_price=533.99,
        tag="быстрая Anthropic",
    ),
    "google/gemini-2.5-flash": ModelInfo(
        id="google/gemini-2.5-flash",
        name="Gemini 2.5 Flash",
        context=1_048_576,
        reasoning=True,
        vision=False,
        tools=False,
        reasoning_effort=False,
        web_search_builtin=False,
        input_price=8.0,
        output_price=67.0,
        tag="дешёвый Gemini",
    ),
}


def _make_placeholder(model_id: str, *, warn: bool = True) -> ModelInfo:
    """Заглушка для id, которого нет в `_FALLBACK_CATALOG`.

    Нужна, чтобы CURATED_MODEL_IDS из .env не роняло импорт при опечатке:
    модель попадёт в список с нулевыми метаданными и «безопасными» флагами.
    Используется `model_catalog.ModelCatalog`.
    """
    if warn:
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
