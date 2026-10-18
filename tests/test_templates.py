"""Templates: доступны через importlib.resources и непустые."""

from __future__ import annotations

import importlib.resources

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
