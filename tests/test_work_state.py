"""Тесты structured working memory (agent/work_state.py)."""

from __future__ import annotations

from agent_coder.agent.work_state import (
    FIELDS,
    MAX_ITEM_CHARS,
    MAX_ITEMS_PER_FIELD,
    WorkStateHandle,
    normalize,
    render_for_prompt,
)


def test_normalize_empty() -> None:
    out = normalize(None)
    assert out == {f: [] for f in FIELDS}


def test_normalize_unknown_fields_dropped() -> None:
    out = normalize({"findings": ["a"], "unknown": ["x"], "garbage": "y"})
    assert out["findings"] == ["a"]
    assert "unknown" not in out
    assert "garbage" not in out


def test_normalize_str_becomes_list() -> None:
    out = normalize({"todo": "один пункт"})
    assert out["todo"] == ["один пункт"]


def test_normalize_dedups_and_strips() -> None:
    out = normalize({"findings": ["  a  ", "a", "", "b", "a"]})
    assert out["findings"] == ["a", "b"]


def test_normalize_truncates_long_items() -> None:
    long = "x" * (MAX_ITEM_CHARS + 100)
    out = normalize({"findings": [long]})
    assert len(out["findings"][0]) == MAX_ITEM_CHARS + 1  # +1 на «…»
    assert out["findings"][0].endswith("…")


def test_normalize_caps_items_per_field() -> None:
    many = [f"item {i}" for i in range(MAX_ITEMS_PER_FIELD + 20)]
    out = normalize({"findings": many})
    assert len(out["findings"]) == MAX_ITEMS_PER_FIELD


def test_handle_get_returns_copy() -> None:
    state: dict = {}
    h = WorkStateHandle(state)
    out = h.get()
    out["findings"].append("mutated")
    assert h.get()["findings"] == []  # не повлияло на живой state


def test_handle_update_set() -> None:
    state: dict = {}
    h = WorkStateHandle(state)
    h.update({"findings": ["a", "b"]})
    assert h.get()["findings"] == ["a", "b"]
    assert state["findings"] == ["a", "b"]  # живой dict обновлён


def test_handle_update_add_dedup() -> None:
    h = WorkStateHandle({})
    h.update({"findings+": ["a", "b"]})
    h.update({"findings+": ["b", "c"]})  # b — дубль
    assert h.get()["findings"] == ["a", "b", "c"]


def test_handle_update_remove() -> None:
    h = WorkStateHandle({})
    h.update({"todo": ["x", "y", "z"]})
    h.update({"todo-": ["y"]})
    assert h.get()["todo"] == ["x", "z"]


def test_handle_update_mixed_ops() -> None:
    h = WorkStateHandle({})
    h.update({"findings+": ["a"], "todo": ["t1"], "done+": ["d1"]})
    assert h.get() == {
        "user_requests": [],
        "findings": ["a"],
        "done": ["d1"],
        "todo": ["t1"],
        "open_questions": [],
    }


def test_handle_update_unknown_field_ignored() -> None:
    h = WorkStateHandle({})
    h.update({"unknown": ["x"], "findings+": ["a"]})
    assert h.get()["findings"] == ["a"]
    assert "unknown" not in h.get()


def test_handle_update_non_dict_patch_no_crash() -> None:
    h = WorkStateHandle({})
    assert h.update("not a dict") == h.get()  # type: ignore[arg-type]


def test_handle_is_empty() -> None:
    h = WorkStateHandle({})
    assert h.is_empty()
    h.update({"findings+": ["a"]})
    assert not h.is_empty()


def test_render_empty_returns_empty_string() -> None:
    assert render_for_prompt({}) == ""
    assert render_for_prompt(None) == ""


def test_render_filled_contains_fields_and_items() -> None:
    state = {
        "findings": ["путь src/x.py"],
        "todo": ["починить y"],
    }
    out = render_for_prompt(state)
    assert "[WORK_STATE]" in out
    assert "src/x.py" in out
    assert "починить y" in out
    assert "Осталось" in out
    assert "Установленные факты" in out


def test_session_from_dict_without_work_state() -> None:
    """Старые сессии (до work_state) грузятся с пустым state, не падают."""
    from agent_coder.agent.prompts import Mode
    from agent_coder.agent.session import Session

    old = {
        "id": "old",
        "workspace": "/tmp",
        "model": "m",
        "mode": "act",
        "messages": [],
        "total_usage": {
            "input_tokens": 0,
            "output_tokens": 0,
            "cache_read_tokens": 0,
            "cache_write_tokens": 0,
            "cost": 0.0,
        },
    }
    s = Session.from_dict(old)
    assert s.work_state == {}
    assert s.mode == Mode.ACT


def test_session_roundtrip_with_work_state() -> None:
    from agent_coder.agent.prompts import Mode
    from agent_coder.agent.session import Session

    s = Session(id="s", workspace="/tmp", model="m", mode=Mode.ACT)
    s.work_state = {"findings": ["a"], "todo": ["b"]}
    d = s.to_dict()
    assert d["work_state"] == {"findings": ["a"], "todo": ["b"]}
    s2 = Session.from_dict(d)
    assert s2.work_state == {"findings": ["a"], "todo": ["b"]}
