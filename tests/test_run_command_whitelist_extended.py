"""Расширение whitelist: системные/текстовые read-only утилиты + git diff/blame."""

import pytest

from agent_coder.tools.run_command import _is_safe_command


def safe(c: str) -> bool:
    return _is_safe_command({"command": c})


@pytest.mark.parametrize("c", [
    "b2sum f", "cksum f",
    "lscpu", "lscpu -e", "lsmem", "lsmod", "lspci", "lspci -v", "lsusb",
    "pidof python",
    "strings f", "strings -n 6 f", "paste a b", "fold -w 80 f",
    "whereis python", "type ls", "basename /a/b", "dirname /a/b",
])
def test_extended_allowed(c):
    assert safe(c), c


@pytest.mark.parametrize("c", [
    "lscpu > f", "strings f | tee out",
    "paste a b; rm x",
])
def test_extended_blocked_mutations(c):
    assert not safe(c), c


def test_extended_pipe():
    assert safe("strings f | head")
    assert safe("lscpu | grep MHz")
    assert safe("b2sum f | sort")


@pytest.mark.parametrize("c", [
    "git diff", "git diff HEAD~1", "git blame f",
])
def test_git_content_still_blocked(c):
    """Политика: diff/blame/show читают содержимое — только через git_diff tool."""
    assert not safe(c), c
