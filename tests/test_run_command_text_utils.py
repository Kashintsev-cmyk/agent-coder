"""B9.2g.6: sed/awk/rg/diff/cmp/comm в pipe — read-only гарантии."""

from __future__ import annotations

from agent_coder.tools.run_command import _is_safe_command


def _cmd(s: str) -> bool:
    return _is_safe_command({"command": s})


# --- allowed: печать/фильтрация ---


def test_sed_print_allowed() -> None:
    assert _cmd("ls | sed -n '1,5p'")
    assert _cmd("cat x.txt | sed -n '10,20p'")
    assert _cmd("cat x.txt | sed -n 's/foo/bar/p'")


def test_awk_print_allowed() -> None:
    assert _cmd("ps aux | awk '{print $2}'")
    assert _cmd("cat f | awk -F: '{print $1, $3}'")
    assert _cmd("cat f | awk '{print NR, $0}'")


def test_rg_readonly_allowed() -> None:
    assert _cmd("cat f | rg error")
    assert _cmd("rg -n TODO src/")


def test_diff_cmp_comm_allowed() -> None:
    assert _cmd("diff a.txt b.txt")
    assert _cmd("cmp a.txt b.txt")
    assert _cmd("comm -12 a.txt b.txt")


# --- blocked: запись/исполнение ---


def test_sed_mutations_blocked() -> None:
    assert not _cmd("ls | sed 's/x/y/'")
    assert not _cmd("ls | sed -i 's/x/y/'")
    assert not _cmd("ls | sed -n 's/x/y/w /tmp/out'")
    assert not _cmd("ls | sed -n '/x/r /etc/passwd'")
    assert not _cmd("ls | sed 's/x/cmd/e'")


def test_awk_exec_blocked() -> None:
    assert not _cmd("ps aux | awk '{system(\"reboot\")}'")
    assert not _cmd("ps aux | awk '{print > \"/tmp/pwn\"}'")
    assert not _cmd("ps aux | awk '{\"ls\" | getline x}'")
    assert not _cmd("ps aux | awk -f /tmp/s.awk")


def test_rg_pre_blocked() -> None:
    assert not _cmd("rg --pre 'rm -rf' pat")
    assert not _cmd("cat f | rg --pre x pat")
    assert not _cmd("cat f | rg --pre-glob '*.go' pat")
