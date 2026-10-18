"""Bootstrap нового проекта из agent_coder/templates/.

Core — scaffold_project(project, target=None). Не знает про UI:
CLI (agent-coder-scaffold) и Telegram (/scaffold) — тонкие адаптеры.
Существующие файлы не перезаписывает; непустой target -> error.
"""

from __future__ import annotations

import importlib.resources
import re
import subprocess
from pathlib import Path
from typing import Any

from agent_coder.standard.paths import bots_dir

_PACKAGE_NAME_RE = re.compile(r"^[a-z][a-z0-9_]*$")

_MAIN_PY = '''"""Entry point {project}."""


def main() -> None:
    print("hello from {project}")


if __name__ == "__main__":
    main()
'''

_SMOKE_TEST = '''"""Smoke test: проект собран, импортируется."""

from __future__ import annotations


def test_smoke() -> None:
    assert True
'''


def package_name(project: str) -> str:
    """snake_case от имени проекта; ValueError, если не валидный identifier."""
    pkg = project.replace("-", "_").replace(".", "_").lower()
    if not _PACKAGE_NAME_RE.match(pkg):
        raise ValueError(
            f"недопустимое имя пакета: {pkg!r} "
            f"(нужно [a-z][a-z0-9_]*, проекта {project!r})"
        )
    return pkg


def _read_template(name: str) -> str:
    files = importlib.resources.files("agent_coder.templates")
    return files.joinpath(name).read_text(encoding="utf-8")


def _render_pyproject(project: str, package: str, description: str) -> str:
    snippet = _read_template("pyproject-snippet.toml")
    snippet = snippet.replace('source = ["app"]', f'source = ["{package}"]')
    return (
        "[project]\n"
        f'name = "{project}"\n'
        'version = "0.1.0"\n'
        f'description = "{description}"\n'
        'requires-python = ">=3.12"\n'
        'license = "LicenseRef-Proprietary"\n'
        "\n"
        "[build-system]\n"
        'requires = ["setuptools>=68"]\n'
        'build-backend = "setuptools.build_meta"\n'
        "\n"
        "[tool.setuptools]\n"
        'py-modules = ["main"]\n'
        "\n"
        "[tool.setuptools.packages.find]\n"
        f'include = ["{package}*"]\n'
        "\n" + snippet
    )


def _render_readme(project: str, package: str) -> str:
    return (
        f"# {project}\n"
        "\n"
        "Описание проекта.\n"
        "\n"
        "## Установка\n"
        "\n"
        "    python3.12 -m venv .venv\n"
        "    source .venv/bin/activate\n"
        "    uv pip install -r requirements-dev.txt\n"
        "\n"
        "## Запуск\n"
        "\n"
        f"    python -m {package}\n"
        "\n"
        "## Структура\n"
        "\n"
        f"    {package}/        # код\n"
        "    tests/            # pytest\n"
        "    main.py           # entry point\n"
    )


def _write_if_missing(path: Path, content: str) -> bool:
    """Записать, если файла нет. Существующий не трогаем."""
    if path.is_file():
        return False
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return True


def _build_files(project: str, package: str, description: str) -> dict[str, str]:
    return {
        "pyproject.toml": _render_pyproject(project, package, description),
        "AGENTS.md": _read_template("AGENTS.md"),
        ".gitignore": _read_template(".gitignore-base"),
        "README.md": _render_readme(project, package),
        "requirements.in": "# Прод-зависимости\n",
        "requirements-dev.in": _read_template("requirements-dev-base.in"),
        ".github/workflows/ci.yml": _read_template("ci-template.yml"),
        f"{package}/__init__.py": f'"""{project} package."""\n',
        f"{package}/__main__.py": _MAIN_PY.format(project=project),
        "main.py": _MAIN_PY.format(project=project),
        "tests/__init__.py": "",
        "tests/test_smoke.py": _SMOKE_TEST,
    }


def _git_init(base: Path) -> str:
    """git init + add + initial commit. Возвращает статус."""
    try:
        subprocess.run(
            ["git", "init", "-q"],
            cwd=base,
            check=True,
            timeout=10,
            capture_output=True,
        )
        subprocess.run(
            ["git", "add", "."],
            cwd=base,
            check=True,
            timeout=10,
            capture_output=True,
        )
        subprocess.run(
            [
                "git",
                "-c",
                "commit.gpgsign=false",
                "-c",
                "user.name=agent-coder",
                "-c",
                "user.email=agent-coder@localhost",
                "commit",
                "-q",
                "-m",
                "Initial commit",
            ],
            cwd=base,
            check=True,
            timeout=10,
            capture_output=True,
        )
        return "initialized"
    except (subprocess.CalledProcessError, FileNotFoundError) as e:
        return f"failed: {e}"


def scaffold_project(
    project: str,
    target: Path | None = None,
    *,
    description: str = "",
    git_init: bool = True,
) -> dict[str, Any]:
    """Создать структуру проекта. Существующие файлы не трогает.

    Возвращает dict: project, package, path, created, skipped, git
    или error="target_not_empty".
    """
    package = package_name(project)
    base = (target if target is not None else bots_dir() / project).resolve()

    report: dict[str, Any] = {
        "project": project,
        "package": package,
        "path": str(base),
        "created": [],
        "skipped": [],
    }

    if base.exists() and any(base.iterdir()):
        report["error"] = "target_not_empty"
        return report

    base.mkdir(parents=True, exist_ok=True)

    for rel, content in _build_files(project, package, description).items():
        if _write_if_missing(base / rel, content):
            report["created"].append(rel)
        else:
            report["skipped"].append(rel)

    if git_init:
        report["git"] = _git_init(base)

    return report
