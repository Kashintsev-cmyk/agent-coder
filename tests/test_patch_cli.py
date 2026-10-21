"""Тесты --patch CLI (B9.2g): валидация + one-shot run_patch."""

from __future__ import annotations

from types import SimpleNamespace

from agent_coder.__main__ import _validate_audit_flags


def _args(**kw):
    base = {
        "audit": False,
        "plan": False,
        "patch": False,
        "resume_audit": False,
        "fresh_audit": False,
        "scope": None,
        "scope_file": None,
    }
    base.update(kw)
    return SimpleNamespace(**base)


def test_patch_ok():
    assert _validate_audit_flags(_args(patch=True)) is None


def test_patch_incompatible_with_audit():
    err = _validate_audit_flags(_args(patch=True, audit=True))
    assert err is not None
    assert "несовместим" in err


def test_patch_incompatible_with_plan():
    err = _validate_audit_flags(_args(patch=True, plan=True))
    assert err is not None
    assert "несовместим" in err
