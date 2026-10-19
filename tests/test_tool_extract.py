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


def test_read_file_not_truncated_in_extract() -> None:
    """read_file — явный контракт offset/limit: extract НЕ вырезает середину.

    Регресс 5974e69: read_file был в _ALWAYS_APPLY, модель получала
    огрызок (head+defs+tail), перечитывала файл с другими окнами и
    упиралась в лимит итераций (сессия 93108d9a: 44 итерации, 0 повторов,
    но 9 перечитываний commands.py).
    """
    content = ["x"] * 200
    content[5] = "def foo():"
    content[100] = "MIDDLE_MARKER_XYZ"
    body = "\n".join(f"{i + 1:5d}│ {ln}" for i, ln in enumerate(content))
    result = f"test.py ({len(content)} строк):\n{body}"
    # read_file в _NO_EXTRACT: возвращается как есть, даже если > limit.
    assert result == tool_extract.extract("read_file", {}, result, 4096)
    long_result = result + "\n" + "y" * 10000
    out = tool_extract.extract("read_file", {}, long_result, 4096)
    assert out == long_result  # никакой обрезки
    assert "обрезано" not in out


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


def test_list_files_passthrough() -> None:
    """list_files — контракт «покажи файлы», extract НЕ режет.

    Регресс 75f25748: tests/ = 65 файлов, extract оставлял 10 + «55
    скрыто», модель пробовала list_files/ls/find по кругу → watchdog
    останавливал легитимный аудит. Tool сам ограничивает MAX_ENTRIES=500.
    """
    result = "\n".join(f"file{i}.py" for i in range(100))
    out = tool_extract.extract("list_files", {}, result, 1024)
    assert out == result  # как есть, без обрезки
    assert "скрыто" not in out


def test_env_override_per_tool(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TOOL_MAX_CHARS_SEARCH", "500")
    result = "\n".join(f"src/f{i}.py:{i}:match" for i in range(1000))
    out = tool_extract.extract("search", {}, result, 2048)
    assert len(out) <= 500


def test_env_override_global(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TOOL_MAX_CHARS", "300")
    result = "\n".join(f"src/f{i}.py:{i}:match" for i in range(1000))
    out = tool_extract.extract("search", {}, result, 2048)
    assert len(out) <= 300


def test_per_tool_env_wins_over_global(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TOOL_MAX_CHARS", "300")
    monkeypatch.setenv("TOOL_MAX_CHARS_SEARCH", "700")
    result = "\n".join(f"src/f{i}.py:{i}:match" for i in range(1000))
    out = tool_extract.extract("search", {}, result, 2048)
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


def test_run_command_listing_keeps_all_lines() -> None:
    """ls/find/tree — листинг: весь список, не head 8 + tail 8.

    Регресс 75f25748: `ls tests` = 65 файлов, extract оставлял 16,
    модель пробовала `ls tests/`, `ls -1 tests/`, `find` по кругу.
    """
    body = [f"test_{i:02d}.py" for i in range(65)]
    result = "\n".join(["$ ls tests", "exit code: 0", "--- stdout ---", *body])
    out = tool_extract.extract("run_command", {"command": "ls tests"}, result, 4096)
    for name in body:
        assert name in out, f"листинг потерял {name}"
    assert "скрыто" not in out


def test_run_command_pytest_still_head_tail() -> None:
    """pytest-лог — не листинг: head/tail, середина вырезана (как было)."""
    body = [f"line{i}" for i in range(200)]
    result = "\n".join(["$ pytest", "exit code: 0", "--- stdout ---", *body])
    out = tool_extract.extract("run_command", {"command": "pytest"}, result, 4096)
    assert "line0" in out and "line199" in out
    assert "line100" not in out


def test_run_command_listing_capped_at_100() -> None:
    """Листинг > LISTING_MAX — режется с маркером (страховка от гиганта)."""
    body = [f"file{i:04d}.py" for i in range(300)]
    result = "\n".join(["$ find .", "exit code: 0", "--- stdout ---", *body])
    out = tool_extract.extract("run_command", {"command": "find ."}, result, 4096)
    assert "file0000.py" in out
    assert "file0099.py" in out
    assert "file0299.py" not in out
    assert "скрыто" in out
