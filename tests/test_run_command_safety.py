"""Тесты безопасности run_command: whitelist, pipe, секреты."""
from __future__ import annotations

from agent_coder.tools.run_command import _is_safe_command


def _cmd(s: str) -> bool:
    return _is_safe_command({"command": s})


def test_env_blocked():
    assert _cmd("env") is False
    assert _cmd("printenv") is False
    assert _cmd("printenv ROUTERAI_API_KEY") is False


def test_env_in_pipe_blocked():
    assert _cmd("ps aux | env") is False
    assert _cmd("cat /etc/hostname | printenv") is False


def test_awk_in_pipe_blocked():
    assert _cmd("ps aux | awk '{print $2}'") is False


def test_sed_in_pipe_blocked():
    assert _cmd("ls | sed 's/x/cmd/e'") is False


def test_proc_self_environ_blocked():
    assert _cmd("cat /proc/self/environ") is False


def test_proc_thread_self_environ_blocked():
    assert _cmd("cat /proc/thread-self/environ") is False


def test_proc_pid_environ_blocked():
    assert _cmd("cat /proc/1234/environ") is False


def test_parens_blocked():
    assert _cmd("ls (foo)") is False


def test_braces_blocked():
    assert _cmd("cat {a,b}") is False


def test_safe_ls():
    assert _cmd("ls -la /home") is True


def test_safe_cat():
    assert _cmd("cat /etc/hostname") is True


def test_safe_pipe():
    assert _cmd("ps aux | head -20") is True
    assert _cmd("cat /proc/meminfo | grep Mem") is True


def test_safe_systemctl():
    assert _cmd("systemctl status agent-coder-bot --no-pager") is True


def test_rm_not_safe():
    assert _cmd("rm -rf /") is False


def test_semicolon_not_safe():
    assert _cmd("ls; rm -rf /") is False


def test_backtick_not_safe():
    assert _cmd("cat `whoami`") is False
