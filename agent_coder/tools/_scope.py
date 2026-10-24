"""Проверка относительного пути на вхождение в область аудита (B11.3).

audit_scopes — список относительных путей от workspace (например ["bot/"]).
Пустой/None — без ограничений (ACT/PLAN/PATCH/VERIFY).

Правило: rel == scope или rel начинается с scope + "/".
"bot/" и "bot" эквивалентны (rstrip). "./" в начале игнорируется.
"""

from __future__ import annotations


def in_scope(rel: str, scopes: list[str] | None) -> bool:
    if not scopes:
        return True
    norm = rel.lstrip("./")
    for s in scopes:
        s_clean = s.strip().rstrip("/").lstrip("./")
        if not s_clean:
            return True  # scope "." или "/" — весь workspace
        if norm == s_clean or norm.startswith(s_clean + "/"):
            return True
    return False
