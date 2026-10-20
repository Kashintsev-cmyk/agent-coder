"""B11.3: --scope / --scope-file для AUDIT."""

from __future__ import annotations

import argparse
from pathlib import Path

from agent_coder.__main__ import _resolve_scopes, _validate_audit_flags
from agent_coder.agent.audit_prompt import build_audit_prompt
from agent_coder.agent.prompts import Mode, system_prompt
from agent_coder.agent.session import Session


def _args(**kw) -> argparse.Namespace:
    base: dict[str, object] = {
        "audit": True,
        "plan": False,
        "resume_audit": False,
        "fresh_audit": False,
        "scope": None,
        "scope_file": None,
    }
    base.update(kw)
    return argparse.Namespace(**base)


def test_scope_requires_audit() -> None:
    assert _validate_audit_flags(_args(audit=False, scope=["a"])) is not None
    assert _validate_audit_flags(_args(audit=False, scope_file="f.txt")) is not None


def test_scope_flags_mutually_exclusive() -> None:
    err = _validate_audit_flags(_args(scope=["a"], scope_file="f.txt"))
    assert err is not None and "взаимоисключающие" in err


def test_resolve_scopes_reads_scope_file(tmp_path: Path) -> None:
    (tmp_path / "a.py").write_text("x = 1\n", encoding="utf-8")
    (tmp_path / "b.py").write_text("y = 2\n", encoding="utf-8")
    lst = tmp_path / "scopes.txt"
    lst.write_text("# comment\n\na.py\nb.py\n", encoding="utf-8")
    scopes, err = _resolve_scopes(
        str(tmp_path), _args(scope_file=str(lst)), current=None
    )
    assert err is None
    assert scopes == ["a.py", "b.py"]


def test_resolve_scopes_rejects_outside_workspace(tmp_path: Path) -> None:
    scopes, err = _resolve_scopes(
        str(tmp_path), _args(scope=["../etc/passwd"]), current=None
    )
    assert scopes is None and err is not None and "вне workspace" in err


def test_resolve_scopes_rejects_nonexistent(tmp_path: Path) -> None:
    scopes, err = _resolve_scopes(str(tmp_path), _args(scope=["nope.py"]), current=None)
    assert scopes is None and err is not None and "не существует" in err


def test_build_audit_prompt_injects_scopes() -> None:
    out = build_audit_prompt(workspace="/tmp/ws", scopes=["a.py", "sub/b.py"])
    assert "## Область аудита (B11.3)" in out
    assert "- `a.py`" in out and "- `sub/b.py`" in out
    # без scopes — блока нет
    out2 = build_audit_prompt(workspace="/tmp/ws")
    assert "Область аудита" not in out2


def test_system_prompt_audit_uses_scopes() -> None:
    out = system_prompt(Mode.AUDIT, "/tmp/ws", audit_scopes=["only.py"])
    assert "Область аудита (B11.3)" in out
    assert "- `only.py`" in out


def test_session_roundtrip_keeps_audit_scopes() -> None:
    s = Session.new(workspace="/tmp/ws", model="m", mode=Mode.AUDIT)
    s.audit_scopes = ["a.py"]
    s2 = Session.from_dict(s.to_dict())
    assert s2.audit_scopes == ["a.py"]
    # backward compat: старые сессии без поля
    d = s.to_dict()
    d.pop("audit_scopes")
    assert Session.from_dict(d).audit_scopes == []
