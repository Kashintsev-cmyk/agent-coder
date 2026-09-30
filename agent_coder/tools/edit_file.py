"""Инструмент: точечная правка файла (замена подстроки)."""
from __future__ import annotations

from pathlib import Path
from typing import Any

from agent_coder.tools.registry import Tool, ToolContext


async def _execute(args: dict[str, Any], ctx: ToolContext) -> str:
    rel = (args.get("path") or "").strip()
    old_string = args.get("old_string", "")
    new_string = args.get("new_string", "")
    replace_all = bool(args.get("replace_all", False))

    if not rel:
        return "Ошибка: не указан path"
    if not old_string:
        return "Ошибка: old_string пустой"
    if old_string == new_string:
        return "Ошибка: old_string и new_string идентичны — нечего менять"

    base = Path(ctx.workspace).resolve()
    full = (base / rel).resolve()
    try:
        full.relative_to(base)
    except ValueError:
        return f"Ошибка: путь вне workspace: {rel}"

    if not full.exists():
        return f"Файл не найден: {rel}"
    if not full.is_file():
        return f"Не файл: {rel}"

    try:
        with open(full, "r", encoding="utf-8", newline="") as f:
            content = f.read()
    except Exception as e:
        return f"Ошибка чтения: {e}"

    count = content.count(old_string)
    if count == 0:
        return (
            f"Ошибка: old_string не найден в {rel}. "
            f"Проверь точное содержимое (пробелы, табы, переводы строк)."
        )
    if count > 1 and not replace_all:
        return (
            f"Ошибка: old_string встречается {count} раз в {rel}. "
            f"Добавь больше контекста, чтобы строка была уникальной, "
            f"или укажи replace_all=true для замены всех вхождений."
        )

    # Определяем номер первой строки изменения
    first_idx = content.index(old_string)
    line_num = content[:first_idx].count("\n") + 1

    if replace_all:
        new_content = content.replace(old_string, new_string)
    else:
        new_content = content.replace(old_string, new_string, 1)

    try:
        with open(full, "w", encoding="utf-8", newline="") as f:
            f.write(new_content)
    except Exception as e:
        return f"Ошибка записи: {e}"

    if replace_all:
        return f"Заменено {count} вхождений в {rel}"
    return f"Заменено в {rel}:{line_num}"


edit_file_tool = Tool(
    name="edit_file",
    description=(
        "Точечная правка файла: заменить фрагмент old_string на new_string. "
        "Используй для небольших изменений вместо write_file — это в разы дешевле "
        "по токенам и безопаснее. old_string должен встречаться в файле ровно один "
        "раз, иначе операция отклонится (укажи больше контекста или replace_all=true). "
        "ВАЖНО: старый и новый фрагменты должны быть с точным сохранением отступов."
    ),
    parameters={
        "type": "object",
        "properties": {
            "path": {
                "type": "string",
                "description": "Путь к файлу относительно workspace",
            },
            "old_string": {
                "type": "string",
                "description": "Точный фрагмент, который нужно заменить (с отступами)",
            },
            "new_string": {
                "type": "string",
                "description": "На что заменить",
            },
            "replace_all": {
                "type": "boolean",
                "description": (
                    "Если true — заменить все вхождения. По умолчанию false, "
                    "и тогда old_string должен быть уникальным."
                ),
            },
        },
        "required": ["path", "old_string", "new_string"],
    },
    executor=_execute,
    requires_approval=False,
    read_only=False,
)
