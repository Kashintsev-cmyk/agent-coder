"""M6: docker read-only только через sudo -n /usr/bin/docker."""

from __future__ import annotations

import pytest

from agent_coder.tools.run_command import _is_safe_command


def _safe(cmd: str) -> bool:
    return _is_safe_command({"command": cmd})


@pytest.mark.parametrize(
    "cmd",
    [
        "sudo -n /usr/bin/docker ps",
        "sudo -n /usr/bin/docker ps -a",
        "sudo -n /usr/bin/docker images",
        "sudo -n /usr/bin/docker stats --no-stream",
        "sudo -n /usr/bin/docker inspect abc",
        "sudo -n /usr/bin/docker logs abc",
        "sudo -n /usr/bin/docker logs --tail 50 abc",
    ],
)
def test_docker_readonly_allowed(cmd):
    assert _safe(cmd) is True


@pytest.mark.parametrize(
    "cmd",
    [
        "sudo -n /usr/bin/docker run alpine",
        "sudo -n /usr/bin/docker exec abc sh",
        "sudo -n /usr/bin/docker rm abc",
        "sudo -n /usr/bin/docker stop abc",
        "sudo /usr/bin/docker ps",
        "sudo -n docker ps",
        "docker run alpine",
        "sudo -n rm /tmp/x",
    ],
)
def test_docker_write_or_wrong_form_blocked(cmd):
    assert _safe(cmd) is False


@pytest.mark.parametrize(
    "cmd",
    [
        "sudo -n /usr/bin/docker ps; rm -rf /",
        "sudo -n /usr/bin/docker ps && rm",
        "sudo -n /usr/bin/docker ps > /tmp/x",
        "sudo -n /usr/bin/docker ps | cat",
    ],
)
def test_sudo_docker_shell_injection_blocked(cmd):
    assert _safe(cmd) is False


def test_docker_ps_without_sudo_still_readonly():
    """Whitelist пропускает `docker ps` (read-only), но в рантайме без
    docker-группы он упадёт `permission denied` — это функциональная
    деградация, не security. Здесь фиксируем контракт whitelist."""
    assert _safe("docker ps") is True
