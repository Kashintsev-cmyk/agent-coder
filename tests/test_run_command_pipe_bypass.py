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


def test_pipe_bypass_b92g5_output_flags() -> None:
    """B9.2g.5: запись на диск через флаги в pipe — блок."""
    from agent_coder.tools.run_command import _is_safe_command

    assert not _is_safe_command({"command": "git log --output=/tmp/x -2 | cat"})
    assert not _is_safe_command({"command": "git archive -o /tmp/z.tar HEAD | cat"})
    assert not _is_safe_command({"command": "git bundle create /tmp/b.bundle HEAD | cat"})
    assert not _is_safe_command({"command": "git format-patch -o /tmp/p HEAD | cat"})
    assert not _is_safe_command({"command": "git fast-export --all --output=/tmp/e | cat"})
    assert not _is_safe_command({"command": "find . -fprint /tmp/x | cat"})


def test_pipe_bypass_b92g5_no_regression() -> None:
    """B9.2g.5: легитимный -o не сломан (ps/grep — не запись)."""
    from agent_coder.tools.run_command import _is_safe_command

    assert _is_safe_command({"command": "ps -o pid,cmd -p 1 | cat"})
    assert _is_safe_command({"command": "grep -o foo src/x.py | wc -l"})
    assert _is_safe_command({"command": "git log --oneline | cat"})
