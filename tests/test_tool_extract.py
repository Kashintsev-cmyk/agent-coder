"""Тесты детерминированного per-tool extraction (agent/tool_extract.py).

Реальные tool-сообщения из сессий — отдельным коммитом (fixtures).
Здесь — синтетика по каждому formatter'у + env-override + fallback.
"""

from __future__ import annotations

import pytest

from agent_coder.agent import tool_extract


def test_short_result_unchanged_for_known_tool() -> None:
    assert tool_extract.extract("run_command", {}, "short", 4096) == "short"


def test_unknown_tool_fallback() -> None:
    result = "x" * 10000
    out = tool_extract.extract("unknown_tool", {}, result, 1000)
    assert len(out) <= 1000
    assert "обрезано" in out
    assert out.startswith("x")
    assert out.endswith("x")


def test_run_command_keeps_exit_code_and_errors() -> None:
    lines = [f"line {i}" for i in range(200)]
    lines[100] = "error: something failed"
    lines[150] = "Warning: deprecated"
    result = "\n".join(
        [
            "$ pytest",
            "exit code: 1",
            "--- stdout ---",
            *lines,
            "Traceback (most recent call last):",
            "ValueError: boom",
        ]
    )
    out = tool_extract.extract("run_command", {}, result, 4096)
    assert "exit code: 1" in out
    assert "error: something failed" in out
    assert "Warning: deprecated" in out
    assert "Traceback" in out
    assert "ValueError: boom" in out
    assert len(out) <= 4096


def test_run_command_heredoc_keeps_exit_code() -> None:
    """Heredoc: '$ cmd' занимает десятки строк, exit code — далеко внизу.

    Регрессия: раньше keep={0,1} сохранял шапку скрипта и терял
    'exit code: N', который оказывался на строке 50+.
    """
    lines = ["$ cd /x && python3 - <<'PYEOF'"]
    lines += [f"line {i}" for i in range(50)]
    lines += [
        "PYEOF",
        "exit code: 1",
        "--- stderr ---",
        "Traceback (most recent call last):",
        "ValueError: boom",
    ]
    result = "\n".join(lines)
    out = tool_extract.extract("run_command", {}, result, 4096)
    assert "exit code: 1" in out
    assert "Traceback" in out
    assert "ValueError: boom" in out
    assert "[команда сокращена]" not in out or len(out) <= 4096


def test_run_command_long_header_truncated() -> None:
    """Многострочный '$ cmd' (heredoc) обрезается до 200 симв."""
    long_cmd = "$ python3 - <<'PYEOF' " + "x" * 5000
    result = long_cmd + "\nexit code: 0\n--- stdout ---\nok"
    out = tool_extract.extract("run_command", {}, result, 4096)
    assert "exit code: 0" in out
    assert "[команда сокращена]" in out
    assert len(out) <= 4096


def test_read_file_keeps_defs_and_imports() -> None:
    content = ["x"] * 200
    content[5] = "def foo():"
    content[10] = "import os"
    content[15] = "class Bar:"
    body = "\n".join(f"{i + 1:5d}│ {ln}" for i, ln in enumerate(content))
    result = f"test.py ({len(content)} строк):\n{body}"
    out = tool_extract.extract("read_file", {}, result, 4096)
    assert "test.py" in out
    assert "def foo" in out
    assert "import os" in out
    assert "class Bar" in out
    assert len(out) <= 4096


def test_git_diff_strips_body_and_counts() -> None:
    result = (
        "diff --git a/a.py b/a.py\n"
        "--- a/a.py\n"
        "+++ b/a.py\n"
        "@@ -1 +1 @@\n"
        "-old\n"
        "+new\n"
        "+another\n"
        "diff --git a/b.py b/b.py\n"
        "+++ b/b.py\n"
        "+x"
    )
    out = tool_extract.extract("git_diff", {}, result, 2048)
    assert "a.py" in out
    assert "b.py" in out
    assert "+3 -1" in out
    assert "@@" not in out
    assert "-old" not in out


def test_git_diff_small_still_stripped() -> None:
    result = "--- a/x.py\n+++ b/x.py\n@@ -1 +1 @@\n-old\n+new\n"
    out = tool_extract.extract("git_diff", {}, result, 2048)
    assert "@@" not in out
    assert "+1 -1" in out
    assert "x.py" in out


def test_git_status_short_passthrough() -> None:
    result = "## master\n M file.py"
    assert tool_extract.extract("git_status", {}, result, 2048) == result


def test_git_log_truncated_when_large() -> None:
    result = "\n".join(f"abc{i:04d} 2026-01-01 Author: commit {i}" for i in range(500))
    out = tool_extract.extract("git_log", {}, result, 2048)
    assert len(out) <= 2048
    assert "обрезано" in out


def test_search_top_hits() -> None:
    result = "\n".join(f"src/f{i}.py:{i}:match content" for i in range(50))
    out = tool_extract.extract("search", {}, result, 2048)
    assert "src/f0.py" in out
    assert "совпадений скрыто" in out
    assert len(out) <= 2048


def test_search_history_top_fragments() -> None:
    result = "\n\n".join(f"[user] fragment {i}" for i in range(30))
    out = tool_extract.extract("search_history", {}, result, 2048)
    assert "fragment 0" in out
    assert "фрагментов скрыто" in out
    assert len(out) <= 2048


def test_list_files_top_and_counter() -> None:
    result = "\n".join(f"file{i}.py" for i in range(100))
    out = tool_extract.extract("list_files", {}, result, 1024)
    assert "file0.py" in out
    assert "файлов скрыто" in out
    assert len(out) <= 1024


def test_env_override_per_tool(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TOOL_MAX_CHARS_LIST_FILES", "500")
    result = "\n".join(f"file{i}.py" for i in range(1000))
    out = tool_extract.extract("list_files", {}, result, 1024)
    assert len(out) <= 500


def test_env_override_global(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TOOL_MAX_CHARS", "300")
    result = "\n".join(f"file{i}.py" for i in range(1000))
    out = tool_extract.extract("list_files", {}, result, 1024)
    assert len(out) <= 300


def test_per_tool_env_wins_over_global(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TOOL_MAX_CHARS", "300")
    monkeypatch.setenv("TOOL_MAX_CHARS_LIST_FILES", "700")
    result = "\n".join(f"file{i}.py" for i in range(1000))
    out = tool_extract.extract("list_files", {}, result, 1024)
    assert len(out) <= 700


def test_git_diff_truncated_marks_counts_incomplete() -> None:
    """git_diff уже усечён tools/git.py — счётчик помечается 'неполные'."""
    result = (
        "--- a/x.py\n+++ b/x.py\n@@ -1 +1 @@\n-old\n+new\n"
        "... (обрезано, всего 999999 символов)"
    )
    out = tool_extract.extract("git_diff", {}, result, 2048)
    assert "x.py" in out
    assert "неполные" in out
    assert "search_history" in out


def test_git_diff_not_truncated_no_marker() -> None:
    """Целый diff — без пометки 'неполные'."""
    result = "--- a/x.py\n+++ b/x.py\n@@ -1 +1 @@\n-old\n+new"
    out = tool_extract.extract("git_diff", {}, result, 2048)
    assert "x.py" in out
    assert "неполные" not in out
