"""Компактный вывод для Telegram. Возвращает HTML-безопасный текст."""

from __future__ import annotations

import html
import re

from agent_coder.llm.types import ToolCall, Usage, format_cost
from agent_coder.telegram.formatting import to_telegram_html

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


_LINE_RANGE_RE = re.compile(r"строки (\d+)-(\d+) из (\d+)")


def render_tool_result(tc: ToolCall, result: str) -> str:
    preview = result.strip().split("\n")[0]
    # read_file: вместо «первой строки файла» показываем, что реально пришло —
    # диапазон и объём. Раньше из слайса на 2000 строк в чате было видно
    # только «файл.py (строки 1-2000 из 5000):», обрезанное до 80 симв.
    if tc.name == "read_file":
        m = _LINE_RANGE_RE.search(preview)
        if m:
            first, last, total = (int(g) for g in m.groups())
            preview = f"строки {first}-{last} из {total}" + (
                " (до конца)" if last >= total else ""
            )
    if len(preview) > 80:
        preview = preview[:77] + "…"
    mark = "✗" if preview.startswith("Ошибка") else "✓"
    return f"   {mark} <i>{html_escape(preview)}</i>"


def render_usage(usage: Usage, *, max_chars: int = 0) -> str:
    """Строка с токенами и стоимостью — в blockquote для визуального выделения.

    `write` показывается наравне с `cache`: прогрев префикса — такая же
    метрика, и без неё непонятно, почему следующий ход дешевле (раньше поле
    собиралось в `Usage`, но в футер не попадало).

    `🧠` — токены рассуждений, только если провайдер их отдал (Claude через
    RouterAI всегда 0, DeepSeek — реальное число).
    """
    parts = [f"in {usage.input_tokens}", f"out {usage.output_tokens}"]
    if usage.reasoning_tokens:
        parts.append(f"🧠 {usage.reasoning_tokens}")
    if usage.cache_read_tokens:
        parts.append(f"cache {usage.cache_read_tokens}")
    if usage.cache_write_tokens:
        parts.append(f"write {usage.cache_write_tokens}")
    return (
        f"<blockquote>💰 {' / '.join(parts)} · {format_cost(usage.cost)}</blockquote>"
    )


def render_assistant_text(text: str) -> str:
    """Текст от ассистента: markdown модели → HTML, с префиксом ⏺.

    Раньше текст только экранировался, и пользователь видел «значки»
    markdown как есть: `**жирный**`, `## Заголовок`, ```блоки кода```,
    `- пункты`, `[ссылка](url)`. Конвертация — в `formatting.py`.
    """
    return f"⏺ {to_telegram_html(text)}"


def render_reasoning(text: str) -> str:
    """Рассуждения модели свёрнутым блоком.

    `<blockquote expandable>` Telegram показывает одной строкой, которую
    разворачивают нажатием: рассуждения часто длиннее самого ответа, и если
    показывать их целиком, ответ уезжает за пределы экрана.

    Текст экранируется и НЕ прогоняется через markdown: рассуждения — это
    черновик модели (обрывки фраз, куски кода, звёздочки), и попытка
    отформатировать его даёт мусорную разметку.
    """
    body = text.strip()
    if not body:
        return ""
    return f"\n<blockquote expandable>💭 {html_escape(body)}</blockquote>"


def render_compression(comp) -> str:
    """Строка о сжатии истории: «📦 История сжата: 90k → 18k токенов»."""

    def _k(n: int) -> str:
        return f"{n // 1000}k" if n >= 1000 else str(n)

    cost = f", {comp.cost:.2f}₽" if comp.cost > 0 else ""
    return (
        f"📦 <b>История сжата</b>: "
        f"{_k(comp.tokens_before)} → {_k(comp.tokens_after)} токенов "
        f"<i>({comp.messages_before} → {comp.messages_after} сообщений, "
        f"сжатие #{comp.summary_count}{cost})</i>"
    )


def _short_args(args: dict) -> str:
    out = []
    for k, v in args.items():
        s = str(v).replace("\n", " ")
        if len(s) > 50:
            s = s[:47] + "…"
        out.append(f"{k}={s!r}")
    return ", ".join(out)


# --- Результаты операций (/verify, /patch, ...) ---
#
# Единый формат «итога» после LLM-операций. cmd_verify и cmd_patch были
# почти-копиями, но разошлись: bullet-префикс («• » vs «  • »), места
# ⚠-предупреждений, формат ошибок. Helpers — один источник правды.
# <pre> для raw-контента сохранён (git diff, отчёт) — не унифицируется.


def render_result_header(icon: str, name: str, model: str) -> str:
    """`🔍 <b>Verify</b> · <code>model</code>` — единая шапка результата."""
    return f"{icon} <b>{html_escape(name)}</b> · <code>{html_escape(model)}</code>"


def render_result_error(error: str) -> str:
    """`❌ <текст>` — единая строка ошибки."""
    return f"❌ {html_escape(error)}"


def render_result_metrics(iterations: int, cost: float) -> str:
    """`Итераций: N · X.XX₽` — единая строка метрик."""
    return f"Итераций: {iterations} · {cost:.2f}₽"


def render_bullet_list(
    items: list[str], *, limit: int = 10, more_label: str = "… ещё"
) -> list[str]:
    """Bullet-список с усечением: `  • item` × limit + `  … ещё N`.

    items — уже отформатированные строки (caller делает html_escape и
    разметку), helper только добавляет префикс и усечение.
    """
    lines = [f"  • {it}" for it in items[:limit]]
    if len(items) > limit:
        lines.append(f"  {more_label} {len(items) - limit}")
    return lines


def render_pre_block(title: str, content: str, *, limit: int) -> str:
    """`\n<b>title:</b>\n<pre>escaped[:limit]</pre>` — raw-контент в <pre>."""
    return f"\n<b>{html_escape(title)}:</b>\n<pre>{html_escape(content[:limit])}</pre>"


def render_warning(message: str) -> str:
    """`\n⚠ message` — единая строка предупреждения."""
    return f"\n⚠ {html_escape(message)}"
