"""Агентный цикл: LLM → tool_call → выполнить → результат → LLM."""

from __future__ import annotations

import base64
import inspect
import json
import logging
import mimetypes
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from agent_coder.agent import compression, history_archive, tool_extract
from agent_coder.agent.prompts import Mode, system_prompt
from agent_coder.agent.session import Session
from agent_coder.agent.work_state import WorkStateHandle
from agent_coder.config import config
from agent_coder.llm.client import LLMClient
from agent_coder.llm.router import RouterState, router
from agent_coder.llm.types import ToolCall, Usage
from agent_coder.tools.registry import ToolContext, ToolRegistry

logger = logging.getLogger(__name__)


@dataclass
class LoopEvents:
    """Колбэки для UI. Могут быть и sync, и async — loop сам разберётся."""

    on_assistant_text: Callable[[str], Any]
    on_tool_call: Callable[[ToolCall], Any]
    on_tool_result: Callable[[ToolCall, str], Any]
    on_usage: Callable[[Usage], Any]
    on_error: Callable[[str], Any]
    request_approval: Callable[[ToolCall], Awaitable[bool]]
    # Необязательный: вызывается после успешного сжатия истории.
    # None — UI про сжатие не уведомляем (например, CLI-режим).
    on_compression: Callable[[Any], Any] | None = None


@dataclass
class LoopResult:
    """Итог одного запуска agent-loop."""

    iterations_used: int = 0
    limit_reached: bool = False  # вышли по max_iter
    watchdog_triggered: bool = False  # вышли по MAX_CONSECUTIVE_ERRORS
    consecutive_errors: int = 0  # сколько ошибок было подряд на выходе


async def _maybe_await(fn, *args) -> None:
    """Вызвать колбэк: если он async — await, если sync — просто выполнить."""
    try:
        result = fn(*args)
    except Exception:
        logger.exception("event callback %s failed", getattr(fn, "__name__", fn))
        return
    if inspect.iscoroutine(result):
        try:
            await result
        except Exception:
            logger.exception(
                "async event callback %s failed", getattr(fn, "__name__", fn)
            )


class AgentLoop:
    def __init__(
        self,
        *,
        client: LLMClient,
        registry: ToolRegistry,
        session: Session,
        events: LoopEvents,
        yolo: bool = False,
    ) -> None:
        self.client = client
        self.registry = registry
        self.session = session
        self.events = events
        self.yolo = yolo
        # Состояние fallback-роутера на время одного run().
        self._router_state = RouterState(
            manual_model=session.model if session.model_manual else None,
        )

    async def run(self, task: str, images: list | None = None) -> LoopResult:
        self._refresh_system_prompt()
        content = _build_user_content(task, images)
        self.session.add_message({"role": "user", "content": content})
        self.session.save()

        # Оценка размера задачи: роутер пропускает модели, в которые она
        # не влезает по контексту (защита от обрезки больших diff'ов).
        self._router_state.input_tokens = compression.estimate_tokens(
            self.session.messages
        )

        # Роутер: если модель не выбрана вручную и MODEL_ROUTER_ENABLED —
        # подставляем первую влезающую модель группы режима
        # (AUDIT → audit-модели, иначе coding).
        if not self.session.model_manual and config.MODEL_ROUTER_ENABLED:
            routed = router.resolve(
                self.session.mode.value, input_tokens=self._router_state.input_tokens
            )
            if routed and routed != self.session.model:
                logger.info(
                    "Роутер: %s → %s (режим %s, ~%d токенов)",
                    self.session.model,
                    routed,
                    self.session.mode.value,
                    self._router_state.input_tokens,
                )
                self.session.model = routed

        # В CHAT нет агентного цикла (нет tools) — max_iter не используется,
        # но на всякий случай даём ему безопасное значение.
        max_iter = {
            Mode.PLAN: config.MAX_ITERATIONS_PLAN,
            Mode.ACT: config.MAX_ITERATIONS_ACT,
            Mode.AUDIT: config.MAX_ITERATIONS_AUDIT,
            Mode.CHAT: config.MAX_ITERATIONS,
        }.get(self.session.mode, config.MAX_ITERATIONS)

        soft = compression.soft_limit(self.session)
        consecutive_errors = 0

        for iteration in range(1, max_iter + 1):
            logger.debug("Итерация %d", iteration)
            fixed = self.session.sanitize_tool_calls()
            if fixed:
                logger.warning("Восстановлено %d dangling tool_calls", fixed)
                self.session.save()
            # B5.2: сжатие истории при превышении порога input-токенов.
            if self.session.last_input_tokens > soft:
                comp = await compression.compress(self.session, self.client)
                if comp is not None:
                    self.session.save()
                    if self.events.on_compression is not None:
                        await _maybe_await(self.events.on_compression, comp)

            # Пересобрать system prompt: work_state мог измениться
            # (update_work_state в прошлой итерации) — модель должна видеть
            # актуальные факты, иначе смысл structured memory теряется.
            self._refresh_system_prompt()

            try:
                response = await self.client.chat(
                    messages=self.session.messages,
                    model=self.session.model,
                    tools=self.registry.openai_schemas(self.session.mode),
                    reasoning_effort=self.session.reasoning_effort,
                    web_search=self.session.web_search,
                    service_tier=self.session.service_tier or config.SERVICE_TIER,
                    verbosity=self.session.verbosity or None,
                    temperature=config.TEMPERATURE,
                )
            except Exception as e:
                # Fallback-цепочка: при 429/timeout/сетевой ошибке пробуем
                # следующую модель группы (ручной выбор уважается — цепочка
                # строится от текущей модели).
                nxt = router.next_on_error(
                    self._router_state, self.session.mode.value, e
                )
                if nxt is not None:
                    logger.warning(
                        "Модель %s недоступна (%s) — переключаюсь на %s",
                        self.session.model,
                        type(e).__name__,
                        nxt,
                    )
                    self.session.model = nxt
                    await _maybe_await(
                        self.events.on_error,
                        f"⚠️ Модель недоступна ({type(e).__name__}) — "
                        f"переключился на {nxt}, продолжаю",
                    )
                    # Повторяем ту же итерацию с новой моделью (без записи
                    # user-сообщения — оно уже в истории).
                    iteration -= 1
                    consecutive_errors = 0
                    continue
                await _maybe_await(self.events.on_error, f"LLM ошибка: {e}")
                self.session.save()
                return LoopResult(
                    iterations_used=iteration,
                    consecutive_errors=consecutive_errors,
                )

            self.session.add_usage(response.usage)
            await _maybe_await(self.events.on_usage, response.usage)

            # B5.1: собираем статистику для будущего сжатия истории.
            self.session.last_input_tokens = response.usage.input_tokens

            msg = response.message
            if msg.content:
                await _maybe_await(self.events.on_assistant_text, msg.content)

            assistant_history: dict[str, Any] = {
                "role": "assistant",
                "content": msg.content or "",
            }
            if msg.tool_calls:
                assistant_history["tool_calls"] = [
                    {
                        "id": tc.id,
                        "type": "function",
                        "function": {
                            "name": tc.name,
                            "arguments": json.dumps(tc.arguments, ensure_ascii=False),
                        },
                    }
                    for tc in msg.tool_calls
                ]
            self.session.add_message(assistant_history)

            if not msg.tool_calls:
                self.session.save()
                return LoopResult(
                    iterations_used=iteration,
                    consecutive_errors=consecutive_errors,
                )

            for tc in msg.tool_calls:
                await self._handle_tool_call(tc)

            # Watchdog: считаем подряд идущие ошибочные tool-результаты
            if self.session.messages:
                last_msg = self.session.messages[-1]
                if last_msg.get("role") == "tool":
                    last_content = last_msg.get("content", "")
                    if isinstance(
                        last_content, str
                    ) and last_content.lstrip().startswith("Ошибка"):
                        consecutive_errors += 1
                    else:
                        consecutive_errors = 0

            if consecutive_errors >= config.MAX_CONSECUTIVE_ERRORS:
                await _maybe_await(
                    self.events.on_error,
                    f"⚠️ {consecutive_errors} ошибок подряд — останавливаюсь, "
                    f"чтобы не зациклиться. Последняя: {last_content[:150]}",
                )
                self.session.save()
                return LoopResult(
                    iterations_used=iteration,
                    watchdog_triggered=True,
                    consecutive_errors=consecutive_errors,
                )

            self.session.save()

        # Цикл завершился естественно = упёрлись в лимит
        self.session.save()
        return LoopResult(
            iterations_used=max_iter,
            limit_reached=True,
            consecutive_errors=consecutive_errors,
        )

    async def _handle_tool_call(self, tc: ToolCall) -> None:
        await _maybe_await(self.events.on_tool_call, tc)

        tool = self.registry.get(tc.name)
        if tool is None:
            result = f"Ошибка: инструмент '{tc.name}' не найден"
            self._append_tool_result(tc, result)
            await _maybe_await(self.events.on_tool_result, tc, result)
            return

        if not self.registry.is_call_allowed(self.session.mode, tool, tc.arguments):
            mode_name = self.session.mode.value
            result = (
                f"Ошибка: инструмент '{tc.name}' недоступен в {mode_name}-режиме "
                f"с такими аргументами. "
                f"{'В Plan/Audit разрешены только read-only команды.' if mode_name in ('plan', 'audit') else ''}"
            )
            self._append_tool_result(tc, result)
            await _maybe_await(self.events.on_tool_result, tc, result)
            return

        if tool.needs_approval(tc.arguments) and not self.yolo:
            approved = await self.events.request_approval(tc)
            if not approved:
                result = "Отклонено пользователем"
                self._append_tool_result(tc, result)
                await _maybe_await(self.events.on_tool_result, tc, result)
                return

        ctx = ToolContext(
            workspace=self.session.workspace,
            mode=self.session.mode,
            yolo=self.yolo,
            session_id=self.session.id,
            work_state=self._work_state_handle(),
        )
        try:
            result = await tool.executor(tc.arguments, ctx)
        except Exception as e:
            result = f"Ошибка выполнения инструмента: {e}"
            logger.exception("tool %s failed", tc.name)

        self._append_tool_result(tc, result)
        await _maybe_await(self.events.on_tool_result, tc, result)

    def _append_tool_result(self, tc: ToolCall, result: str) -> None:
        """Полный результат — в архив, extracted — в messages, затем save.

        Полный текст уходит в history_archive (его ищет search_history),
        чтобы модель могла достать детали. В session.messages кладём
        детерминированно сжатую версию (см. tool_extract).

        save() внутри — чтобы error-path (tool is None, not allowed,
        rejected) тоже фиксировался на диске, а не только success.
        """
        full_msg = {"role": "tool", "tool_call_id": tc.id, "content": result}
        history_archive.append_messages(self.session.id, [full_msg])
        extracted = tool_extract.extract(
            tc.name, tc.arguments, result, tool_extract.DEFAULT_MAX_CHARS
        )
        self.session.add_message(
            {
                "role": "tool",
                "tool_call_id": tc.id,
                "content": extracted,
            }
        )
        self.session.save()

    def _work_state_handle(self) -> WorkStateHandle:
        """Узкий handle над живым session.work_state (не копия).

        Создаётся на месте — обёртка дешёвая, а ссылка на живой dict
        гарантирует, что tool и system prompt видят один и тот же state.
        """
        return WorkStateHandle(self.session.work_state)

    def _refresh_system_prompt(self) -> None:
        prompt = system_prompt(
            self.session.mode,
            self.session.workspace,
            self._work_state_handle(),
        )
        if (
            not self.session.messages
            or self.session.messages[0].get("role") != "system"
        ):
            self.session.messages.insert(0, {"role": "system", "content": prompt})
        else:
            self.session.messages[0] = {"role": "system", "content": prompt}


def _build_user_content(task: str, images: list | None) -> str | list[dict[str, Any]]:
    """Собрать content для user-сообщения.

    Если images нет — просто текст. Если есть — multimodal блоки:
    [{"type":"text","text":task}, {"type":"image_url","image_url":{"url":"data:..."}}]
    """
    if not images:
        return task
    blocks: list[dict[str, Any]] = [{"type": "text", "text": task}]
    for path in images:
        try:
            raw = Path(path).read_bytes()
        except OSError as e:
            logger.warning("Не удалось прочитать изображение %s: %s", path, e)
            continue
        mime, _ = mimetypes.guess_type(str(path))
        mime = mime or "image/jpeg"
        b64 = base64.b64encode(raw).decode("ascii")
        blocks.append(
            {
                "type": "image_url",
                "image_url": {"url": f"data:{mime};base64,{b64}"},
            }
        )
    return blocks
