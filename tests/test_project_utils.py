"""_create_project_on_disk: scaffold + check_project_standard = ok."""

from __future__ import annotations

from pathlib import Path

import pytest

from agent_coder.standard import collect_issues, summarize
from agent_coder.telegram.handlers import project_utils


@pytest.fixture()
def env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, Path]:
    """monkeypatch module attr: _PROJECTS_DEFAULT_DIR — контракт обёртки."""
    bots = tmp_path / "bots"
    bots.mkdir()
    monkeypatch.setattr(project_utils, "_PROJECTS_DEFAULT_DIR", bots)
    return {"bots": bots}


async def test_create_project_returns_path(env: dict[str, Path]) -> None:
    path = await project_utils._create_project_on_disk("demo")
    assert path == (env["bots"] / "demo").resolve()
    assert (path / "pyproject.toml").is_file()
    assert (path / "requirements-dev.txt").is_file()
    assert (path / ".git").is_dir()


async def test_create_project_passes_standard(env: dict[str, Path]) -> None:
    """Ключевое: scaffolded проект = 0 issues (ok)."""
    path = await project_utils._create_project_on_disk("demo")
    summary = summarize(collect_issues(path))
    assert summary["score"] == "ok", summary["issues"]
    assert summary["majors"] == 0
    assert summary["minors"] == 0


async def test_create_project_hyphen(env: dict[str, Path]) -> None:
    path = await project_utils._create_project_on_disk("my-bot")
    assert path.name == "my-bot"
    assert (path / "my_bot" / "__init__.py").is_file()


async def test_create_project_nonempty_raises(env: dict[str, Path]) -> None:
    """Старый контракт: FileExistsError на существующем проекте."""
    target = env["bots"] / "demo"
    target.mkdir()
    (target / "x.txt").write_text("x", encoding="utf-8")
    with pytest.raises(FileExistsError, match="уже существует"):
        await project_utils._create_project_on_disk("demo")


async def test_create_project_invalid_package_raises(env: dict[str, Path]) -> None:
    with pytest.raises(ValueError, match="недопустимое имя"):
        await project_utils._create_project_on_disk("123abc")
