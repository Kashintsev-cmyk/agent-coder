"""Детерминированное извлечение из tool-результатов.

Идея: полный tool-вывод уходит в history_archive (его умеет искать
search_history), а в session.messages кладётся сжатая версия по правилам
для каждого tool. Без LLM — только regex по строкам + head/tail + счётчики.

ВАЖНО: read_file сюда НЕ входит. Модель явно запрашивает offset/limit —
это контракт, вырезать середину нельзя (иначе перечитывание → loop).
Объём read_file ограничен самим tool'ом (MAX_LINES/MAX_BYTES).

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
    "git_diff": 2048,
    "git_status": 2048,
    "git_log": 2048,
    "search": 2048,
    "search_history": 2048,
    "list_files": 1024,
}

# error-строки, которые нельзя терять в run_command-выводе
# head/tail (в строках) — выбраны по реальным сессиям: средний tool-вывод
# 30–60 строк, при 8+8 остаётся контекст, но не весь лог. Крупные игроки
# (Claude Code, Cline) режут ещё агрессивнее, но у нас в паре с
# history_archive — можно себе позволить сохранять чуть больше.
RUN_HEAD = 8
RUN_TAIL = 8
READ_HEAD = 15
READ_TAIL = 15
SEARCH_TOP = 5
SEARCH_SNIPPET = 120
HISTORY_TOP = 5
HISTORY_SNIPPET = 200
LIST_TOP = 10

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


# Команды-«листинги»: модель хочет ВЕСЬ список, head/tail режет его и
# провоцирует loop (ls tests/ = 65 строк, head 8 + tail 8 = 16, модель
# пробует ls tests/, ls -1, find... — сессия 75f25748). Для листинга
# оставляем до LISTING_MAX строк вместо head/tail.
_LISTING_RE = re.compile(
    r"^(?:ls|find|tree|du|git\s+ls-files|git\s+status|rg\s+--files)(?:\s|$)"
)
LISTING_MAX = 100


def _fmt_run_command(result: str, args: dict[str, Any], limit: int) -> str:
    """exit code + error-строки + head/tail контекст (листинги — целиком).

    Листинг (ls/find/tree/du/git ls-files/status) при exit_code=0 —
    отдаём до LISTING_MAX строк, без head/tail: модель хочет весь список.

    Особый случай — heredoc (`python3 - <<'PYEOF'`): команда многострочная,
    «$ cmd» занимает десятки строк, а exit code оказывается далеко внизу.
    Тело скрипта модели не нужно (она его сама сгенерировала в args),
    поэтому:
    - «$ cmd» обрезается до 200 симв. (первая строка);
    - «exit code: N» ищется regex'ом всегда;
    - маркеры «--- stdout ---» / «--- stderr ---» сохраняются;
    - error/Traceback/Warning сохраняются всегда;
    - head/tail — вокруг stdout-тела (после exit code).
    """
    lines = result.splitlines()
    n = len(lines)
    keep: set[int] = set()
    exit_idx: int | None = None
    for i, ln in enumerate(lines):
        if ln.startswith("exit code:"):
            exit_idx = i
            keep.add(i)
            break

    # Листинг? Команда из args["command"], exit_code=0 → отдаём тело целиком
    # (до LISTING_MAX), без head/tail. Head/tail для логов pytest/build.
    cmd = str(args.get("command") or "").strip()
    is_listing = bool(cmd) and _LISTING_RE.match(cmd) is not None
    ok_exit = exit_idx is not None and lines[exit_idx].strip() == "exit code: 0"
    if is_listing and ok_exit:
        body_start = exit_idx + 1
        # Пропускаем маркеры --- stdout ---/--- stderr --- при подсчёте.
        body = [
            ln
            for ln in lines[body_start:]
            if ln not in ("--- stdout ---", "--- stderr ---")
        ]
        if len(body) <= LISTING_MAX:
            # Влезает целиком — оставляем все body-строки.
            for i in range(body_start, n):
                keep.add(i)
            return _assemble(lines, keep, limit)
        # Не влезает — первые LISTING_MAX строк тела + маркер.
        kept = 0
        for i in range(body_start, n):
            if lines[i] in ("--- stdout ---", "--- stderr ---"):
                keep.add(i)
                continue
            if kept < LISTING_MAX:
                keep.add(i)
                kept += 1
            else:
                # маркер добавим ниже, в _assemble он не нужен
                break
        text = _assemble(lines, keep, limit)
        if kept < len(body):
            text += f"\n…[ещё {len(body) - kept} строк скрыто]…"
        return _cap(text, limit)
    # Первая строка ($ cmd) — обрезаем длинный heredoc, чтобы шапка
    # не съедала весь лимит.
    if n > 0:
        if len(lines[0]) > 200:
            lines[0] = lines[0][:200] + "…[команда сокращена]"
        keep.add(0)
    # Маркеры stdout/stderr — полезны для парсинга, стоят 1 строку.
    for i, ln in enumerate(lines):
        if ln in ("--- stdout ---", "--- stderr ---"):
            keep.add(i)
    # error-строки (и в stdout, и в stderr) — всегда.
    for i, ln in enumerate(lines):
        if _ERROR_RE.search(ln) and i != exit_idx:
            keep.add(i)
    body_start = (exit_idx + 1) if exit_idx is not None else (2 if n >= 2 else 0)
    for i in range(body_start, min(body_start + RUN_HEAD, n)):
        keep.add(i)
    for i in range(max(n - RUN_TAIL, body_start), n):
        keep.add(i)
    return _assemble(lines, keep, limit)


def _fmt_read_file(result: str, args: dict[str, Any], limit: int) -> str:
    """Оставлено для совместимости/экспериментов; в EXTRACTORS НЕ включён.

    Причина: read_file — явный контракт «дай строки offset..offset+limit»,
    вырезать середину нельзя (модель перечитывает → loop). См. комментарий
    у _ALWAYS_APPLY. В format_for_summary read_file идёт через общий
    fallback head/tail — там ок (резюме всё равно LLM-сжимается).
    """
    lines = result.splitlines()
    if not lines:
        return result
    n = len(lines)
    keep: set[int] = {0}
    for i in range(1, min(1 + READ_HEAD, n)):
        keep.add(i)
    for i in range(max(n - READ_TAIL, 1), n):
        keep.add(i)
    for i, ln in enumerate(lines):
        if i == 0:
            continue
        if _DEF_RE.search(_strip_numbered(ln)):
            keep.add(i)
    return _assemble(lines, keep, limit)


def _fmt_git_diff(result: str, args: dict[str, Any], limit: int) -> str:
    """Тело diff выкидываем: список файлов + счётчик '+N -M'.

    tools/git.py режет diff через _truncate(MAX_OUTPUT) ДО нас, поэтому
    для огромных diff'ов тело неполное. Детектим маркер _truncate
    ('... (обрезано, всего N символов)') и помечаем счётчик как
    неполный — иначе модель доверится неверному '+N -M'. Точные числа
    всегда есть в history_archive (search_history).
    """
    if not result.strip():
        return result
    lines = result.splitlines()
    truncated = bool(lines) and lines[-1].startswith("... (обрезано, всего ")
    files: list[str] = []
    adds = dels = 0
    for ln in lines:
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
    if truncated:
        header = (
            f"diff: {len(files)} файл(ов), +{adds} -{dels} "
            "(неполные: diff усечён до MAX_OUTPUT, точные — search_history)"
        )
    else:
        header = f"diff: {len(files)} файл(ов), +{adds} -{dels}"
    body = [f"  {f}" for f in files[:50]]
    if len(files) > 50:
        body.append(f"  …ещё {len(files) - 50} файлов…")
    text = header + ("\n" + "\n".join(body) if body else "")
    return _cap(text, limit)


def _fmt_search(result: str, args: dict[str, Any], limit: int) -> str:
    """Топ-10 хитов (rg: 'путь:строка:содержимое'), остальное — «ещё N скрыто»."""
    lines = result.splitlines()
    kept = [ln[:SEARCH_SNIPPET] for ln in lines[:SEARCH_TOP]]
    if len(lines) > SEARCH_TOP:
        kept.append(f"…ещё {len(lines) - SEARCH_TOP} совпадений скрыто…")
    return _cap("\n".join(kept), limit)


def _fmt_search_history(result: str, args: dict[str, Any], limit: int) -> str:
    """Фрагменты через '\\n\\n'; каждый — '[role] text[:600]'."""
    blocks = [b for b in result.split("\n\n") if b.strip()]
    kept = [b[:HISTORY_SNIPPET] for b in blocks[:HISTORY_TOP]]
    if len(blocks) > HISTORY_TOP:
        kept.append(f"…ещё {len(blocks) - HISTORY_TOP} фрагментов скрыто…")
    return _cap("\n\n".join(kept), limit)


def _fmt_list_files(result: str, args: dict[str, Any], limit: int) -> str:
    """Счётчик + первые 20 + «ещё N скрыто»."""
    lines = result.splitlines()
    if not lines:
        return result
    kept = lines[:LIST_TOP]
    if len(lines) > LIST_TOP:
        kept.append(f"…ещё {len(lines) - LIST_TOP} файлов скрыто…")
    return _cap("\n".join(kept), limit)


EXTRACTORS: dict[str, Extractor] = {
    "run_command": _fmt_run_command,
    "git_diff": _fmt_git_diff,
    "search": _fmt_search,
    "search_history": _fmt_search_history,
    "list_files": _fmt_list_files,
}

# tools, для которых formatter применяется всегда (не только при превышении):
# - run_command: хотим резать даже средние логи (30–60 строк) — хвост лога
#   почти никогда не несёт новых фактов, а volume копится;
# - git_diff: тело выкидываем даже для коротких diff'ов;
# - search/search_history/list_files: лимитируют количество хитов/строк,
#   а не объём, поэтому применяются всегда.
# read_file СОЗНАТЕЛЬНО НЕ входит: модель явно запросила offset/limit —
# это контракт, вырезать середину нельзя (иначе модель перечитывает
# файл с другими окнами → loop → упор в лимит итераций; регресс 5974e69).
# Объём read_file ограничен самим tool'ом (MAX_LINES/MAX_BYTES).
_ALWAYS_APPLY = frozenset(
    {"run_command", "git_diff", "search", "search_history", "list_files"}
)

# tools, которые extract НЕ трогает ВООБЩЕ (ни formatter, ни fallback):
# read_file — явный контракт offset/limit; объём ограничен tool'ом
# (MAX_LINES=2000 / MAX_BYTES=200k). Любая обрезка здесь = модель
# перечитывает файл с другими окнами → loop → упор в лимит итераций.
# tools-«контракты», которые extract НЕ трогает ВООБЩЕ:
# - read_file: явный offset/limit;
# - list_files: «покажи файлы» — модель хочет весь список, а не head/tail
#   (loop в 75f25748: tests/ = 65 файлов, extract оставлял 10 + «55 скрыто»,
#   модель пробовала ls/find/list_files по кругу). MAX_ENTRIES=500 в tool'е.
_NO_EXTRACT = frozenset({"read_file", "list_files"})


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


def extract(
    tool_name: str,
    args: dict[str, Any],
    result: str,
    max_chars: int,
    *,
    for_summary: bool = False,
) -> str:
    """Сжать tool-результат до per-tool лимита (env-переопределяемо).

    Короткий результат неизвестного/непокрытого formatter'ом tool'а
    возвращается как есть. Длинный — head(40%)/tail(40%) с маркером.
    Для git_diff formatter применяется всегда (тело diff не нужно).
    Для read_file (_NO_EXTRACT) — возвращается как есть, кроме
    for_summary=True: явный контракт offset/limit в живом цикле (обрезка
    → перечитывание → loop), но в резюме read_file — просто информация,
    его можно резать fallback'ом (иначе резюме раздувается до сотен КБ).
    """
    if tool_name in _NO_EXTRACT and not for_summary:
        return result
    limit = _resolve_limit(tool_name, max_chars)
    formatter = EXTRACTORS.get(tool_name)
    if formatter is not None and (tool_name in _ALWAYS_APPLY or len(result) > limit):
        return formatter(result, args, limit)
    if len(result) <= limit:
        return result
    return _fallback(result, args, limit)
