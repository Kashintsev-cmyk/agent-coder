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


class TestRenderCompression:
    def _comp(self, before=90000, after=18000, mb=40, ma=10, n=1):
        from agent_coder.agent.compression import CompressionResult

        return CompressionResult(
            messages_before=mb,
            messages_after=ma,
            tokens_before=before,
            tokens_after=after,
            summary_count=n,
        )

    def test_shows_token_flow(self):
        out = renderer.render_compression(self._comp())
        assert "История сжата" in out
        assert "90k" in out
        assert "18k" in out
        assert "токенов" in out

    def test_shows_message_counts_and_index(self):
        out = renderer.render_compression(self._comp(mb=40, ma=10, n=3))
        assert "40 → 10 сообщений" in out
        assert "сжатие #3" in out

    def test_small_numbers_not_scaled(self):
        out = renderer.render_compression(self._comp(before=900, after=120))
        assert "900" in out
        assert "120" in out
        assert "0k" not in out


def test_tool_icon_known_and_fallback():
    from agent_coder.telegram.renderer import tool_icon

    assert tool_icon("read_file") == "📖"
    assert tool_icon("run_command") == "⚡"
    assert tool_icon("totally-made-up") == "🔧"


# --- render_result_* (унификация итога /verify, /patch) ---


def test_render_result_header():
    from agent_coder.telegram.renderer import render_result_header

    out = render_result_header("🔍", "Verify", "anthropic/claude-haiku-5.5")
    assert out == "🔍 <b>Verify</b> · <code>anthropic/claude-haiku-5.5</code>"


def test_render_result_error_escapes():
    from agent_coder.telegram.renderer import render_result_error

    out = render_result_error("boom <bad> & worse")
    assert out == "❌ boom &lt;bad&gt; &amp; worse"


def test_render_result_metrics():
    from agent_coder.telegram.renderer import render_result_metrics

    assert render_result_metrics(3, 0.05) == "Итераций: 3 · 0.05₽"


def test_render_bullet_list_truncates():
    from agent_coder.telegram.renderer import render_bullet_list

    items = [f"item{i}" for i in range(12)]
    out = render_bullet_list(items, limit=10)
    assert out[0] == "  • item0"
    assert len(out) == 11
    assert out[-1] == "  … ещё 2"


def test_render_bullet_list_no_truncation():
    from agent_coder.telegram.renderer import render_bullet_list

    out = render_bullet_list(["a", "b"], limit=10)
    assert out == ["  • a", "  • b"]


def test_render_pre_block():
    from agent_coder.telegram.renderer import render_pre_block

    out = render_pre_block("git diff", "+ line <x>", limit=100)
    assert out == "\n<b>git diff:</b>\n<pre>+ line &lt;x&gt;</pre>"
    # усечение
    out2 = render_pre_block("t", "abcdef", limit=3)
    assert out2 == "\n<b>t:</b>\n<pre>abc</pre>"


def test_render_warning():
    from agent_coder.telegram.renderer import render_warning

    out = render_warning("Бюджет <исчерпан>")
    assert out == "\n⚠ Бюджет &lt;исчерпан&gt;"
