"""B9.2g.5: date -s, ip link set, nvidia-smi -pl — reject."""

from __future__ import annotations


def test_safe_patterns_reject_mutations() -> None:
    from agent_coder.tools.run_command import _is_safe_command

    assert not _is_safe_command({"command": "date -s 2020-01-01"})
    assert not _is_safe_command({"command": "date -s 12:00"})
    assert not _is_safe_command({"command": "ip link set eth0 down"})
    assert not _is_safe_command({"command": "ip addr add 1.2.3.4/24 dev eth0"})
    assert not _is_safe_command({"command": "ip route del default"})
    assert not _is_safe_command({"command": "nvidia-smi -pl 200"})
    assert not _is_safe_command({"command": "nvidia-smi -pm 1"})


def test_safe_patterns_allow_read_only() -> None:
    from agent_coder.tools.run_command import _is_safe_command

    assert _is_safe_command({"command": "date"})
    assert _is_safe_command({"command": "date +%Y-%m-%d"})
    assert _is_safe_command({"command": "date -u +%s"})
    assert _is_safe_command({"command": "date -d '2024-01-01' +%s"})
    assert _is_safe_command({"command": "ip a"})
    assert _is_safe_command({"command": "ip -br addr"})
    assert _is_safe_command({"command": "ip link show"})
    assert _is_safe_command({"command": "ip route"})
    assert _is_safe_command({"command": "ip neigh"})
    assert _is_safe_command({"command": "nvidia-smi"})
    assert _is_safe_command({"command": "nvidia-smi -L"})
    assert _is_safe_command({"command": "nvidia-smi -q"})
    assert _is_safe_command(
        {"command": "nvidia-smi --query-gpu=name --format=csv"}
    )
