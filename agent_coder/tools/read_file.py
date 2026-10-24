"""Инструмент: чтение файла."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from agent_coder.tools._scope import in_scope
from agent_coder.tools.registry import Tool, ToolContext

# MAX_BYTES контролирует только «чтение целиком» (без offset/limit).
# 200k -> 80k: одно read_file не должно возвращать ~50k токенов (85%
# soft-лимита). 80k ~ 20k токенов — влезает с запасом. Файлы > 80k
# читаются слайсами (offset/limit) — контракт tool'а не меняется.
MAX_BYTES = 80_000
MAX_LINES = 2000


async def _execute(args: dict[str, Any], ctx: ToolContext) -> str:
    rel = args.get("path", "").strip()
    if not rel:
        return "Ошибка: не указан path"

    full = _resolve(ctx.workspace, rel)
    if full is None:
        return f"Ошибка: путь вне рабочей директории: {rel}"

    if not in_scope(rel, ctx.audit_scopes):
        return (
            f"Ошибка: {rel!r} вне области аудита {ctx.audit_scopes}. "
            f"Читай только файлы из указанных путей."
        )

    if not full.exists():
        return f"Файл не найден: {rel}"
    if not full.is_file():
        return f"Не файл: {rel}"

    offset_raw = args.get("offset")
    limit_raw = args.get("limit")
    has_slice = offset_raw is not None or limit_raw is not None

    try:
        offset = int(offset_raw) if offset_raw is not None else 0
        limit = int(limit_raw) if limit_raw is not None else None
    except (TypeError, ValueError):
        return "Ошибка: offset и limit должны быть целыми числами"

    if offset < 0:
        return f"Ошибка: offset не может быть отрицательным ({offset})"
    if limit is not None and limit < 0:
        return f"Ошибка: limit не может быть отрицательным ({limit})"
    if limit is not None and limit > MAX_LINES:
        return f"limit слишком большой ({limit}). Максимум {MAX_LINES} строк за раз."

    # R13: is_full_read — «читать всё целиком», даже с offset=0 (обход).
    is_full_read = (offset == 0) and (limit is None)

    # R13: full-read по уже прочитанному файлу отвергаем — модель перечитывает
    # 28/32 файлов в resume (na_shtile с2, cost 8.19₽). Разрешено: offset/limit
    # для уточнения конкретных строк.
    if is_full_read and ctx.read_paths:
        known = {k.replace("\\", "/").lstrip("./") for k in ctx.read_paths}
        rel_clean = rel.replace("\\", "/").lstrip("./")
        if rel_clean in known:
            return (
                f"Файл {rel} уже прочитан (см. [ПРОЧИТАННЫЕ ФАЙЛЫ]). "
                f"НЕ перечитывай целиком — используй offset/limit для "
                f"конкретных строк (например, offset=285 limit=20) или search."
            )

    size = full.stat().st_size
    # Если пользователь передал offset/limit — это ОК для больших файлов.
    # Блокируем только «читать всё целиком» для слишком больших файлов.
    if is_full_read and size > MAX_BYTES:
        return (
            f"Файл слишком большой ({size} байт). "
            f"Укажи offset и limit — например, offset=0 limit=100, "
            f"или используй search."
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
            return f"Ошибка чтения: {e}"

        if not chunk:
            return f"{rel}: пусто (offset={offset}, limit={limit})"
        numbered = "\n".join(f"{i + offset + 1:5d}│ {ln}" for i, ln in enumerate(chunk))
        return (
            f"{rel} (строки {offset + 1}-{offset + len(chunk)} из {total}):\n{numbered}"
        )

    try:
        text = full.read_text(encoding="utf-8", errors="replace")
    except Exception as e:
        return f"Ошибка чтения: {e}"

    lines = text.splitlines()
    numbered = "\n".join(f"{i + 1:5d}│ {ln}" for i, ln in enumerate(lines))
    return f"{rel} ({len(lines)} строк):\n{numbered}"


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
    executor=_execute,
    read_only=True,
)
