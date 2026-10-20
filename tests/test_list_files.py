"""B9.5: list_files не показывает кеши/venv-мусор (3e2513be: 30K символов)."""

from __future__ import annotations

import asyncio
from pathlib import Path

from agent_coder.agent.prompts import Mode
from agent_coder.tools.list_files import _execute
from agent_coder.tools.registry import ToolContext


def _run(workspace: Path, path: str = ".") -> str:
    ctx = ToolContext(workspace=str(workspace), mode=Mode.ACT, yolo=True)
    return asyncio.run(_execute({"path": path}, ctx))


def _mk_tree(root: Path) -> None:
    (root / "app").mkdir()
    (root / "app" / "main.py").write_text("x", encoding="utf-8")
    (root / "tests").mkdir()
    (root / "tests" / "test_x.py").write_text("x", encoding="utf-8")
    # мусор, который модель в 3e2513be получала:
    (root / ".mypy_cache").mkdir()
    (root / ".mypy_cache" / "cache.db").write_text("x", encoding="utf-8")
    (root / ".pytest_cache").mkdir()
    (root / ".pytest_cache" / "v.db").write_text("x", encoding="utf-8")
    (root / ".venv310").mkdir()
    (root / ".venv310" / "lib.py").write_text("x", encoding="utf-8")
    (root / "pkg.egg-info").mkdir()
    (root / "pkg.egg-info" / "PKG-INFO").write_text("x", encoding="utf-8")
    (root / "__pycache__").mkdir()
    (root / "__pycache__" / "x.pyc").write_text("x", encoding="utf-8")


def test_list_files_skips_caches(tmp_path: Path) -> None:
    """B9.5: .mypy_cache/.pytest_cache/.venv310/*.egg-info не в output."""
    _mk_tree(tmp_path)
    out = _run(tmp_path)
    assert "app/main.py" in out
    assert "tests/test_x.py" in out
    assert ".mypy_cache" not in out
    assert ".pytest_cache" not in out
    assert ".venv310" not in out
    assert "egg-info" not in out
    assert "__pycache__" not in out


def test_list_files_venv_prefix_all_versions(tmp_path: Path) -> None:
    """B9.5: префикс .venv ловит .venv310/.venv311/.venv312."""
    for name in (".venv310", ".venv311", ".venv312", ".venv"):
        d = tmp_path / name
        d.mkdir()
        (d / "x.py").write_text("x", encoding="utf-8")
    (tmp_path / "main.py").write_text("x", encoding="utf-8")
    out = _run(tmp_path)
    assert "main.py" in out
    for name in (".venv310", ".venv311", ".venv312"):
        assert name not in out


def test_list_files_keeps_normal_dirs(tmp_path: Path) -> None:
    """B9.5: не переусердствовали — обычные директории остаются."""
    _mk_tree(tmp_path)
    out = _run(tmp_path)
    assert "app" in out
    assert "tests" in out
