"""B11.4: render_read_paths — recent-N вместо alphabet."""

from __future__ import annotations

from agent_coder.agent.work_state import (
    MAX_READ_PATHS_IN_PROMPT,
    render_read_paths,
)


def test_keeps_recent_not_alphabet() -> None:
    n = MAX_READ_PATHS_IN_PROMPT
    paths = {f"f_{i:03d}.py": f"f_{i:03d}.py (1): x" for i in range(n + 10)}
    out = render_read_paths(paths)
    # последние N попали
    assert f"f_{n + 9:03d}.py" in out
    # самые старые — за пределами N, скрыты
    assert "f_000.py" not in out
    assert "…ещё 10 файлов скрыто" in out


def test_hidden_hint_is_search_history() -> None:
    n = MAX_READ_PATHS_IN_PROMPT
    paths = {f"p_{i:03d}.py": f"p_{i:03d}.py" for i in range(n + 3)}
    out = render_read_paths(paths)
    assert "search_history" in out


def test_under_limit_shows_all_no_hidden() -> None:
    paths = {f"q_{i}.py": f"q_{i}.py" for i in range(5)}
    out = render_read_paths(paths)
    for i in range(5):
        assert f"q_{i}.py" in out
    assert "скрыто" not in out


def test_none_and_empty() -> None:
    assert render_read_paths(None) == ""
    assert render_read_paths({}) == ""
