"""Защита боевого sessions/ от записи во время тестов (A10).

Incident 2026-11-17..20: `pytest --noconftest` (и любой прогон без
tests/conftest.py) не выставляет `SESSIONS_DIR`, поэтому
`config.SESSIONS_DIR` указывает на боевой `<repo>/sessions`. Тесты,
дергающие `Session.save()`, оставляли там мусор — 116 файлов с
фикстурой `model="m"`, `workspace="/tmp"`. conftest.py эту дыру
закрывает, но только для прогонов, которые его грузят.

Контракт `_resolve_sessions_dir`:
- `SESSIONS_DIR` задан в env → уважаем как есть (прод, systemd, CI);
- идёт прогон pytest → временный каталог вне репозитория (не боевой);
- иначе → `<repo>/sessions` (штатное поведение бота).
"""

from __future__ import annotations

import pytest

from agent_coder.config import _resolve_sessions_dir


def test_env_var_wins(monkeypatch, tmp_path):
    """Явный SESSIONS_DIR в окружении — приоритет над любым автодетектом."""
    monkeypatch.setenv("SESSIONS_DIR", str(tmp_path / "explicit"))
    monkeypatch.setenv("PYTEST_CURRENT_TEST", "tests/test_x.py::test_y")
    assert _resolve_sessions_dir(tmp_path) == tmp_path / "explicit"


def test_under_pytest_does_not_use_prod_dir(monkeypatch, tmp_path):
    """Под pytest без env — не боевой каталог, а временный."""
    monkeypatch.delenv("SESSIONS_DIR", raising=False)
    monkeypatch.setenv("PYTEST_CURRENT_TEST", "tests/test_x.py::test_y")
    resolved = _resolve_sessions_dir(tmp_path)
    assert resolved != tmp_path / "sessions", "тесты пишут в боевой sessions/"
    assert "pytest" in str(resolved).lower()


def test_prod_behaviour_when_not_testing(monkeypatch, tmp_path):
    """Вне тестов — штатный `<repo>/sessions`."""
    monkeypatch.delenv("SESSIONS_DIR", raising=False)
    monkeypatch.delenv("PYTEST_CURRENT_TEST", raising=False)
    monkeypatch.setattr("agent_coder.config._running_under_pytest", lambda: False)
    assert _resolve_sessions_dir(tmp_path) == tmp_path / "sessions"


@pytest.mark.parametrize("marker", ["PYTEST_CURRENT_TEST", "PYTEST_VERSION"])
def test_pytest_markers_detected(monkeypatch, tmp_path, marker):
    """Любой из маркеров pytest включает защиту; мусор отключает её."""
    monkeypatch.delenv("SESSIONS_DIR", raising=False)
    monkeypatch.delenv("PYTEST_CURRENT_TEST", raising=False)
    monkeypatch.delenv("PYTEST_VERSION", raising=False)
    monkeypatch.setenv(marker, "1")
    assert _resolve_sessions_dir(tmp_path) != tmp_path / "sessions"
