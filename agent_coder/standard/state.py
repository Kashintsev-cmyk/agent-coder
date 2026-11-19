"""Machine-состояние проекта: <state_dir>/<project>/state.json.

Отдельно от .md (view) и .notes.md (manual). Timestamp живёт здесь —
.md детерминирован, иначе каждый migrate даёт git diff (F13).

Расположение по умолчанию — ~/.agent-coder/projects/<project>/state.json
(вне репо, как авто-память Claude Code). Переопределяется через
AGENT_CODER_STATE_DIR.
"""

from __future__ import annotations

import json
import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

ENV_STATE_DIR = "AGENT_CODER_STATE_DIR"
_DEFAULT_STATE_DIR = Path.home() / ".agent-coder" / "projects"


def state_dir() -> Path:
    raw = os.environ.get(ENV_STATE_DIR)
    return Path(raw).expanduser() if raw else _DEFAULT_STATE_DIR


def state_path(project: str) -> Path:
    return state_dir() / project / "state.json"


def read_state(project: str) -> dict[str, Any] | None:
    p = state_path(project)
    if not p.is_file():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


class StateWriteError(RuntimeError):
    """Не удалось записать state.json (нет прав, read-only ФС, каталог занят).

    Явный тип вместо голого OSError: migrate-хендлер показывает пользователю
    внятное сообщение, а не падает в общий dp.errors(). Типичная причина в
    проде — каталог state не перечислен в ReadWritePaths systemd-юнита
    (ProtectHome=read-only даёт EROFS).
    """

    def __init__(self, path: Path, cause: OSError) -> None:
        self.path = path
        self.cause = cause
        hint = os.environ.get(ENV_STATE_DIR) or str(_DEFAULT_STATE_DIR)
        super().__init__(
            f"не удалось записать state в {path}: {cause.strerror or cause} "
            f"(каталог state: {hint}; проверь права и ReadWritePaths юнита)"
        )


def _write_atomic(p: Path, payload: str) -> Path:
    """tmp + replace, с переводом OSError в StateWriteError."""
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_suffix(".json.tmp")
        tmp.write_text(payload, encoding="utf-8")
        tmp.replace(p)
    except OSError as exc:
        raise StateWriteError(p, exc) from exc
    return p


def write_state(project: str, state: dict[str, Any]) -> Path:
    """Атомарная запись: tmp + replace. Возвращает путь.

    Raises:
        StateWriteError: каталог/файл недоступен для записи.
    """
    p = state_path(project)
    payload = json.dumps(state, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    return _write_atomic(p, payload)


def build_state(project: str, summary: dict[str, Any]) -> dict[str, Any]:
    """Собрать state из summary. Timestamp — здесь, не в .md."""
    return {
        "project": project,
        "generated_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "score": summary["score"],
        "majors": summary["majors"],
        "minors": summary["minors"],
        "issues": summary["issues"],
    }


def write_if_changed(path: Path, content: str) -> bool:
    """Записать файл, только если содержимое изменилось.

    Возвращает True, если файл записан, False — если совпал с диском.
    Нужно для идемпотентного migrate: повторный прогон не трогает
    mtime и не даёт git diff (F13).
    """
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.is_file() and path.read_text(encoding="utf-8") == content:
            return False
        path.write_text(content, encoding="utf-8")
    except OSError as exc:
        raise StateWriteError(path, exc) from exc
    return True
