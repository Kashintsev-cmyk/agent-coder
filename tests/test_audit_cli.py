"""Тесты CLI-интеграции audit (B11.2b).

_validate_audit_flags и _start_audit — чистые хелперы из __main__.
"""

from __future__ import annotations

from types import SimpleNamespace

from agent_coder.__main__ import _start_audit, _validate_audit_flags
from agent_coder.agent import audit_storage as storage
from agent_coder.agent.prompts import Mode
from agent_coder.agent.session import Session


def _args(**kw):
    return SimpleNamespace(
        audit=kw.get("audit", False),
        resume_audit=kw.get("resume_audit", False),
        fresh_audit=kw.get("fresh_audit", False),
    )


def _new_audit_session(workspace):
    return Session.new(
        workspace=workspace,
        model="deepseek/deepseek-v4-flash",
        mode=Mode.AUDIT,
    )


def test_validate_ok():
    assert _validate_audit_flags(_args(audit=True)) is None


def test_validate_requires_audit():
    err = _validate_audit_flags(_args(resume_audit=True))
    assert err is not None
    assert "--audit" in err


def test_validate_mutually_exclusive():
    err = _validate_audit_flags(_args(audit=True, resume_audit=True, fresh_audit=True))
    assert err is not None
    assert "исключ" in err


def test_start_audit_loads_state(tmp_path):
    workspace = str(tmp_path)
    s1 = _new_audit_session(workspace)
    s1.work_state["findings"] = ["a.py:1 — old"]
    storage.save(workspace, s1, storage.load(workspace))

    s2 = _new_audit_session(workspace)
    loaded = _start_audit(workspace, s2, fresh=False)

    assert loaded["findings"] == ["a.py:1 — old"]
    assert "a.py:1 — old" in s2.work_state["findings"]


def test_start_audit_fresh_ignores_state(tmp_path):
    workspace = str(tmp_path)
    s1 = _new_audit_session(workspace)
    s1.work_state["findings"] = ["a.py:1 — old"]
    storage.save(workspace, s1, storage.load(workspace))

    s2 = _new_audit_session(workspace)
    loaded = _start_audit(workspace, s2, fresh=True)

    assert loaded["findings"] == []
    assert s2.work_state.get("findings") in (None, [])
