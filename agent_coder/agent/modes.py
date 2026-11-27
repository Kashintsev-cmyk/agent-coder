"""Режимы работы агента. Единственное определение Mode (SSOT)."""

from __future__ import annotations

from enum import StrEnum


class Mode(StrEnum):
    PLAN = "plan"
    ACT = "act"
    AUDIT = "audit"
