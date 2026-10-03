"""Компактный вывод для Telegram. Возвращает HTML-безопасный текст."""
from __future__ import annotations

import html

from agent_coder.llm.types import ToolCall, Usage, format_cost

TOOL_ICONS = {
    "read_file": "📖",
    "write_file": "✏️",
    "edit_file": "✎",
    "list_files": "📂",
    "search": "🔍",
    "run_command": "⚡",
    "git_status": "📊",
    "git_diff": "📝",
    "git_log": "📜",
    "git_commit": "💾",
}


def tool_icon(name: str) -> str:
    """Иконка инструмента. Единая точка (fallback 🔧)."""
    return TOOL_ICONS.get(name, "🔧")


def html_escape(s: str) -> str:
    """Экранируем HTML-спец-символы (но не кавычки)."""
    return html.escape(s, quote=False)


def render_tool_call(tc: ToolCall) -> str:
    icon = tool_icon(tc.name)
    args = _short_args(tc.arguments)
    return f"{icon} <b>{html_escape(tc.name)}</b>({html_escape(args)})"


def render_tool_result(tc: ToolCall, result: str) -> str:
    preview = result.strip().split("\n")[0]
    if len(preview) > 80:
        preview = preview[:77] + "…"
    mark = "✗" if preview.startswith("Ошибка") else "✓"
    return f"   {mark} <i>{html_escape(preview)}</i>"


def render_usage(usage: Usage) -> str:
    """Строка с токенами и стоимостью — в blockquote для визуального выделения."""
    parts = [f"in {usage.input_tokens}", f"out {usage.output_tokens}"]
    if usage.cache_read_tokens:
        parts.append(f"cache {usage.cache_read_tokens}")
    return f"<blockquote>💰 {' / '.join(parts)} · {format_cost(usage.cost)}</blockquote>"


def render_assistant_text(text: str) -> str:
    """Текст от ассистента — с префиксом, экранированный."""
    return f"⏺ {html_escape(text)}"


def _short_args(args: dict) -> str:
    out = []
    for k, v in args.items():
        s = str(v).replace("\n", " ")
        if len(s) > 50:
            s = s[:47] + "…"
        out.append(f"{k}={s!r}")
    return ", ".join(out)
