"""B9.2g.2 (BLOCKER): pipe-bypass — mutating subcmd + \n + hard-block."""

from __future__ import annotations


def test_pipe_bypass_blocked() -> None:
    from agent_coder.tools.run_command import _is_safe_command

    assert not _is_safe_command({"command": "docker rm -f x | cat"})
    assert not _is_safe_command({"command": "systemctl restart foo | cat"})
    assert not _is_safe_command({"command": "ip link set eth0 down | cat"})
    assert not _is_safe_command({"command": "date -s 2020-01-01 | cat"})
    assert not _is_safe_command({"command": "sort -o /tmp/x | cat"})
    assert not _is_safe_command({"command": "find . -delete | cat"})
    assert not _is_safe_command({"command": "ls | cat\nrm -rf x"})
    assert not _is_safe_command({"command": "ls | rm -rf /"})


def test_pipe_filters_still_allowed() -> None:
    from agent_coder.tools.run_command import _is_safe_command

    assert _is_safe_command({"command": "ps aux | grep python"})
    assert _is_safe_command({"command": "cat file.txt | sort | uniq -c"})
    assert _is_safe_command({"command": "docker ps | grep web"})
    assert _is_safe_command({"command": "systemctl status bot | grep active"})
