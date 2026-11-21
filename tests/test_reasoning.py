"""Тесты вывода рассуждений модели и метрик reasoning в футере.

RouterAI отдаёт chain-of-thought в ``message.model_extra["reasoning"]``
(живой probe 2026-11-22, deepseek-v4.1-flash). В SDK такого поля нет,
поэтому извлечение — отдельной функцией, а показ — необязательным
колбэком ``LoopEvents.on_reasoning``.
"""

from __future__ import annotations

from types import SimpleNamespace

from agent_coder.agent.loop import AgentLoop
from agent_coder.llm.client import _extract_reasoning
from agent_coder.llm.types import AssistantMessage, Usage
from agent_coder.telegram import renderer


class TestRenderReasoning:
    def test_empty_returns_empty_string(self):
        # Пустой блок в буфере дал бы лишний перевод строки в сообщении.
        assert renderer.render_reasoning("") == ""
        assert renderer.render_reasoning("   \n\t ") == ""

    def test_wrapped_in_expandable_blockquote(self):
        out = renderer.render_reasoning("думаю про 17*23")
        assert out == "\n<blockquote expandable>💭 думаю про 17*23</blockquote>"

    def test_escapes_html_but_keeps_markdown_verbatim(self):
        # Рассуждения — черновик модели: markdown не рендерим, но HTML
        # экранировать обязательно, иначе Telegram отвергнет сообщение.
        out = renderer.render_reasoning("**x** <b>y</b> & z")
        assert "&lt;b&gt;y&lt;/b&gt;" in out
        assert "&amp;" in out
        assert "**x**" in out
        assert "<b>" not in out

    def test_strips_outer_whitespace(self):
        out = renderer.render_reasoning("\n\n  мысль  \n\n")
        assert out == "\n<blockquote expandable>💭 мысль</blockquote>"


class TestRenderUsageReasoning:
    def test_reasoning_tokens_shown(self):
        out = renderer.render_usage(
            Usage(input_tokens=10, output_tokens=5, reasoning_tokens=7)
        )
        assert "🧠 7" in out

    def test_reasoning_tokens_hidden_when_zero(self):
        # Claude через RouterAI всегда отдаёт 0 — пустой значок не нужен.
        out = renderer.render_usage(Usage(input_tokens=10, output_tokens=5))
        assert "🧠" not in out

    def test_cache_write_shown(self):
        # Прогрев префикса — метрика: без неё непонятно, почему ход дешевле.
        out = renderer.render_usage(
            Usage(input_tokens=10, output_tokens=5, cache_write_tokens=42)
        )
        assert "write 42" in out

    def test_all_footer_fields_together(self):
        out = renderer.render_usage(
            Usage(
                input_tokens=1,
                output_tokens=2,
                cache_read_tokens=3,
                cache_write_tokens=4,
                reasoning_tokens=5,
                cost=0.5,
            )
        )
        for expected in ("in 1", "out 2", "cache 3", "write 4", "🧠 5"):
            assert expected in out
        assert "💰" in out


class TestExtractReasoning:
    def _msg(self, **extra):
        return SimpleNamespace(model_extra=extra)

    def test_reads_reasoning_key(self):
        msg = self._msg(reasoning="  We need answer one number.  ")
        assert _extract_reasoning(msg) == "We need answer one number."

    def test_falls_back_to_reasoning_content(self):
        # Так поле называют некоторые провайдеры/старые версии роутера.
        msg = self._msg(reasoning_content="альтернативное имя")
        assert _extract_reasoning(msg) == "альтернативное имя"

    def test_reasoning_key_wins(self):
        msg = self._msg(reasoning="основное", reasoning_content="запасное")
        assert _extract_reasoning(msg) == "основное"

    def test_no_model_extra(self):
        assert _extract_reasoning(SimpleNamespace()) == ""

    def test_none_model_extra(self):
        assert _extract_reasoning(SimpleNamespace(model_extra=None)) == ""

    def test_blank_or_non_string_value(self):
        assert _extract_reasoning(self._msg(reasoning="   ")) == ""
        assert _extract_reasoning(self._msg(reasoning=None)) == ""
        assert _extract_reasoning(self._msg(reasoning={"x": "dict"})) == ""


def _events(on_reasoning):
    """LoopEvents с одним нужным полем.

    Полный конструктор тянет request_approval и прочие обязательные
    колбэки, а _emit_reasoning читает только on_reasoning.
    """
    return SimpleNamespace(on_reasoning=on_reasoning)


class TestEmitReasoning:
    """_emit_reasoning вызывается только из run(); тестируем сам метод.

    Экземпляр AgentLoop тянет за собой сессии, workspace и конфиг — для
    проверки проброса колбэка достаточно объекта с полем events.
    """

    async def test_calls_callback_with_text(self):
        seen: list[str] = []
        loop = SimpleNamespace(events=_events(seen.append))
        msg = AssistantMessage(content="ok", reasoning="мысль")
        await AgentLoop._emit_reasoning(loop, msg)
        assert seen == ["мысль"]

    async def test_async_callback_awaited(self):
        seen: list[str] = []

        async def cb(text: str) -> None:
            seen.append(text)

        loop = SimpleNamespace(events=_events(cb))
        await AgentLoop._emit_reasoning(
            loop, AssistantMessage(content="", reasoning="м")
        )
        assert seen == ["м"]

    async def test_no_callback_is_noop(self):
        # CLI-режим: on_reasoning не задан — падать не должны.
        loop = SimpleNamespace(events=_events(None))
        await AgentLoop._emit_reasoning(
            loop, AssistantMessage(content="", reasoning="м")
        )

    async def test_empty_reasoning_not_emitted(self):
        seen: list[str] = []
        loop = SimpleNamespace(events=_events(seen.append))
        await AgentLoop._emit_reasoning(loop, AssistantMessage(content="ok"))
        await AgentLoop._emit_reasoning(
            loop, AssistantMessage(content="", reasoning="   ")
        )
        assert seen == []

    async def test_events_without_field_tolerated(self):
        # Внешние реализации LoopEvents (тестовые заглушки) могут не знать
        # про новое поле — доступ идёт через getattr.
        loop = SimpleNamespace(events=SimpleNamespace())
        await AgentLoop._emit_reasoning(
            loop, AssistantMessage(content="", reasoning="м")
        )


def test_assistant_message_reasoning_defaults_to_empty():
    assert AssistantMessage(content="x").reasoning == ""
    assert AssistantMessage(content="", reasoning="r").reasoning == "r"


def test_usage_add_sums_reasoning_tokens():
    total = Usage(reasoning_tokens=3) + Usage(reasoning_tokens=4)
    assert total.reasoning_tokens == 7
