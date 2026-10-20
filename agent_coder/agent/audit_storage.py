"""Persistent audit state (B11.2).

Каталог: ``{workspace}/.agent-coder/audit-{slug}/``. Файлы:

- ``findings.md`` — append-only, «- файл:строка — текст»
- ``coverage.md`` — append-only, «- путь — digest»
- ``todo.md``     — append-only, «- [ ] текст»
- ``meta.json``   — overwrite атомарно (tmp + os.replace)

Контракт:

- ``load(workspace) -> dict`` — ``{"findings", "coverage", "todo", "meta"}``;
  нет каталога → пустая структура.
- ``save(workspace, session, loaded)`` — append-only по diff
  ``session.work_state["findings"] - loaded["findings"]``; ключ дедупа —
  «файл:строка» (B9.4). ``coverage`` — из ``read_paths``, только новые
  пути. ``meta.json`` — atomic overwrite.
- ``mark_read(workspace, paths)`` — append в ``coverage.md``.

R1: при первом создании каталога — append ``.agent-coder/`` в
``workspace/.gitignore`` (идемпотентно).
R4: ``meta.json`` пишется атомарно, чтобы не оставить полу-записанный
файл при падении.

Только AUDIT пишет сюда (интеграция — B11.2b). ACT/PLAN не трогают.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import tempfile
import time
from pathlib import Path
from typing import Any

AUDIT_ROOT = ".agent-coder"
STATE_DIR_PREFIX = "audit-"

_FINDING_KEY_RE = re.compile(r"([\w./-]+:\d+)")


def _slug(workspace: str) -> str:
    """Стабильный slug: basename + sha1(path)[:8].

    ``hash()`` не годится — нестабилен между запусками (PYTHONHASHSEED).
    """
    ws = Path(workspace).resolve()
    name = re.sub(r"[^\w.-]+", "-", ws.name).strip("-") or "project"
    digest = hashlib.sha1(str(ws).encode("utf-8")).hexdigest()[:8]
    return f"{name}-{digest}"


def project_dir(workspace: str) -> Path:
    return Path(workspace) / AUDIT_ROOT / f"{STATE_DIR_PREFIX}{_slug(workspace)}"


def _ensure_gitignore(workspace: str) -> None:
    gi = Path(workspace) / ".gitignore"
    line = f"{AUDIT_ROOT}/"
    if gi.is_file():
        text = gi.read_text(encoding="utf-8", errors="replace")
        if any(raw.strip() == line for raw in text.splitlines()):
            return
        if text and not text.endswith("\n"):
            text += "\n"
        text += line + "\n"
    else:
        text = line + "\n"
    gi.write_text(text, encoding="utf-8")


def _empty_state() -> dict[str, Any]:
    return {"findings": [], "coverage": {}, "todo": [], "meta": {}}


def _finding_key(item: str) -> str:
    m = _FINDING_KEY_RE.search(item or "")
    return m.group(1) if m else (item or "").strip()


def _atomic_write_json(path: Path, data: dict[str, Any]) -> None:
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=".meta.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def load(workspace: str) -> dict[str, Any]:
    pd = project_dir(workspace)
    if not pd.is_dir():
        return _empty_state()

    state = _empty_state()

    fm = pd / "findings.md"
    if fm.is_file():
        state["findings"] = [
            line.strip().lstrip("- ").strip()
            for line in fm.read_text(encoding="utf-8", errors="replace").splitlines()
            if line.strip() and line.strip() != "-"
        ]

    cm = pd / "coverage.md"
    if cm.is_file():
        for line in cm.read_text(encoding="utf-8", errors="replace").splitlines():
            line = line.strip().lstrip("- ").strip()
            if not line:
                continue
            if " — " in line:
                path, digest = line.split(" — ", 1)
                state["coverage"][path.strip()] = digest.strip()
            else:
                state["coverage"][line] = ""

    tm = pd / "todo.md"
    if tm.is_file():
        state["todo"] = [
            line.strip().removeprefix("- [ ]").strip()
            for line in tm.read_text(encoding="utf-8", errors="replace").splitlines()
            if line.strip().startswith("- [ ]")
        ]

    mj = pd / "meta.json"
    if mj.is_file():
        try:
            state["meta"] = json.loads(mj.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            state["meta"] = {}

    return state


def save(workspace: str, session, loaded: dict[str, Any]) -> None:
    """Append-only save. ``loaded`` — результат ``load()`` до старта AUDIT.

    Повторный save в той же сессии не дублирует findings: diff считается
    против ``loaded``, а не против последнего save. Вызывать один раз в
    конце AUDIT (B11.2b).
    """
    pd = project_dir(workspace)
    pd.mkdir(parents=True, exist_ok=True)
    _ensure_gitignore(workspace)

    loaded_findings = list(loaded.get("findings", [])) if loaded else []
    loaded_keys = {_finding_key(item) for item in loaded_findings}

    ws = session.work_state or {}
    current_findings = list(ws.get("findings", []))
    new_findings = [
        item for item in current_findings if _finding_key(item) not in loaded_keys
    ]

    if new_findings:
        fm = pd / "findings.md"
        with fm.open("a", encoding="utf-8") as f:
            for item in new_findings:
                f.write(f"- {item}\n")

    loaded_cov = dict(loaded.get("coverage", {})) if loaded else {}
    read_paths = session.read_paths or {}
    new_cov = {p: d for p, d in read_paths.items() if p not in loaded_cov}

    if new_cov:
        cm = pd / "coverage.md"
        with cm.open("a", encoding="utf-8") as f:
            for path, digest in new_cov.items():
                f.write(f"- {path} — {digest}\n" if digest else f"- {path}\n")

    loaded_todo = list(loaded.get("todo", [])) if loaded else []
    current_todo = list(ws.get("todo") or [])
    new_todo = [item for item in current_todo if item not in loaded_todo]

    if new_todo:
        tm = pd / "todo.md"
        with tm.open("a", encoding="utf-8") as f:
            for item in new_todo:
                f.write(f"- [ ] {item}\n")

    loaded_meta = (loaded.get("meta") or {}) if loaded else {}
    coverage_count = len(loaded_cov) + len(new_cov)
    total_files = int(loaded.get("total_files", 0)) if loaded else 0
    meta = {
        "workspace": str(Path(workspace).resolve()),
        "slug": _slug(workspace),
        "updated": time.time(),
        "sessions": int(loaded_meta.get("sessions", 0)) + 1,
        "findings_count": len(loaded_findings) + len(new_findings),
        "coverage_count": coverage_count,
        "total_files": total_files,
        "coverage_remaining": max(0, total_files - coverage_count)
        if total_files
        else 0,
    }
    _atomic_write_json(pd / "meta.json", meta)


def mark_read(workspace: str, paths: dict[str, str]) -> None:
    """Append ``paths`` в ``coverage.md``. Для инструментов AUDIT (B11.4)."""
    pd = project_dir(workspace)
    pd.mkdir(parents=True, exist_ok=True)
    _ensure_gitignore(workspace)
    cm = pd / "coverage.md"
    with cm.open("a", encoding="utf-8") as f:
        for path, digest in paths.items():
            f.write(f"- {path} — {digest}\n" if digest else f"- {path}\n")


def _count_py_files(workspace: str, scopes: list[str] | None = None) -> int:
    """Сколько .py в scope (R15: прогресс «покрыто X/Y»).

    scopes — относительные пути от workspace (как session.audit_scopes).
    None/[] — весь workspace. Prunning через _is_skipped_dir (SKIP_DIRS из
    list_files): .venv, .venv310, __pycache__, .agent-coder и т.п.
    """
    from agent_coder.tools.list_files import _is_skipped_dir

    root = Path(workspace).resolve()
    roots = [root / s for s in scopes] if scopes else [root]
    total = 0
    for r in roots:
        if not r.is_dir():
            continue
        for _dirpath, dirnames, filenames in os.walk(r):
            dirnames[:] = [d for d in dirnames if not _is_skipped_dir(d)]
            total += sum(1 for f in filenames if f.endswith(".py"))
    return total


def prepare(workspace: str, *, fresh: bool = False) -> dict[str, Any]:
    """Старт AUDIT: ``fresh=True`` — удалить каталог state, затем ``load()``.

    Для CLI-флага ``--fresh-audit`` (B11.2b). При ``fresh=False`` —
    просто ``load()``, если каталога нет — пустое состояние.
    """
    if fresh:
        pd = project_dir(workspace)
        if pd.is_dir():
            shutil.rmtree(pd)
    return load(workspace)
