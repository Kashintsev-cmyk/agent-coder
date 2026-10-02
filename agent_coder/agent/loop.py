"""Агентный цикл: LLM → tool_call → выполнить → результат → LLM."""
from __future__ import annotations

import inspect
import json
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from agent_coder.agent import compression
from agent_coder.agent.prompts import Mode, system_prompt
from agent_coder.agent.session import Session
from agent_coder.config import config
from agent_coder.llm.client import LLMClient
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
            logger.exception("async event callback %s failed", getattr(fn, "__name__", fn))


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

    async def run(self, task: str) -> None:
        self._refresh_system_prompt()
        self.session.add_message({"role": "user", "content": task})
        self.session.save()

        max_iter = {
            Mode.PLAN: config.MAX_ITERATIONS_PLAN,
            Mode.ACT: config.MAX_ITERATIONS_ACT,
            Mode.AUDIT: config.MAX_ITERATIONS_AUDIT,
        }.get(self.session.mode, config.MAX_ITERATIONS)

        soft = compression.soft_limit(self.session)
        for iteration in range(1, max_iter + 1):
            logger.debug("Итерация %d", iteration)
            fixed = self.session.sanitize_tool_calls()
            if fixed:
                logger.warning("Восстановлено %d dangling tool_calls", fixed)
                self.session.save()
            # B5.2: сжатие истории при превышении порога input-токенов.
            if (
                self.session.last_input_tokens > soft
                and await compression.compress(self.session, self.client)
            ):
                self.session.save()

            try:
                response = await self.client.chat(
                    messages=self.session.messages,
                    model=self.session.model,
                    tools=self.registry.openai_schemas(self.session.mode),
                    reasoning_effort=self.session.reasoning_effort,
                    web_search=self.session.web_search,
                    service_tier=self.session.service_tier or config.SERVICE_TIER,
                    verbosity=self.session.verbosity or None,
                )
            except Exception as e:
                await _maybe_await(self.events.on_error, f"LLM ошибка: {e}")
                self.session.save()
                return

            self.session.add_usage(response.usage)
            await _maybe_await(self.events.on_usage, response.usage)

            # B5.1: собираем статистику для будущего сжатия истории.
            self.session.last_input_tokens = response.usage.input_tokens
            _log_history_threshold(self.session)

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
                return

            for tc in msg.tool_calls:
                await self._handle_tool_call(tc)

            self.session.save()

        await _maybe_await(
            self.events.on_error,
            f"⏸ Достигнут лимит итераций ({max_iter}). "
            f"Прогресс сохранён. Напиши «продолжи» — я продолжу с этого места.",
        )
        self.session.save()

    async def _handle_tool_call(self, tc: ToolCall) -> None:
        await _maybe_await(self.events.on_tool_call, tc)

        tool = self.registry.get(tc.name)
        if tool is None:
            result = f"Ошибка: инструмент '{tc.name}' не найден"
            self._append_tool_result(tc.id, result)
            await _maybe_await(self.events.on_tool_result, tc, result)
            return

        if not self.registry.is_call_allowed(self.session.mode, tool, tc.arguments):
            mode_name = self.session.mode.value
            result = (
                f"Ошибка: инструмент '{tc.name}' недоступен в {mode_name}-режиме "
                f"с такими аргументами. "
                f"{'В Plan/Audit разрешены только read-only команды.' if mode_name in ('plan','audit') else ''}"
            )
            self._append_tool_result(tc.id, result)
            await _maybe_await(self.events.on_tool_result, tc, result)
            return

        if tool.needs_approval(tc.arguments) and not self.yolo:
            approved = await self.events.request_approval(tc)
            if not approved:
                result = "Отклонено пользователем"
                self._append_tool_result(tc.id, result)
                await _maybe_await(self.events.on_tool_result, tc, result)
                return

        ctx = ToolContext(
            workspace=self.session.workspace,
            mode=self.session.mode,
            yolo=self.yolo,
        )
        try:
            result = await tool.executor(tc.arguments, ctx)
        except Exception as e:
            result = f"Ошибка выполнения инструмента: {e}"
            logger.exception("tool %s failed", tc.name)

        self._append_tool_result(tc.id, result)
        await _maybe_await(self.events.on_tool_result, tc, result)
        self.session.save()

    def _append_tool_result(self, tool_call_id: str, result: str) -> None:
        self.session.add_message({
            "role": "tool",
            "tool_call_id": tool_call_id,
            "content": result,
        })

    def _refresh_system_prompt(self) -> None:
        prompt = system_prompt(self.session.mode, self.session.workspace)
        if not self.session.messages or self.session.messages[0].get("role") != "system":
            self.session.messages.insert(0, {"role": "system", "content": prompt})
        else:
            self.session.messages[0] = {"role": "system", "content": prompt}


def _log_history_threshold(session) -> None:
    """B5.1: логировать, если input_tokens превысил порог сжатия.

    Сжатие не выполняем — только сигнал в лог. Порог:
    min(model.context * 0.6, config.MAX_HISTORY_TOKENS).
    """
    from agent_coder.model_catalog import catalog
    m = catalog.get(session.model)
    soft = config.MAX_HISTORY_TOKENS
    if m and m.context:
        soft = min(int(m.context * 0.6), config.MAX_HISTORY_TOKENS)
    if session.last_input_tokens > soft:
        logger.warning(
            "Порог истории превышен: %d > %d (модель %s) — кандидат на сжатие",
            session.last_input_tokens, soft, session.model,
        )

