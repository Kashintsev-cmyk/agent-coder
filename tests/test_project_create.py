"""Тесты создания проекта (/new <name>)."""
from __future__ import annotations

import pytest

from agent_coder.telegram import handlers
from agent_coder.telegram.handlers import project_utils as _pu

# --- validate ---

def test_validate_name_ok():
    for n in ("my-project", "my_project", "myproject", "my.project",
              "abc123", "a", "x" * 40):
        assert handlers._validate_project_name(n) is None, n


def test_validate_name_bad():
    for n in ("", ".hidden", "-dash", "my/proj", "my proj",
              "x" * 41, "y!", "имя", "..", "."):
        assert handlers._validate_project_name(n) is not None, n


# --- create on disk ---

@pytest.mark.asyncio
async def test_create_project_structure(tmp_path, monkeypatch):
    monkeypatch.setattr(_pu, "_PROJECTS_DEFAULT_DIR", tmp_path)

    path = await handlers._create_project_on_disk("test-proj")

    assert path.exists()
    assert path.is_dir()
    assert path == tmp_path / "test-proj"
    assert (path / "README.md").exists()
    assert (path / ".gitignore").exists()
    assert (path / ".git").is_dir()

    readme = (path / "README.md").read_text(encoding="utf-8")
    assert "# test-proj" in readme
    assert "python -m test_proj" in readme

    gi = (path / ".gitignore").read_text(encoding="utf-8")
    assert "__pycache__/" in gi
    assert ".venv/" in gi
    assert ".env" in gi


@pytest.mark.asyncio
async def test_create_project_twice_raises(tmp_path, monkeypatch):
    monkeypatch.setattr(_pu, "_PROJECTS_DEFAULT_DIR", tmp_path)
    await handlers._create_project_on_disk("dup")
    with pytest.raises(FileExistsError):
        await handlers._create_project_on_disk("dup")


@pytest.mark.asyncio
async def test_create_project_initial_commit(tmp_path, monkeypatch):
    """Если git config настроен — должен быть initial commit."""
    monkeypatch.setattr(_pu, "_PROJECTS_DEFAULT_DIR", tmp_path)
    path = await handlers._create_project_on_disk("with-git")

    from agent_coder.shell_runner import run_shell
    r = await run_shell("git log --oneline", cwd=path, timeout=10)
    assert (path / ".git").is_dir()
    if r.ok and r.stdout.strip():
        assert "Initial commit" in r.stdout
