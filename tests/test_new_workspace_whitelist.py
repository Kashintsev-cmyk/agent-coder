"""M5: /new <path> — whitelist TELEGRAM_PROJECTS_DIRS + запрет кода бота."""

from __future__ import annotations

import agent_coder.telegram.handlers.project_utils as pu
from agent_coder.config import config
from agent_coder.telegram.handlers.project_utils import _workspace_allowed


def test_allowed_under_bots(tmp_path, monkeypatch):
    bots = tmp_path / "bots"
    bots.mkdir()
    monkeypatch.setattr(config, "TELEGRAM_PROJECTS_DIRS", [bots])
    assert _workspace_allowed(bots / "foo")[0] is True


def test_allowed_under_projects(tmp_path, monkeypatch):
    projects = tmp_path / "projects"
    projects.mkdir()
    monkeypatch.setattr(config, "TELEGRAM_PROJECTS_DIRS", [projects])
    assert _workspace_allowed(projects / "bar")[0] is True


def test_rejected_outside(tmp_path, monkeypatch):
    bots = tmp_path / "bots"
    bots.mkdir()
    monkeypatch.setattr(config, "TELEGRAM_PROJECTS_DIRS", [bots])
    assert _workspace_allowed(tmp_path / "etc")[0] is False


def test_rejected_own_code(tmp_path, monkeypatch):
    own = tmp_path / "agent-coder"
    fake_file = own / "agent_coder" / "telegram" / "handlers" / "project_utils.py"
    fake_file.parent.mkdir(parents=True)
    fake_file.touch()
    monkeypatch.setattr(pu, "__file__", str(fake_file))
    # own включён в "разрешённые корни" — всё равно должен быть отклонён
    monkeypatch.setattr(config, "TELEGRAM_PROJECTS_DIRS", [tmp_path])
    ok, reason = _workspace_allowed(own)
    assert ok is False
    assert "собственный код" in reason


def test_rejected_parent_escape(tmp_path, monkeypatch):
    bots = tmp_path / "bots"
    bots.mkdir()
    monkeypatch.setattr(config, "TELEGRAM_PROJECTS_DIRS", [bots])
    assert _workspace_allowed(bots / ".." / "etc")[0] is False
