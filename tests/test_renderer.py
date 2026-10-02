"""Тесты HTML-рендера сообщений для Telegram."""
from __future__ import annotations

from agent_coder.llm.types import ToolCall, Usage
from agent_coder.telegram import renderer


class TestRenderToolCall:
    def test_known_tool_icon(self):
        tc = ToolCall(id="1", name="read_file", arguments={"path": "a.py"})
        out = renderer.render_tool_call(tc)
        assert "📖" in out
        assert "read_file" in out

    def test_unknown_tool_fallback_icon(self):
        tc = ToolCall(id="1", name="mystery", arguments={})
        assert "🔧" in renderer.render_tool_call(tc)

    def test_html_escaped(self):
        tc = ToolCall(id="1", name="x", arguments={"k": "<b>"})
        out = renderer.render_tool_call(tc)
        assert "<b>" not in out.replace("<b>", "", 1) or "&lt;" in out


class TestRenderToolResult:
    def test_success_mark(self):
        tc = ToolCall(id="1", name="x")
        assert "✓" in renderer.render_tool_result(tc, "готово")

    def test_error_mark(self):
        tc = ToolCall(id="1", name="x")
        assert "✗" in renderer.render_tool_result(tc, "Ошибка: нет файла")

    def test_preview_truncated(self):
        tc = ToolCall(id="1", name="x")
        out = renderer.render_tool_result(tc, "a" * 200)
        assert "…" in out

    def test_only_first_line(self):
        tc = ToolCall(id="1", name="x")
        out = renderer.render_tool_result(tc, "первая\nвторая")
        assert "вторая" not in out


class TestRenderUsage:
    def test_basic(self):
        out = renderer.render_usage(Usage(input_tokens=10, output_tokens=5))
        assert "in 10" in out
        assert "out 5" in out

    def test_with_cache(self):
        out = renderer.render_usage(
            Usage(input_tokens=10, output_tokens=5, cache_read_tokens=3)
        )
        assert "cache 3" in out


class TestRenderAssistantText:
    def test_prefix_and_escape(self):
        out = renderer.render_assistant_text("<b>важно</b>")
        assert out.startswith("⏺ ")
        assert "&lt;b&gt;" in out
