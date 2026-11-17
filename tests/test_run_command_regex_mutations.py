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
    assert _is_safe_command({"command": "nvidia-smi --query-gpu=name --format=csv"})


# --- B9.2g.7: пишущие флаги у read-only команд ---


def test_mutating_flags_rejected() -> None:
    """dmesg/tree/nvidia-smi/file: флаги записи и очистки — не read-only."""
    from agent_coder.tools.run_command import _is_safe_command

    for cmd in (
        # dmesg: очистка и правка ring buffer'а ядра
        "dmesg -C",
        "dmesg -c",
        "dmesg -D",
        "dmesg -E",
        "dmesg -n 1",
        "dmesg --clear",
        "dmesg --read-clear",
        "dmesg --console-off",
        "dmesg -w",  # follow: висит до таймаута
        "dmesg --follow",
        # tree: запись вывода в файл
        "tree -o out.txt /etc",
        "tree -L 2 -o /tmp/x .",
        "tree --output /tmp/x .",
        # nvidia-smi: запись в файл, бесконечный цикл
        "nvidia-smi -q --filename=/tmp/x.txt",
        "nvidia-smi --filename /tmp/x.txt",
        "nvidia-smi -l 1",
        "nvidia-smi --loop=2",
        "nvidia-smi -f /tmp/x",
        # file: компиляция magic-файла на диск
        "file -C -m mymagic",
        "file -m mymagic",
    ):
        assert not _is_safe_command({"command": cmd}), cmd


def test_mutating_flags_read_only_still_allowed() -> None:
    """Дыры закрыты, но legit-вызовы тех же команд остались без approval."""
    from agent_coder.tools.run_command import _is_safe_command

    for cmd in (
        "dmesg",
        "tree .",
        "tree -L 2 -d .",
        "nvidia-smi",
        "nvidia-smi -q",
        "nvidia-smi -L",
        "nvidia-smi --query-gpu=name --format=csv",
        "file README.md",
    ):
        assert _is_safe_command({"command": cmd}), cmd


def test_git_branch_upstream_flag_rejected() -> None:
    """--set-upstream-to/-u/--edit-description пишут .git/config."""
    from agent_coder.tools.run_command import _is_safe_command

    assert not _is_safe_command(
        {"command": "git branch --set-upstream-to=origin/master"}
    )
    assert not _is_safe_command({"command": "git branch -u origin/master"})
    assert not _is_safe_command({"command": "git branch --edit-description"})
    assert not _is_safe_command({"command": "git branch --unset-upstream"})
    # listing остаётся read-only
    for cmd in (
        "git branch",
        "git branch -a",
        "git branch -v",
        "git branch -vv",
    ):
        assert _is_safe_command({"command": cmd}), cmd
