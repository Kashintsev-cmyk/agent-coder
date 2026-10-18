"""Templates: доступны через importlib.resources и непустые."""

from __future__ import annotations

import importlib.resources
import subprocess

EXPECTED = {
    "AGENTS.md",
    "ci-template.yml",
    ".gitignore-base",
    "pyproject-snippet.toml",
    "requirements-dev-base.in",
}


def test_templates_dir_lists_expected() -> None:
    files = importlib.resources.files("agent_coder.templates")
    names = {p.name for p in files.iterdir() if p.name != "__init__.py"}
    missing = EXPECTED - names
    assert not missing, f"missing templates: {missing}"


def test_templates_nonempty() -> None:
    files = importlib.resources.files("agent_coder.templates")
    for name in EXPECTED:
        content = files.joinpath(name).read_text(encoding="utf-8")
        assert content.strip(), f"{name} is empty"


def test_snippet_targets_py312_and_uv() -> None:
    files = importlib.resources.files("agent_coder.templates")
    snippet = files.joinpath("pyproject-snippet.toml").read_text(encoding="utf-8")
    assert "py312" in snippet or ">=3.12" in snippet, "snippet must target py312"
    ci = files.joinpath("ci-template.yml").read_text(encoding="utf-8")
    assert "3.12" in ci and "3.13" in ci, "ci matrix must be 3.12/3.13"
    reqs = files.joinpath("requirements-dev-base.in").read_text(encoding="utf-8")
    assert "uv" in reqs.lower() or "pip-tools" not in reqs, "reqs must be uv-based"


def test_templates_tracked_in_git() -> None:
    """F21: шаблоны должны быть в git, иначе CI-клон их не увидит.

    Локально файл может быть на диске, но gitignored (как было с
    templates/AGENTS.md: локально 3 passed, в CI 2 failed).
    """
    top = subprocess.run(
        ["git", "rev-parse", "--show-toplevel"],
        capture_output=True,
        text=True,
        timeout=5,
        check=False,
    )
    if top.returncode != 0:
        import pytest

        pytest.skip("not a git repo")
    out = subprocess.run(
        ["git", "ls-files", "agent_coder/templates/"],
        cwd=top.stdout.strip(),
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )
    assert out.returncode == 0, out.stderr
    tracked = {line.split("/")[-1] for line in out.stdout.splitlines()}
    missing = EXPECTED - tracked
    assert not missing, f"templates missing from git: {missing}"
