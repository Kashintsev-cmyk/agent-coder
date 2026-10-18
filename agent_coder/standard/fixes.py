"""FixSpec: как чинить issue. Данные, а не текст — ACT применяет по id."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

FixKind = Literal["edit_pyproject", "create_file", "edit_ci", "manual"]


@dataclass(frozen=True, slots=True)
class FixSpec:
    """Рецепт фикса. hint — что сказать человеку/агенту; kind — категория."""

    issue_id: str
    kind: FixKind
    hint: str


FIX_SPECS: dict[str, FixSpec] = {
    "missing_file": FixSpec(
        "missing_file",
        "create_file",
        "создай файл (шаблон в `agent_coder/templates/`)",
    ),
    "missing_tests_dir": FixSpec(
        "missing_tests_dir",
        "create_file",
        "создай `tests/` с хотя бы одним `test_*.py`",
    ),
    "pyproject_unparseable": FixSpec(
        "pyproject_unparseable",
        "edit_pyproject",
        "исправь TOML-синтаксис (проверь через tomllib)",
    ),
    "pytest_testpaths_missing": FixSpec(
        "pytest_testpaths_missing",
        "edit_pyproject",
        'добавь [tool.pytest.ini_options] с testpaths = ["tests"]',
    ),
    "ruff_line_length_not_88": FixSpec(
        "ruff_line_length_not_88",
        "edit_pyproject",
        "в [tool.ruff] поставь line-length = 88",
    ),
    "ruff_select_incomplete": FixSpec(
        "ruff_select_incomplete",
        "edit_pyproject",
        'в [tool.ruff.lint] добавь select = ["E","F","W","I","UP","B","C4","SIM"]',
    ),
    "coverage_source_placeholder_app": FixSpec(
        "coverage_source_placeholder_app",
        "edit_pyproject",
        'в [tool.coverage.run] замени source = ["app"] на имя пакета',
    ),
    "requires_python_below_312": FixSpec(
        "requires_python_below_312",
        "edit_pyproject",
        'в [project] поставь requires-python = ">=3.12" (3.10/3.11 EOL)',
    ),
    "license_legacy_dict": FixSpec(
        "license_legacy_dict",
        "edit_pyproject",
        'в [project] замени license = { text = "..." } на license = "<SPDX>" (PEP 639)',
    ),
    "ruff_target_version_not_py312": FixSpec(
        "ruff_target_version_not_py312",
        "edit_pyproject",
        'в [tool.ruff] поставь target-version = "py312"',
    ),
    "setuptools_packages_find_missing": FixSpec(
        "setuptools_packages_find_missing",
        "edit_pyproject",
        'добавь [tool.setuptools.packages.find] с include = ["<pkg>*"]; '
        "иначе pip install -e . падает",
    ),
    "ci_missing_with_remote": FixSpec(
        "ci_missing_with_remote",
        "edit_ci",
        "скопируй agent_coder/templates/ci-template.yml в .github/workflows/ci.yml",
    ),
    "ci_matrix_has_eol_python": FixSpec(
        "ci_matrix_has_eol_python",
        "edit_ci",
        'убери 3.10/3.11 из матрицы, оставь ["3.12", "3.13"]',
    ),
    "ruff_format_not_applied": FixSpec(
        "ruff_format_not_applied",
        "manual",
        "применить `ruff format .` и закоммитить style-only",
    ),
    "ci_matrix_missing_312": FixSpec(
        "ci_matrix_missing_312",
        "edit_ci",
        "добавь 3.12 (и 3.13) в python-version CI-матрицы",
    ),
}


def fix_for(issue_id: str) -> FixSpec | None:
    return FIX_SPECS.get(issue_id)
