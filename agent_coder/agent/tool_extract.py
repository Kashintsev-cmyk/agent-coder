"""Детерминированное извлечение из tool-результатов.

Идея: полный tool-вывод уходит в history_archive (его умеет искать
search_history), а в session.messages кладётся сжатая версия по правилам
для каждого tool. Без LLM — только regex по строкам + head/tail + счётчики.

Лимиты: DEFAULT_LIMITS (per-tool) переопределяются через env:
  TOOL_MAX_CHARS_<TOOL_NAME>  — конкретный tool, приоритет 1
  TOOL_MAX_CHARS              — глобальный, приоритет 2
Если ни то, ни другое не задано — DEFAULT_LIMITS[tool] или переданный
max_chars (для неизвестных tools).
"""

from __future__ import annotations

import os
import re
from collections.abc import Callable
from typing import Any

Extractor = Callable[[str, dict[str, Any], int], str]

DEFAULT_MAX_CHARS = 4096
DEFAULT_LIMITS: dict[str, int] = {
    "run_command": 4096,
    "read_file": 4096,
    "git_diff": 2048,
    "git_status": 2048,
    "git_log": 2048,
    "search": 2048,
    "search_history": 2048,
    "list_files": 1024,
}

# error-строки, которые нельзя терять в run_command-выводе
_ERROR_RE = re.compile(r"error|Error|FAIL|Traceback|Warning", re.IGNORECASE)
# определения/импорты в read_file-выводе (после отсечения "NNNNN│ ")
_DEF_RE = re.compile(r"^(def |class |import |from )")


def _cap(text: str, limit: int) -> str:
    """Жёсткий потолок с маркером. Гарантирует len(result) <= limit."""
    if len(text) <= limit:
        return text
    marker = f"\n…[обрезано {len(text)} симв.]…"
    if len(marker) >= limit:
        return text[:limit]
    keep = limit - len(marker)
    return text[:keep] + marker


def _fallback(result: str, args: dict[str, Any], limit: int) -> str:
    """Общий fallback: head(40%) + маркер + tail(40%)."""
    if len(result) <= limit:
        return result
    marker = f"\n…[обрезано {len(result)} симв.]…\n"
    if len(marker) >= limit:
        return result[:limit]
    budget = limit - len(marker)
    head = budget * 2 // 5
    tail = budget * 2 // 5
    return result[:head] + marker + result[-tail:]


def _assemble(lines: list[str], keep_idx: set[int], limit: int) -> str:
    """Склеить выбранные строки в исходном порядке, вставляя маркеры пропусков."""
    if not keep_idx:
        return _fallback("\n".join(lines), {}, limit)
    parts: list[str] = []
    prev = -1
    for i in sorted(keep_idx):
        if prev != -1 and i != prev + 1:
            parts.append(f"…[{i - prev - 1} строк скрыто]…")
        parts.append(lines[i])
        prev = i
    if prev < len(lines) - 1:
        parts.append(f"…[{len(lines) - prev - 1} строк скрыто]…")
    return _cap("\n".join(parts), limit)


def _strip_numbered(line: str) -> str:
    """'  42│ def foo()' -> 'def foo()' (read_file нумерует строки)."""
    idx = line.find("│ ")
    return line[idx + 2 :] if idx >= 0 else line


# ---------- formatters ----------


def _fmt_run_command(result: str, args: dict[str, Any], limit: int) -> str:
    """Header ($ cmd, exit code) + error-строки + первые/последние 20 строк.

    Приоритет: header и error-строки. head/tail — контекст вокруг них.
    Если error-строк больше, чем влезает в лимит, хвост режется _cap
    (best-effort; в реальных сессиях ошибок мало).
    """
    lines = result.splitlines()
    n = len(lines)
    keep: set[int] = set(range(min(2, n)))  # "$ cmd", "exit code: N"
    for i, ln in enumerate(lines):
        if _ERROR_RE.search(ln):
            keep.add(i)
    body_start = 2 if n >= 2 else 0
    for i in range(body_start, min(body_start + 20, n)):
        keep.add(i)
    for i in range(max(n - 20, body_start), n):
        keep.add(i)
    return _assemble(lines, keep, limit)


def _fmt_read_file(result: str, args: dict[str, Any], limit: int) -> str:
    """Путь + N строк (header) + первые/последние 30 + все def/class/import/from."""
    lines = result.splitlines()
    if not lines:
        return result
    n = len(lines)
    keep: set[int] = {0}
    for i in range(1, min(1 + 30, n)):
        keep.add(i)
    for i in range(max(n - 30, 1), n):
        keep.add(i)
    for i, ln in enumerate(lines):
        if i == 0:
            continue
        if _DEF_RE.search(_strip_numbered(ln)):
            keep.add(i)
    return _assemble(lines, keep, limit)


def _fmt_git_diff(result: str, args: dict[str, Any], limit: int) -> str:
    """Тело diff выкидываем: список файлов + счётчик '+N -M'.

    NB: tools/git.py уже режет diff через _truncate(MAX_OUTPUT), поэтому
    для очень больших diff'ов счётчик может недосчитать. Приемлемо —
    точные числа есть в history_archive (search_history).
    """
    if not result.strip():
        return result
    files: list[str] = []
    adds = dels = 0
    for ln in result.splitlines():
        if ln.startswith("+++ b/"):
            f = ln[6:].strip()
            if f and f not in files:
                files.append(f)
        elif ln.startswith("+++ "):
            f = ln[4:].strip()
            if f and f != "/dev/null" and f not in files:
                files.append(f)
        elif ln.startswith("+") and not ln.startswith("+++"):
            adds += 1
        elif ln.startswith("-") and not ln.startswith("---"):
            dels += 1
    if not files and adds == 0 and dels == 0:
        return _fallback(result, args, limit)
    header = f"diff: {len(files)} файл(ов), +{adds} -{dels}"
    body = [f"  {f}" for f in files[:50]]
    if len(files) > 50:
        body.append(f"  …ещё {len(files) - 50} файлов…")
    text = header + ("\n" + "\n".join(body) if body else "")
    return _cap(text, limit)


def _fmt_search(result: str, args: dict[str, Any], limit: int) -> str:
    """Топ-10 хитов (rg: 'путь:строка:содержимое'), остальное — «ещё N скрыто»."""
    lines = result.splitlines()
    kept = [ln[:160] for ln in lines[:10]]
    if len(lines) > 10:
        kept.append(f"…ещё {len(lines) - 10} совпадений скрыто…")
    return _cap("\n".join(kept), limit)


def _fmt_search_history(result: str, args: dict[str, Any], limit: int) -> str:
    """Фрагменты через '\\n\\n'; каждый — '[role] text[:600]'."""
    blocks = [b for b in result.split("\n\n") if b.strip()]
    kept = [b[:200] for b in blocks[:10]]
    if len(blocks) > 10:
        kept.append(f"…ещё {len(blocks) - 10} фрагментов скрыто…")
    return _cap("\n\n".join(kept), limit)


def _fmt_list_files(result: str, args: dict[str, Any], limit: int) -> str:
    """Счётчик + первые 20 + «ещё N скрыто»."""
    lines = result.splitlines()
    if not lines:
        return result
    kept = lines[:20]
    if len(lines) > 20:
        kept.append(f"…ещё {len(lines) - 20} файлов скрыто…")
    return _cap("\n".join(kept), limit)


EXTRACTORS: dict[str, Extractor] = {
    "run_command": _fmt_run_command,
    "read_file": _fmt_read_file,
    "git_diff": _fmt_git_diff,
    "search": _fmt_search,
    "search_history": _fmt_search_history,
    "list_files": _fmt_list_files,
}

# tools, для которых formatter применяется всегда (не только при превышении):
# - git_diff: тело выкидываем даже для коротких diff'ов;
# - search/search_history/list_files: лимитируют количество хитов/строк,
#   а не объём, поэтому применяются всегда.
_ALWAYS_APPLY = frozenset({"git_diff", "search", "search_history", "list_files"})


def _resolve_limit(tool_name: str, default: int) -> int:
    """Приоритет: env_<NAME> > env_TOOL_MAX_CHARS > DEFAULT_LIMITS[tool] > default."""
    env_specific = os.getenv(f"TOOL_MAX_CHARS_{tool_name.upper()}")
    if env_specific:
        try:
            return int(env_specific)
        except ValueError:
            pass
    env_global = os.getenv("TOOL_MAX_CHARS")
    if env_global:
        try:
            return int(env_global)
        except ValueError:
            pass
    return DEFAULT_LIMITS.get(tool_name, default)


def extract(tool_name: str, args: dict[str, Any], result: str, max_chars: int) -> str:
    """Сжать tool-результат до per-tool лимита (env-переопределяемо).

    Короткий результат неизвестного/непокрытого formatter'ом tool'а
    возвращается как есть. Длинный — head(40%)/tail(40%) с маркером.
    Для git_diff formatter применяется всегда (тело diff не нужно).
    """
    limit = _resolve_limit(tool_name, max_chars)
    formatter = EXTRACTORS.get(tool_name)
    if formatter is not None and (tool_name in _ALWAYS_APPLY or len(result) > limit):
        return formatter(result, args, limit)
    if len(result) <= limit:
        return result
    return _fallback(result, args, limit)
