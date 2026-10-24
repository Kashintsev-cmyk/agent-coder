"""B11.3: audit_scopes enforced в read_file/list_files/search.

Регресс: на audit-04 (na_shtile, 32 файла) модель читала tests/,
.github/, .env.example при --scope bot/ (3 из 33 read_file вне scope).
Причина — scopes шли только в промт, инструменты не проверяли.
"""

from __future__ import annotations

import asyncio

import pytest

from agent_coder.tools._scope import in_scope
from agent_coder.tools.list_files import _execute as list_files_exec
from agent_coder.tools.read_file import _execute as read_file_exec
from agent_coder.tools.registry import ToolContext

NA = "/home/botuser/bots/na_shtile"


# ---------- in_scope unit ----------


@pytest.mark.parametrize(
    "rel,scopes,expected",
    [
        ("bot/main.py", ["bot/"], True),
        ("bot/main.py", ["bot"], True),
        ("bot/main.py", ["./bot/"], True),
        ("bot/deep/x.py", ["bot/"], True),
        ("tests/x.py", ["bot/"], False),
        ("botx/x.py", ["bot/"], False),  # префикс, но не граница пути
        ("anything", None, True),
        ("anything", [], True),
        ("x", ["."], True),
        ("x", [""], True),
        ("bot/main.py", ["bot/", "tests/"], True),
        ("tests/x.py", ["bot/", "tests/"], True),
        ("scripts/x.py", ["bot/", "tests/"], False),
    ],
)
def test_in_scope(rel: str, scopes: list[str] | None, expected: bool) -> None:
    assert in_scope(rel, scopes) is expected


# ---------- enforcement: read_file ----------


def test_read_file_blocks_out_of_scope() -> None:
    ctx = ToolContext(workspace=NA, mode="audit", audit_scopes=["bot/"])
    out = asyncio.run(read_file_exec({"path": "tests/conftest.py", "limit": 3}, ctx))
    assert out.startswith("Ошибка:")
    assert "вне области аудита" in out
    assert "bot/" in out


def test_read_file_allows_in_scope() -> None:
    ctx = ToolContext(workspace=NA, mode="audit", audit_scopes=["bot/"])
    out = asyncio.run(read_file_exec({"path": "bot/main.py", "limit": 3}, ctx))
    assert not out.startswith("Ошибка:")
    assert "bot/main.py" in out


def test_read_file_no_scopes_allows_all() -> None:
    ctx = ToolContext(workspace=NA, mode="audit", audit_scopes=None)
    out = asyncio.run(read_file_exec({"path": "tests/conftest.py", "limit": 3}, ctx))
    assert not out.startswith("Ошибка:")


# ---------- enforcement: list_files ----------


def test_list_files_blocks_out_of_scope() -> None:
    ctx = ToolContext(workspace=NA, mode="audit", audit_scopes=["bot/"])
    out = asyncio.run(list_files_exec({"path": "tests/"}, ctx))
    assert out.startswith("Ошибка:")
    assert "вне области аудита" in out


def test_list_files_blocks_dot_with_scopes() -> None:
    ctx = ToolContext(workspace=NA, mode="audit", audit_scopes=["bot/"])
    out = asyncio.run(list_files_exec({}, ctx))
    assert out.startswith("Ошибка:")
    assert "вне области аудита" in out


def test_list_files_allows_in_scope() -> None:
    ctx = ToolContext(workspace=NA, mode="audit", audit_scopes=["bot/"])
    out = asyncio.run(list_files_exec({"path": "bot/"}, ctx))
    assert not out.startswith("Ошибка:")
