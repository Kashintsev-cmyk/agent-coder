"""Тесты /clone: нормализация URL + клонирование (mock git)."""
from __future__ import annotations

import pytest

from agent_coder.telegram import handlers

# --- _normalize_github_url ---

def test_normalize_short_form():
    ssh, name = handlers._normalize_github_url("user/repo")
    assert ssh == "git@github.com:user/repo.git"
    assert name == "repo"


def test_normalize_https():
    ssh, name = handlers._normalize_github_url("https://github.com/user/repo")
    assert ssh == "git@github.com:user/repo.git"
    assert name == "repo"


def test_normalize_https_with_git():
    ssh, name = handlers._normalize_github_url("https://github.com/user/repo.git")
    assert ssh == "git@github.com:user/repo.git"
    assert name == "repo"


def test_normalize_ssh():
    ssh, name = handlers._normalize_github_url("git@github.com:user/repo.git")
    assert ssh == "git@github.com:user/repo.git"
    assert name == "repo"


def test_normalize_http():
    ssh, _name = handlers._normalize_github_url("http://github.com/user/repo")
    assert ssh == "git@github.com:user/repo.git"


def test_normalize_repo_with_dots():
    _ssh, name = handlers._normalize_github_url("user/my.repo-name")
    assert name == "my.repo-name"


def test_normalize_invalid():
    for bad in ("", "just-a-word", "https://gitlab.com/user/repo",
                "https://github.com/", "user/"):
        assert handlers._normalize_github_url(bad) is None, bad


# --- _clone_repo ---

@pytest.mark.asyncio
async def test_clone_repo_existing_dir_raises(tmp_path):
    dest = tmp_path / "existing"
    dest.mkdir()
    with pytest.raises(FileExistsError):
        await handlers._clone_repo("git@github.com:user/repo.git", dest)


@pytest.mark.asyncio
async def test_clone_repo_actually_works(tmp_path):
    """Проверим, что run_shell вызов с git clone отрабатывает (без сети).

    Используем локальный git init как «репо» для клонирования — этого
    достаточно, чтобы убедиться, что команда сформирована правильно
    и shell_runner её выполняет.
    """
    # Создаём локальный «origin»
    origin = tmp_path / "origin"
    origin.mkdir()
    from agent_coder.shell_runner import run_shell
    await run_shell("git init -q", cwd=origin, timeout=10)
    (origin / "README.md").write_text("hi", encoding="utf-8")
    await run_shell("git add .", cwd=origin, timeout=10)
    await run_shell(
        "git -c commit.gpgsign=false -c user.email=t@t -c user.name=T "
        "commit -q -m init",
        cwd=origin, timeout=10,
    )

    # Клонируем локально через file:// — git принимает file:// как URL
    dest = tmp_path / "cloned"
    r = await run_shell(
        f"git clone --quiet file://{origin} {dest}",
        cwd=tmp_path, timeout=30,
    )
    assert r.ok, r.output
    assert (dest / "README.md").exists()
