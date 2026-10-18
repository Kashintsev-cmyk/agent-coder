"""Публичный фасад над check_project_standard.

Переходный шаг: ядро (225 строк, 15+ проверок) живёт в
tools/check_project_standard.py и пока не переписано на Issue.
Здесь — единственная точка импорта приватных имён. Если ядро
переименуют, сломается только этот модуль.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from agent_coder.standard.issue import Issue
from agent_coder.tools.check_project_standard import (
    _collect_issues,
    _summarize,
)


def collect_issues(path: Path) -> list[Issue]:
    """Собрать отклонения проекта. Read-only, без сети и git."""
    return [Issue.from_dict(d) for d in _collect_issues(Path(path))]


def summarize(issues: list[Issue]) -> dict[str, Any]:
    """score/majors/minors/issues — тот же формат, что у JSON tool'а."""
    return _summarize([i.to_dict() for i in issues])
