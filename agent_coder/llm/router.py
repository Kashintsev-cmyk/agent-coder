"""Роутер моделей: дефолт по режиму сессии + fallback-цепочки при 429/timeout.

Логика (вариант «б» — ручной выбор приоритетен):

- Если пользователь выбрал модель вручную (``/model`` или меню сессии) —
  роутер её НЕ трогает. Работает только fallback-цепочка внутри группы
  выбранной модели (при 429/timeout).
- Если модель не выбрана вручную (``model_manual=False``) и включён
  ``MODEL_ROUTER_ENABLED`` — при старте задачи подставляется дефолт группы
  по режиму: AUDIT → ``AUDIT_MODEL_IDS[0]``, иначе → ``CODING_MODEL_IDS[0]``.

Fallback-цепочки (порядок = приоритет):
- coding: deepseek/deepseek-v4.1-flash → z-ai/glm-5.3-flash → qwen/qwen3-coder-next
- audit:  deepseek/deepseek-r1 → z-ai/glm-5.3 → moonshotai/kimi-k2.7-code

Fallback срабатывает на 429 (RateLimitError) и timeout (APITimeoutError);
клиент (`llm.client`) уже делает retry с backoff — роутер подключается,
когда retry исчерпан или ошибка не ретраебельна.
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
    RateLimitError,       # 429
    APITimeoutError,      # таймаут запроса
    APIConnectionError,   # сеть недоступна (включает APITimeoutError)
)


@dataclass
class RouterState:
    """Состояние роутера в рамках одной задачи (AgentLoop)."""

    # Модель, выбранная пользователем вручную (None — не выбрана).
    manual_model: str | None = None
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
        self._coding: list[str] = list(config.CODING_MODEL_IDS)
        self._audit: list[str] = list(config.AUDIT_MODEL_IDS)

    # --- группы ---

    def group_for_mode(self, mode: str) -> list[str]:
        """Группа моделей для режима ('audit' → audit-группа, иначе coding)."""
        return self._audit if mode == "audit" else self._coding

    def default_for_mode(self, mode: str) -> str | None:
        """Дефолтная модель режима (первая в группе) или None, если группа пуста."""
        group = self.group_for_mode(mode)
        return group[0] if group else None

    # --- выбор модели ---

    def resolve(
        self,
        mode: str,
        manual_model: str | None = None,
    ) -> str | None:
        """Какую модель использовать для задачи.

        Приоритет: ручной выбор > дефолт группы режима > None.
        """
        if manual_model:
            return manual_model
        if config.MODEL_ROUTER_ENABLED:
            return self.default_for_mode(mode)
        return None

    def chain_for(self, model_id: str, mode: str) -> list[str]:
        """Fallback-цепочка для модели: её группа (порядок = приоритет).

        Выбранная модель ставится на первое место, далее — остальные
        модели её группы в исходном порядке. Если модель вне групп —
        цепочка из одного элемента.
        """
        group = self._audit if model_id in self._audit else self._coding
        if model_id not in group:
            return [model_id]
        rest = [m for m in group if m != model_id]
        return [model_id, *rest]

    # --- fallback при ошибке ---

    def next_on_error(
        self,
        state: RouterState,
        mode: str,
        error: Exception,
    ) -> str | None:
        """Следующая модель цепочки, если ошибка фолбэчная, иначе None.

        Мутирует state: двигает chain_index, пишет в switches.
        """
        if not isinstance(error, FALLBACK_ERRORS):
            return None
        base = state.manual_model
        if base is None and config.MODEL_ROUTER_ENABLED:
            base = self.default_for_mode(mode)
        if base is None:
            return None
        chain = self.chain_for(base, mode)
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
