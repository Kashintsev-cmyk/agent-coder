"""scaffold_project: структура, pyproject, source=<pkg>, идемпотентность."""

from __future__ import annotations

import tomllib
from pathlib import Path

import pytest

from agent_coder.scaffold import package_name, scaffold_project


def test_package_name_simple() -> None:
    assert package_name("demo") == "demo"


def test_package_name_hyphen_and_dot() -> None:
    assert package_name("my-bot.v2") == "my_bot_v2"


def test_package_name_starts_with_digit_rejected() -> None:
    with pytest.raises(ValueError):
        package_name("123abc")


def test_scaffold_creates_expected_files(tmp_path: Path) -> None:
    base = tmp_path / "demo"
    r = scaffold_project("demo", target=base, git_init=False)
    assert "error" not in r
    assert r["package"] == "demo"
    for rel in (
        "pyproject.toml",
        "AGENTS.md",
        ".gitignore",
        "README.md",
        "requirements.in",
        "requirements-dev.in",
        ".github/workflows/ci.yml",
        "demo/__init__.py",
        "demo/__main__.py",
        "main.py",
        "tests/__init__.py",
        "tests/test_smoke.py",
    ):
        assert (base / rel).is_file(), f"missing {rel}"


def test_pyproject_valid_and_pkg_substituted(tmp_path: Path) -> None:
    base = tmp_path / "demo"
    scaffold_project("demo", target=base, git_init=False)
    with open(base / "pyproject.toml", "rb") as f:
        data = tomllib.load(f)
    assert data["project"]["name"] == "demo"
    assert data["project"]["requires-python"] == ">=3.12"
    assert data["project"]["license"] == "LicenseRef-Proprietary"
    assert data["tool"]["coverage"]["run"]["source"] == ["demo"]
    assert data["tool"]["setuptools"]["packages"]["find"]["include"] == ["demo*"]
    assert data["tool"]["ruff"]["target-version"] == "py312"


def test_pyproject_no_app_placeholder(tmp_path: Path) -> None:
    base = tmp_path / "demo"
    scaffold_project("demo", target=base, git_init=False)
    text = (base / "pyproject.toml").read_text(encoding="utf-8")
    assert '"app"' not in text


def test_hyphen_project_uses_underscore_package(tmp_path: Path) -> None:
    base = tmp_path / "my-bot"
    r = scaffold_project("my-bot", target=base, git_init=False)
    assert r["package"] == "my_bot"
    assert (base / "my_bot/__init__.py").is_file()
    with open(base / "pyproject.toml", "rb") as f:
        data = tomllib.load(f)
    assert data["tool"]["coverage"]["run"]["source"] == ["my_bot"]


def test_nonempty_target_errors(tmp_path: Path) -> None:
    base = tmp_path / "demo"
    base.mkdir()
    (base / "existing.txt").write_text("x", encoding="utf-8")
    r = scaffold_project("demo", target=base, git_init=False)
    assert r.get("error") == "target_not_empty"


def test_existing_files_not_overwritten(tmp_path: Path) -> None:
    base = tmp_path / "demo"
    base.mkdir()
    (base / "README.md").write_text("custom", encoding="utf-8")
    # target not empty -> error, README untouched
    r = scaffold_project("demo", target=base, git_init=False)
    assert r.get("error") == "target_not_empty"
    assert (base / "README.md").read_text(encoding="utf-8") == "custom"


def test_empty_existing_target_ok(tmp_path: Path) -> None:
    base = tmp_path / "demo"
    base.mkdir()
    r = scaffold_project("demo", target=base, git_init=False)
    assert "error" not in r
    assert (base / "pyproject.toml").is_file()


def test_description_in_pyproject(tmp_path: Path) -> None:
    base = tmp_path / "demo"
    scaffold_project("demo", target=base, description="My bot", git_init=False)
    with open(base / "pyproject.toml", "rb") as f:
        data = tomllib.load(f)
    assert data["project"]["description"] == "My bot"


def test_git_init_creates_repo(tmp_path: Path) -> None:
    """Если git есть — repo создан, initial commit существует."""
    import shutil

    if shutil.which("git") is None:
        pytest.skip("git not available")
    base = tmp_path / "demo"
    r = scaffold_project("demo", target=base, git_init=True)
    assert r.get("git") == "initialized", r.get("git")
    assert (base / ".git").is_dir()
    # .gitignore включает AGENTS.md и т.п. — проверяем, что файлы попали в коммит
    import subprocess

    out = subprocess.run(
        ["git", "ls-files"], cwd=base, capture_output=True, text=True, check=True
    ).stdout
    assert "pyproject.toml" in out
    assert "AGENTS.md" in out
