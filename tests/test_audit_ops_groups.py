"""Тесты группировки audit-оп'ов."""

from __future__ import annotations

from agent_coder.audit_ops import (
    AUDIT_GROUPS,
    AUDIT_OPS,
    op_group,
    ops_in_group,
)


def test_all_ops_have_valid_group():
    valid = {g for g, _ in AUDIT_GROUPS}
    for op in AUDIT_OPS:
        assert op_group(op.id) in valid, f"{op.id} → {op_group(op.id)}"


def test_ops_in_group_covers_all():
    seen: set[str] = set()
    for gid, _ in AUDIT_GROUPS:
        for op in ops_in_group(gid):
            seen.add(op.id)
    expected = {op.id for op in AUDIT_OPS}
    assert seen == expected, f"разница: {expected - seen}"


def test_groups_not_empty():
    for gid, glabel in AUDIT_GROUPS:
        assert ops_in_group(gid), f"группа {gid} ({glabel}) пуста"


def test_ops_in_group_unknown_empty():
    assert ops_in_group("nonexistent") == []


def test_op_group_unknown_defaults():
    assert op_group("no-such-op") == "codetypes"


def test_keyboards_expose_group_api():
    """handlers.py рассчитывает на эти функции — они должны быть."""
    from agent_coder.telegram import keyboards

    assert hasattr(keyboards, "audit_menu_keyboard")
    assert hasattr(keyboards, "audit_group_keyboard")
    # верхний уровень — только группы (aud_grp:), не оп'ы (aud:)
    top = keyboards.audit_menu_keyboard().inline_keyboard
    cbs = [b.callback_data for row in top for b in row]
    assert any(cb.startswith("aud_grp:") for cb in cbs if cb)
    assert not any(cb.startswith("aud:") for cb in cbs if cb)


def test_server_keyboards_expose_group_api():
    """Симметрично для /server."""
    from agent_coder.telegram import keyboards

    assert hasattr(keyboards, "server_menu_keyboard")
    assert hasattr(keyboards, "server_group_keyboard")
    top = keyboards.server_menu_keyboard().inline_keyboard
    cbs = [b.callback_data for row in top for b in row]
    assert any(cb.startswith("srv_grp:") for cb in cbs if cb)
    assert not any(cb.startswith("srv:") for cb in cbs if cb)
