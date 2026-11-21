"""Markdown модели → Telegram HTML.

Модели отвечают markdown-ом: **жирным**, `кодом`, ```блоками```, заголовками,
списками и ссылками. Telegram с `parse_mode=HTML` понимает `<b>`, `<i>`,
`<code>`, `<pre>`, `<blockquote>`, `<a href>` — но не markdown. Без конвертации
пользователь видит «значки» разметки (`**`, `##`, `- `) вместо оформления.

Порядок операций важен:

1. вынимаем блоки кода, inline-код и ссылки в заглушки — внутри кода разметка
   не действует, а в URL нельзя допустить двойное экранирование `&`;
2. экранируем остаток (иначе `<` в тексте сломает HTML всего сообщения);
3. построчно разбираем заголовки/списки/цитаты;
4. инлайн: жирный, курсив;
5. возвращаем заглушки уже готовыми тегами.

Чего сознательно НЕ делаем: `_курсив_` подчёркиваниями. В ответах про код
`read_file`, `snake_case`, `__init__.py` встречаются чаще настоящего курсива,
и любой такой текст ломался бы.
"""

from __future__ import annotations

import html
import re

# ```язык\nкод``` или ```код```. `info` — всё до перевода строки (или до
# закрывающих бэктиков, если блок однострочный); как делить однострочный блок
# на язык и код — решает `_code_block_html`.
_CODE_BLOCK = re.compile(r"```(?P<info>[^\n]*)\n?(?P<body>.*?)```", re.DOTALL)
_INLINE_CODE = re.compile(r"`([^`\n]+)`")
_BOLD = re.compile(r"\*\*(?!\s)(.+?)(?<!\s)\*\*", re.DOTALL)
_ITALIC = re.compile(r"(?<![*\w])\*(?!\s|\*)([^*\n]+?)(?<!\s)\*(?![*\w])")
_LINK = re.compile(r"\[([^\]\n]+)\]\((https?://[^)\s]+)\)")
_HEADING = re.compile(r"^\s{0,3}#{1,6}\s+(.*?)\s*#*\s*$")
_BULLET = re.compile(r"^\s{0,3}[-*+]\s+(.*)$")
# `>` к моменту разбора уже экранирован в `&gt;` (html.escape идёт раньше
# построчного разбора, чтобы теги не экранировались вместе с текстом).
_QUOTE = re.compile(r"^\s{0,3}&gt;\s?(.*)$")
_HOLE = re.compile(r"\x00(\d+)\x00")

# Язык подсветки: class="language-python". Допустимы буквы, цифры, `+`, `-`,
# `_`, `#` (c#, c++). Всё прочее — не язык, а часть кода.
_LANG_OK = re.compile(r"^[A-Za-z0-9_+#.-]{1,20}$")


def _code_block_html(info: str, body: str) -> str:
    """`<pre><code class="language-X">…</code></pre>` или просто `<pre>`.

    Если «язык» оказался фразой с пробелом (`bash ls -la`), первое слово
    считаем языком, остальное — первой строкой кода: иначе пользователь
    теряет содержимое однострочного блока.
    """
    lang = ""
    if info:
        head, _, rest = info.partition(" ")
        if _LANG_OK.match(head.strip()):
            lang = head.strip().lower()
            if rest.strip():
                body = f"{rest.strip()}\n{body}" if body else rest.strip()
        else:
            body = f"{info}\n{body}" if body else info
    escaped = html.escape(body.rstrip("\n"), quote=False)
    if lang:
        return f'<pre><code class="language-{lang}">{escaped}</code></pre>'
    return f"<pre>{escaped}</pre>"


def _render_lines(text: str) -> str:
    """Заголовки, списки и цитаты — построчно.

    Подряд идущие строки `>` собираются в ОДИН `<blockquote>`: Telegram
    рисует рамку на блок, и отдельная рамка на каждую строку выглядит рвано.
    """
    out: list[str] = []
    quote_buf: list[str] = []

    def flush_quote() -> None:
        if quote_buf:
            out.append("<blockquote>" + "\n".join(quote_buf) + "</blockquote>")
            quote_buf.clear()

    for line in text.split("\n"):
        quote = _QUOTE.match(line)
        if quote is not None:
            quote_buf.append(quote.group(1))
            continue
        flush_quote()
        heading = _HEADING.match(line)
        if heading is not None:
            out.append(f"<b>{heading.group(1)}</b>")
            continue
        bullet = _BULLET.match(line)
        if bullet is not None:
            # Отступ вложенных пунктов сохраняем: два пробела на уровень.
            indent = len(line) - len(line.lstrip())
            out.append(" " * indent + "• " + bullet.group(1))
            continue
        out.append(line)
    flush_quote()
    return "\n".join(out)


def to_telegram_html(text: str) -> str:
    """Конвертировать markdown-ответ модели в HTML, безопасный для Telegram."""
    if not text:
        return ""
    holes: list[str] = []

    def stash(payload: str) -> str:
        holes.append(payload)
        return f"\x00{len(holes) - 1}\x00"

    work = _CODE_BLOCK.sub(
        lambda m: stash(_code_block_html(m.group("info"), m.group("body"))), text
    )
    work = _INLINE_CODE.sub(
        lambda m: stash(f"<code>{html.escape(m.group(1), quote=False)}</code>"), work
    )
    # Ссылки тоже вынимаем ДО общего escape: иначе `&` в URL экранировался бы
    # дважды (`&amp;amp;`) и ссылка вела бы в никуда.
    work = _LINK.sub(
        lambda m: stash(
            f'<a href="{html.escape(m.group(2), quote=True)}">'
            f"{html.escape(m.group(1), quote=False)}</a>"
        ),
        work,
    )
    work = html.escape(work, quote=False)
    work = _render_lines(work)
    work = _BOLD.sub(lambda m: f"<b>{m.group(1)}</b>", work)
    work = _ITALIC.sub(lambda m: f"<i>{m.group(1)}</i>", work)

    # Заглушки могли попасть внутрь собранных блоков (код в цитате) —
    # разворачиваем, пока замены есть.
    while _HOLE.search(work):
        work = _HOLE.sub(lambda m: holes[int(m.group(1))], work)
    return work
