"""Тесты группировки серверных op'ов."""
from __future__ import annotations

from agent_coder.server_ops import (
    SERVER_GROUPS,
    SERVER_OPS,
    op_group,
    ops_in_group,
)


def test_groups_listed():
    ids = [g for g, _ in SERVER_GROUPS]
    assert "resources" in ids
    assert "system" in ids
    assert "network" in ids
    assert "services" in ids
    assert "logs" in ids


def test_all_ops_have_valid_group():
    valid = {g for g, _ in SERVER_GROUPS}
    for op in SERVER_OPS:
        assert op_group(op.id) in valid, f"{op.id} → {op_group(op.id)}"


def test_ops_in_group_covers_all():
    seen: set[str] = set()
    for gid, _ in SERVER_GROUPS:
        for op in ops_in_group(gid):
            seen.add(op.id)
    expected = {op.id for op in SERVER_OPS}
    assert seen == expected, f"разница: {expected - seen}"


def test_ops_in_group_unknown_empty():
    assert ops_in_group("nonexistent") == []


def test_op_group_unknown_defaults_system():
    assert op_group("no-such-op") == "system"


def test_groups_not_empty():
    for gid, glabel in SERVER_GROUPS:
        ops = ops_in_group(gid)
        assert ops, f"группа {gid} ({glabel}) пуста"
