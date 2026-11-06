"""Templates sync (F17): канон — agent_coder/templates/.

_shared-dev/project-template не сравниваем: это отдельный репо,
используется weather-bot/futures_3.0 напрямую, вне зоны agent-coder.
Тест ловит: пропавший/пустой шаблон, сломанный importlib.resources.
"""

from __future__ import annotations

import re
from pathlib import Path

from agent_coder import scaffold

TEMPLATES = Path(scaffold.__file__).parent / "templates"
REQUIRED = (
    "AGENTS.md",
    "ci-template.yml",
    ".gitignore-base",
    "pyproject-snippet.toml",
    "requirements-dev-base.in",
)


def test_templates_dir_exists():
    assert TEMPLATES.is_dir()


def test_init_marker_present():
    assert (TEMPLATES / "__init__.py").exists(), (
        "нет __init__.py — importlib.resources.files('agent_coder.templates') сломается"
    )


def test_required_present_and_nonempty():
    missing = [n for n in REQUIRED if not (TEMPLATES / n).exists()]
    empty = [n for n in REQUIRED if (TEMPLATES / n).exists() and (TEMPLATES / n).stat().st_size == 0]
    assert not missing, f"нет шаблонов: {missing}"
    assert not empty, f"пустые шаблоны: {empty}"


def test_scaffold_reads_existing_templates():
    src = Path(scaffold.__file__).read_text(encoding="utf-8")
    names = set(re.findall(r'_read_template\("([^"]+)"\)', src))
    assert names, "scaffold.py: не найдено _read_template(...) — тест ослеп"
    missing = sorted(n for n in names if not (TEMPLATES / n).exists())
    assert not missing, f"scaffold.py читает отсутствующие шаблоны: {missing}"
