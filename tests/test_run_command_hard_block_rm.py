"""B9.2g.4: _HARD_BLOCK ловит rm -r -f (раздельные флаги)."""

from __future__ import annotations


def test_hard_block_rm_split_flags() -> None:
    from agent_coder.tools.run_command import _is_destructive_rm

    assert _is_destructive_rm("rm -r -f /")
    assert _is_destructive_rm("rm -f -r /")
    assert _is_destructive_rm("rm -rf /")
    assert _is_destructive_rm("rm -fr /")
    assert _is_destructive_rm("rm --recursive --force /")
    assert _is_destructive_rm("rm -r --force /")
    assert _is_destructive_rm("rm -rf /bin")
    assert _is_destructive_rm("rm -r -f /*")
    assert _is_destructive_rm("rm -rf ~")
    assert not _is_destructive_rm("rm -rf ./build")
    assert not _is_destructive_rm("rm -rf /home/user/project")
    assert not _is_destructive_rm("rm -r /tmp/x")
