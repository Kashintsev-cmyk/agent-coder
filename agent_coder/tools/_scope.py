"""Проверка относительного пути на вхождение в область аудита (B11.3).

audit_scopes — список относительных путей от workspace (например ["bot/"]).
Пустой/None — без ограничений (ACT/PLAN/PATCH/VERIFY).
Scope "." / "/" / "" — весь workspace, allow-all (B9.2h).

M2 (2026-11-07): нормализация через PurePosixPath. Абсолютные пути и `..`
в parts — reject (bypass закрыт). "bot/" и "bot" эквивалентны (rstrip).
"./" и "." в parts отбрасываются.
"""

from __future__ import annotations

from pathlib import PurePosixPath


def _normalize(rel: str) -> str | None:
    """Нормализовать относительный путь. None — путь небезопасен (абс., `..`)."""
    if not rel:
        return ""
    p = PurePosixPath(rel)
    if p.is_absolute():
        return None
    if ".." in p.parts:
        return None
    return "/".join(part for part in p.parts if part not in (".", ""))


def in_scope(rel: str, scopes: list[str] | None) -> bool:
    if not scopes:
        return True
    # scope "." / "/" = "весь workspace" — явный allow-all (B9.2h: системные
    # диагностики `cat /etc/hosts` проходят). Проверяем ДО _normalize(rel):
    # absolute system paths при scope "." тоже разрешены.
    for s in scopes:
        if s.strip().rstrip("/") in ("", "."):
            return True
    norm = _normalize(rel)
    if norm is None:
        return False  # абс. или `..` — никогда не в scope
    for s in scopes:
        s_norm = _normalize(s.strip().rstrip("/"))
        if s_norm is None:
            continue  # битый scope — игнорируем
        if norm == s_norm or norm.startswith(s_norm + "/"):
            return True
    return False
