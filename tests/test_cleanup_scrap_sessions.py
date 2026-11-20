"""scripts/cleanup_scrap_sessions.py: чистит фикстуры pytest, не трогает работу.

Критерий — workspace в tempdir (session_index.is_scrap_workspace), а не цена:
196 из 203 фикстур имеют непустой work_state (`findings: f0,f1,f2`) и по
критерию «ценная сессия» из cleanup_cheap_sessions.py прошли бы мимо.
"""

from __future__ import annotations

import importlib.util
import sys
import tempfile
from pathlib import Path

import pytest

from agent_coder.agent import session_index as _si
from agent_coder.agent.prompts import Mode
from agent_coder.agent.session import Session
from agent_coder.agent.session_index import INDEX_FILENAME, SessionIndex
from agent_coder.config import config

_SCRIPT = (
    Path(__file__).resolve().parent.parent / "scripts" / "cleanup_scrap_sessions.py"
)


def _load_script():
    spec = importlib.util.spec_from_file_location("cleanup_scrap_sessions", _SCRIPT)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture()
def env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, Path]:
    # Имя отличается от tmp_path/"sessions": такой каталог уже создаёт
    # autouse-фикстура _isolated_sessions_dir в conftest.
    sessions_dir = tmp_path / "scrap-sessions"
    sessions_dir.mkdir()
    monkeypatch.setattr(config, "SESSIONS_DIR", sessions_dir)
    monkeypatch.setattr(_si, "index", SessionIndex(path=sessions_dir / INDEX_FILENAME))
    return {"sessions": sessions_dir, "root": tmp_path}


def _make(workspace: str, *, title: str, valuable: bool = False) -> Session:
    s = Session.new(workspace=workspace, model="m", mode=Mode.ACT, title=title)
    s.messages = [{"role": "user", "content": "задача"}]
    if valuable:
        s.work_state = {"findings": ["f0"], "todo": [], "user_requests": []}
    s.save()
    return s


# tmp_path сам лежит в tempdir (pytest-of-botuser), поэтому «настоящий»
# workspace задаём строкой вне tempdir — существовать на диске он не обязан.
_REAL_WS = "/home/botuser/bots/proj"
_SCRAP_WS = tempfile.gettempdir()


def test_removes_only_scrap_workspaces(env, monkeypatch, capsys):
    scrap = _make(_SCRAP_WS, title="scratch", valuable=True)
    real = _make(_REAL_WS, title="настоящая работа")

    monkeypatch.setattr(sys, "argv", ["cleanup", "--no-backup"])
    rc = _load_script().main()

    assert rc == 0
    # Фикстура удалена, несмотря на «ценный» work_state
    assert Session.load(scrap.id) is None
    # Настоящая сессия цела
    assert Session.load(real.id) is not None
    out = capsys.readouterr().out
    assert "Удалено сессий: 1" in out


def test_dry_run_keeps_everything(env, monkeypatch, capsys):
    scrap = _make(_SCRAP_WS, title="scratch")

    monkeypatch.setattr(sys, "argv", ["cleanup", "--dry-run"])
    rc = _load_script().main()

    assert rc == 0
    assert Session.load(scrap.id) is not None
    assert "[dry-run]" in capsys.readouterr().out


def test_backup_keeps_json_copy(env, monkeypatch):
    scrap = _make(_SCRAP_WS, title="scratch")
    backup = env["root"] / "backup"

    monkeypatch.setattr(sys, "argv", ["cleanup", "--backup-dir", str(backup)])
    rc = _load_script().main()

    assert rc == 0
    assert Session.load(scrap.id) is None
    assert (backup / f"{scrap.id}.json").exists(), "бэкап не создан"


def test_nothing_to_clean(env, monkeypatch, capsys):
    _make(_REAL_WS, title="работа")

    monkeypatch.setattr(sys, "argv", ["cleanup", "--no-backup"])
    rc = _load_script().main()

    assert rc == 0
    assert "чистить нечего" in capsys.readouterr().out


def test_system_tempdir_is_scrap(monkeypatch):
    """Реальный tempdir (не только /tmp) считается мусором."""
    assert _si.is_scrap_workspace(tempfile.gettempdir())
    assert _si.is_scrap_workspace(tempfile.gettempdir() + "/sub/dir")
    assert not _si.is_scrap_workspace("/home/botuser/bots/agent-coder")
    # Похожий префикс, но не tempdir — не мусор
    assert not _si.is_scrap_workspace(tempfile.gettempdir() + "_backup")
