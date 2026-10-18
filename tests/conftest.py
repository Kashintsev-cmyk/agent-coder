"""Общие фикстуры для тестов agent-coder."""

from __future__ import annotations

import atexit
import os
import shutil
import tempfile

# ВАЖНО: до любого import agent_coder.
# project_utils._PROJECTS_DEFAULT_DIR = bots_dir() кэшируется при import,
# а github/commands копируют значение в свой namespace. Если env не задан
# в момент import — все копии указывают на реальный ~/bots, и тесты
# оставляют мусор (incident 2026-10-18: dup/test-proj/with-git).
_TEST_BOTS_DIR = tempfile.mkdtemp(prefix="agent-coder-test-bots-")
os.environ.setdefault("AGENT_CODER_BOTS_DIR", _TEST_BOTS_DIR)


@atexit.register
def _cleanup_test_bots_dir() -> None:
    if os.path.isdir(_TEST_BOTS_DIR):
        shutil.rmtree(_TEST_BOTS_DIR, ignore_errors=True)


import pytest

from agent_coder.config import config


@pytest.fixture(autouse=True)
def _isolated_sessions_dir(tmp_path, monkeypatch):
    """Изолировать каталог сессий, чтобы тесты не трогали реальные данные."""
    sessions_dir = tmp_path / "sessions"
    sessions_dir.mkdir()
    monkeypatch.setattr(config, "SESSIONS_DIR", sessions_dir)
    return sessions_dir
