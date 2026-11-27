"""Инструмент: чтение файла."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from agent_coder.tools._scope import in_scope
from agent_coder.tools.registry import (
    Tool,
    ToolContext,
    ToolExecutionResult,
    ToolStatus,
)


def _env_int(name: str, default: int, *, lo: int, hi: int) -> int:
    """Лимит из окружения, с клампом в [lo, hi].

    Индустриальный стандарт: лимиты чтения должны быть настраиваемыми
    (cline#13263 — «limits should be configurable»), но с жёстким потолком,
    чтобы конфиг не мог вкачать 10 МБ в контекст одной строкой.
    """
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        val = int(raw)
    except ValueError:
        return default
    return max(lo, min(hi, val))


# MAX_BYTES контролирует объём возвращаемого текста — и для «чтения целиком»,
# и для слайса (раньше проверялся только is_full_read → слайс из 2000 очень
# длинных строк мог отдать сотни КБ).
# 200k -> 80k: одно read_file не должно возвращать ~50k токенов (85%
# soft-лимита). 80k ~ 20k токенов — влезает с запасом. Файлы > 80k
# читаются слайсами (offset/limit) — контракт tool'а не меняется.
MAX_BYTES = _env_int("READ_MAX_BYTES", 80_000, lo=1_000, hi=1_000_000)
MAX_LINES = _env_int("READ_MAX_LINES", 2_000, lo=1, hi=10_000)
# Claude Code режет строки длиннее 2000 символов: иначе минифицированный
# JS/CSS или однострочный JSON съедает весь бюджет одним «рядком».
MAX_LINE_CHARS = _env_int("READ_MAX_LINE_CHARS", 2_000, lo=100, hi=100_000)


def _clip_line(line: str) -> str:
    """Обрезать слишком длинную строку, отметив сколько выкинуто."""
    if len(line) <= MAX_LINE_CHARS:
        return line
    dropped = len(line) - MAX_LINE_CHARS
    return f"{line[:MAX_LINE_CHARS]}…[+{dropped} симв.]"


def _render(
    rel: str,
    chunk: list[str],
    offset: int,
    total: int | None,
) -> str | None:
    """Нумерация + байтовый кап + футер с подсказкой про offset.

    Возвращает None, если в чанк не влезло ни строки.
    """
    numbered: list[str] = []
    used = 0
    shown = 0
    for i, raw in enumerate(chunk):
        piece = f"{i + offset + 1:5d}│ {_clip_line(raw)}"
        size = len(piece.encode("utf-8", errors="replace")) + 1
        if used + size > MAX_BYTES:
            break
        numbered.append(piece)
        used += size
        shown += 1

    if not shown:
        return None

    first = offset + 1
    last = offset + shown
    head = (
        f"{rel} (строки {first}-{last} из {total}):"
        if total
        else f"{rel} (строки {first}-{last}):"
    )
    body = "\n".join(numbered)

    notes: list[str] = []
    if shown < len(chunk):
        notes.append(
            f"… лимит {MAX_BYTES} байт: показаны строки {first}-{last}, "
            f"далее offset={last}"
        )
    elif total is not None and last < total:
        notes.append(f"… далее offset={last}")
    if notes:
        return f"{head}\n{body}\n\n" + "\n".join(notes)
    return f"{head}\n{body}"


def _read_error(output: str) -> ToolExecutionResult:
    return ToolExecutionResult(output, ToolStatus.ERROR)


def _read_rejected(output: str) -> ToolExecutionResult:
    return ToolExecutionResult(output, ToolStatus.REJECTED)


def _read_success(output: str) -> ToolExecutionResult:
    return ToolExecutionResult(output, ToolStatus.SUCCESS_NO_CHANGE)


async def _execute_result(
    args: dict[str, Any], ctx: ToolContext
) -> ToolExecutionResult:
    rel = args.get("path", "").strip()
    if not rel:
        return _read_error("Ошибка: не указан path")

    full = _resolve(ctx.workspace, rel)
    if full is None:
        return _read_error(f"Ошибка: путь вне рабочей директории: {rel}")

    # M2: проверяем resolved-relative, а не сырой rel — иначе `bot/../.env`
    # проходил сырую in_scope через prefix-match, хотя resolve давал `.env`.
    try:
        resolved_rel = str(
            Path(full).resolve().relative_to(Path(ctx.workspace).resolve())
        )
    except ValueError:
        return _read_error(f"Ошибка: путь вне рабочей директории: {rel}")

    if not in_scope(str(resolved_rel), ctx.audit_scopes):
        return _read_error(
            f"Ошибка: {rel!r} вне области аудита {ctx.audit_scopes}. "
            f"Читай только файлы из указанных путей."
        )

    if not full.exists():
        return _read_error(f"Файл не найден: {rel}")
    if not full.is_file():
        return _read_error(f"Не файл: {rel}")

    offset_raw = args.get("offset")
    limit_raw = args.get("limit")
    has_slice = offset_raw is not None or limit_raw is not None

    try:
        offset = int(offset_raw) if offset_raw is not None else 0
        limit = int(limit_raw) if limit_raw is not None else None
    except (TypeError, ValueError):
        return _read_error("Ошибка: offset и limit должны быть целыми числами")

    if offset < 0:
        return _read_error(f"Ошибка: offset не может быть отрицательным ({offset})")
    if limit is not None and limit < 0:
        return _read_error(f"Ошибка: limit не может быть отрицательным ({limit})")
    if limit is not None and limit > MAX_LINES:
        return _read_error(
            f"limit слишком большой ({limit}). Максимум {MAX_LINES} строк за раз."
        )

    # R13: is_full_read — «читать всё целиком», даже с offset=0 (обход).
    is_full_read = (offset == 0) and (limit is None)

    # R13: full-read по уже прочитанному файлу отвергаем — модель перечитывает
    # 28/32 файлов в resume (na_shtile с2, cost 8.19₽). Разрешено: offset/limit
    # для уточнения конкретных строк.
    if is_full_read and ctx.read_paths:
        known = {k.replace("\\", "/").lstrip("./") for k in ctx.read_paths}
        rel_clean = rel.replace("\\", "/").lstrip("./")
        if rel_clean in known:
            return _read_rejected(
                f"Файл {rel} уже прочитан (см. [ПРОЧИТАННЫЕ ФАЙЛЫ]). "
                f"НЕ перечитывай целиком — используй offset/limit для "
                f"конкретных строк (например, offset=285 limit=20) или search."
            )

    size = full.stat().st_size
    # Если пользователь передал offset/limit — это ОК для больших файлов.
    # Блокируем только «читать всё целиком» для слишком больших файлов.
    # Формулировка — как в Claude Code: сообщаем размер и сразу готовую
    # команду следующего шага, чтобы не тратить итерацию на «а как читать?».
    if is_full_read and size > MAX_BYTES:
        return _read_error(
            f"Файл слишком большой ({size} байт, лимит {MAX_BYTES}). "
            f"Читай слайсами: offset=0 limit={MAX_LINES}, затем "
            f"offset={MAX_LINES} limit={MAX_LINES} и т.д. "
            f"Или найди нужное через search."
        )

    # Срез читаем построчно, не загружая файл целиком в память.
    if has_slice:
        chunk: list[str] = []
        total = 0
        end = None if limit is None else offset + limit
        try:
            with full.open("r", encoding="utf-8", errors="replace") as f:
                for i, raw_line in enumerate(f):
                    total += 1
                    if end is not None and i >= end:
                        continue
                    if i >= offset:
                        chunk.append(raw_line.rstrip("\r\n"))
        except Exception as e:
            return _read_error(f"Ошибка чтения: {e}")

        if not chunk:
            return _read_success(f"{rel}: пусто (offset={offset}, limit={limit})")
        out = _render(rel, chunk, offset, total)
        if out is None:
            return _read_error(
                f"{rel}: первой строке не хватает места в {MAX_BYTES} байт "
                f"(строка длиннее лимита). Уменьши offset или читай search."
            )
        return _read_success(out)

    try:
        text = full.read_text(encoding="utf-8", errors="replace")
    except Exception as e:
        return _read_error(f"Ошибка чтения: {e}")

    lines = text.splitlines()
    out = _render(rel, lines, 0, len(lines))
    if out is None:
        return _read_success(f"{rel}: пусто")
    return _read_success(out)


async def _execute(args: dict[str, Any], ctx: ToolContext) -> str:
    """Compatibility wrapper used by existing direct executor tests."""
    return (await _execute_result(args, ctx)).output


def _resolve(workspace: str, rel: str) -> Path | None:
    base = Path(workspace).resolve()
    target = (base / rel).resolve()
    try:
        target.relative_to(base)
    except ValueError:
        return None
    return target


read_file_tool = Tool(
    name="read_file",
    description=(
        "Прочитать файл из рабочей директории. Возвращает содержимое "
        "с нумерацией строк. Для больших файлов укажи offset и limit "
        f"(не более {MAX_LINES} строк за раз)."
    ),
    parameters={
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "Путь относительно workspace"},
            "offset": {"type": "integer", "description": "Начальная строка (0-based)"},
            "limit": {"type": "integer", "description": "Сколько строк прочитать"},
        },
        "required": ["path"],
    },
    executor=_execute_result,
    read_only=True,
)
