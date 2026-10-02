"""Общие фикстуры для тестов agent-coder."""
from __future__ import annotations

import pytest

from agent_coder.config import config


@pytest.fixture(autouse=True)
def _isolated_sessions_dir(tmp_path, monkeypatch):
    """Изолировать каталог сессий, чтобы тесты не трогали реальные данные."""
    sessions_dir = tmp_path / "sessions"
    sessions_dir.mkdir()
    monkeypatch.setattr(config, "SESSIONS_DIR", sessions_dir)
    return sessions_dir
