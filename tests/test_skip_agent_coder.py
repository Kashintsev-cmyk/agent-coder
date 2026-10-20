"""Тест B11.2c: .agent-coder/ не виден в list_files/search (R2).

Аудит-стейт живёт в .agent-coder/audit-{slug}/. Если не скипать —
модель читает свой же findings.md/coverage.md, шум в аудите.
"""

from __future__ import annotations

from agent_coder.tools.list_files import SKIP_DIRS, _is_skipped_dir


def test_agent_coder_in_skip_dirs():
    assert ".agent-coder" in SKIP_DIRS


def test_is_skipped_dir_agent_coder():
    assert _is_skipped_dir(".agent-coder") is True
    assert _is_skipped_dir("audit-myproject-deadbeef") is False
