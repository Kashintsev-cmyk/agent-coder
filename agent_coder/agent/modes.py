"""Режимы работы агента. Единственное определение Mode (SSOT)."""

from __future__ import annotations

from enum import Enum


class Mode(str, Enum):
    PLAN = "plan"
    ACT = "act"
    AUDIT = "audit"
