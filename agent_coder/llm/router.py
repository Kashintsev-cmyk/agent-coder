"""Роутер моделей: дефолт по режиму сессии + fallback-цепочки при 429/timeout.

Логика (вариант «б» — ручной выбор приоритетен):

- Если пользователь выбрал модель вручную (``/model`` или меню сессии) —
  роутер её НЕ трогает. Работает только fallback-цепочка внутри
  ``MODEL_IDS`` (при 429/timeout).
- Если модель не выбрана вручную (``model_manual=False``) и включён
  ``MODEL_ROUTER_ENABLED`` — при старте задачи подставляется
  ``RECOMMENDED_MODEL_BY_MODE[mode]``, если она влезает по контексту;
  иначе — первая ВЛЕЗАЮЩАЯ из единого ``MODEL_IDS`` (порядок = приоритет).

Fallback-цепочка: ``MODEL_IDS`` (порядок = приоритет), выбранная модель
всегда первая, остальные — в порядке ``MODEL_IDS`` с фильтром по контексту.

Проверка размера контекста: перед выбором/переключением модели роутер
сверяет оценку входных токенов с ``context_limit * CONTEXT_FILL_RATIO``.
Модели, в которые задача не влезает, пропускаются (иначе diff обрежется
или провайдер вернёт ошибку). Поэтому DeepSeek R1 (64k) стоит последним
в audit-цепочке и получает только небольшие задачи.

Fallback срабатывает на 429 (RateLimitError) и timeout (APITimeoutError);
клиент (`llm.client`) уже делает retry с backoff — роутер подключается,
когда retry исчерпан или ошибка не ретраибельна.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

from openai import APIConnectionError, APITimeoutError, RateLimitError

from agent_coder.config import config
from agent_coder.models import ModelInfo

logger = logging.getLogger(__name__)

# Ошибки, по которым имеет смысл пробовать следующую модель цепочки.
FALLBACK_ERRORS: tuple[type[Exception], ...] = (
    RateLimitError,  # 429
    APITimeoutError,  # таймаут запроса
    APIConnectionError,  # сеть недоступна (включает APITimeoutError)
)


@dataclass
class RouterState:
    """Состояние роутера в рамках одной задачи (AgentLoop)."""

    # Модель, выбранная пользователем вручную (None — не выбрана).
    manual_model: str | None = None
    # Оценка входных токенов задачи (для проверки влезания в context_limit).
    input_tokens: int = 0
    # Индекс текущей модели в fallback-цепочке.
    chain_index: int = 0
    # История переключений для отчёта пользователю.
    switches: list[str] = field(default_factory=list)

    @property
    def current(self) -> str | None:
        return self.manual_model


class ModelRouter:
    """Выбор модели по режиму + fallback-цепочка при 429/timeout."""

    def __init__(self) -> None:
        self._models: list[str] = list(config.MODEL_IDS)

    # --- контекст ---

    def max_input_tokens(self, model_id: str) -> int | None:
        """Сколько входных токенов модель принимает с учётом запаса.

        ``context_limit * CONTEXT_FILL_RATIO`` (запас на ответ и на
        неточность оценки токенов). None — метаданных о модели нет.
        """
        info = self.info(model_id)
        if info is None or not info.context:
            return None
        return int(info.context * config.CONTEXT_FILL_RATIO)

    def fits_context(self, model_id: str, input_tokens: int = 0) -> bool:
        """Влезает ли задача (input_tokens) в контекст модели.

        Нет данных о модели или неизвестный размер задачи — считаем, что
        влезает (не блокируем работу из-за отсутствия метаданных).
        """
        if input_tokens <= 0:
            return True
        limit = self.max_input_tokens(model_id)
        if limit is None:
            return True
        return input_tokens <= limit

    def filter_by_context(
        self, model_ids: list[str], input_tokens: int = 0
    ) -> list[str]:
        """Отбросить модели, в которые задача не влезает (с логом)."""
        kept: list[str] = []
        for mid in model_ids:
            if self.fits_context(mid, input_tokens):
                kept.append(mid)
            else:
                logger.info(
                    "Модель %s пропущена: ~%d токенов > лимит %s",
                    mid,
                    input_tokens,
                    self.max_input_tokens(mid),
                )
        return kept

    # --- выбор модели ---

    def resolve(
        self,
        mode: str,
        manual_model: str | None = None,
        input_tokens: int = 0,
    ) -> str | None:
        """Какую модель использовать для задачи.

        Приоритет: ручной выбор > recommended для режима (если влезает) >
        первая влезающая из ``MODEL_IDS`` > первая ``MODEL_IDS``.
        Ручной выбор не переопределяется даже при превышении контекста
        (пользователь мог сознательно выбрать конкретную модель).
        """
        if manual_model:
            return manual_model
        if not config.MODEL_ROUTER_ENABLED:
            return None
        # 1) recommendation при создании/смене режима — часть выбора
        #    модели, поэтому живёт в ядре (AGENTS.md). Не влезает — пропускаем.
        recommended = config.RECOMMENDED_MODEL_BY_MODE.get(mode)
        if recommended and self.fits_context(recommended, input_tokens):
            return recommended
        # 2) первая влезающая из MODEL_IDS (порядок = приоритет).
        for mid in self._models:
            if self.fits_context(mid, input_tokens):
                return mid
        # 3) ни одна не влезает — лучше попробовать первую, чем не отвечать
        #    (провайдер вернёт переполнение, fallback переключит).
        return self._models[0] if self._models else None

    def chain_for(
        self,
        model_id: str,
        mode: str,
        input_tokens: int = 0,
    ) -> list[str]:
        """Fallback-цепочка для модели: ``MODEL_IDS`` (порядок = приоритет).

        Выбранная модель всегда первая (это текущая модель задачи), далее —
        остальные ``MODEL_IDS`` в исходном порядке, КРОМЕ тех, в чей
        context_limit задача не влезает. Если модель вне ``MODEL_IDS`` —
        цепочка из одного элемента.
        """
        if model_id not in self._models:
            return [model_id]
        rest = self.filter_by_context(
            [m for m in self._models if m != model_id], input_tokens
        )
        return [model_id, *rest]

    # --- fallback при ошибке ---

    def next_on_error(
        self,
        state: RouterState,
        mode: str,
        error: Exception,
    ) -> str | None:
        """Следующая модель цепочки, если ошибка фолбэчная, иначе None.

        Модели, в которые задача не влезает, из цепочки исключены
        (см. ``chain_for``). Мутирует state: двигает chain_index, пишет
        в switches.
        """
        if not isinstance(error, FALLBACK_ERRORS):
            return None
        base = state.manual_model
        if base is None:
            base = self.resolve(mode, input_tokens=state.input_tokens)
        if base is None:
            return None
        chain = self.chain_for(base, mode, state.input_tokens)
        state.chain_index += 1
        if state.chain_index >= len(chain):
            logger.warning(
                "Fallback-цепочка исчерпана (%s), ошибка: %s",
                " → ".join(chain),
                error,
            )
            return None
        nxt = chain[state.chain_index]
        state.switches.append(f"{chain[state.chain_index - 1]} → {nxt}")
        logger.warning(
            "Fallback модели (%s): %s", type(error).__name__, state.switches[-1]
        )
        return nxt

    # --- метаданные ---

    def info(self, model_id: str) -> ModelInfo | None:
        """Метаданные модели из каталога (для UI/логов)."""
        from agent_coder.model_catalog import catalog

        return catalog.get(model_id)


# Глобальный синглтон.
router = ModelRouter()
