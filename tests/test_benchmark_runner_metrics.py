"""Tests for benchmark runner iteration/tool_calls accounting (ACT modes)."""

from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

# benchmark/ не установлен как пакет (pip install -e . ставит только
# agent_coder), поэтому из tests/ он не резолвится без корня проекта
# на sys.path. conftest.py этим не занимается (там env vars).
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from benchmark.runner import (
    _count_iterations_and_tools,
    _find_case_archive,
)


def _write_jsonl(path, messages):
    path.write_text(
        "\n".join(json.dumps(m, ensure_ascii=False) for m in messages) + "\n",
        encoding="utf-8",
    )


def test_count_iterations_and_tools(tmp_path):
    archive = tmp_path / "s.jsonl"
    _write_jsonl(
        archive,
        [
            {"role": "user", "content": "go"},
            {"role": "assistant", "content": "a1"},
            {"role": "tool", "tool_call_id": "x", "content": "ok"},
            {"role": "assistant", "content": "a2"},
            {"role": "tool", "tool_call_id": "y", "content": "ok"},
            {"role": "tool", "tool_call_id": "z", "content": "ok"},
        ],
    )
    iterations, tool_calls = _count_iterations_and_tools(archive)
    assert iterations == 2
    assert tool_calls == 3


def test_count_missing_archive_returns_zero(tmp_path):
    assert _count_iterations_and_tools(tmp_path / "nope.jsonl") == (0, 0)


def test_count_from_json_uses_request_count(tmp_path):
    session = tmp_path / "s.json"
    session.write_text(
        json.dumps(
            {
                "request_count": 5,
                "messages": [
                    {"role": "user"},
                    {"role": "assistant"},
                    {"role": "tool"},
                    {"role": "assistant"},
                    {"role": "tool"},
                ],
            }
        ),
        encoding="utf-8",
    )
    # request_count wins even though only 2 assistant messages are present
    assert _count_iterations_and_tools(session) == (5, 2)


def test_count_from_json_falls_back_to_assistant_count(tmp_path):
    session = tmp_path / "s.json"
    session.write_text(
        json.dumps(
            {
                "messages": [
                    {"role": "user"},
                    {"role": "assistant"},
                    {"role": "tool"},
                    {"role": "assistant"},
                    {"role": "tool"},
                    {"role": "assistant"},
                ]
            }
        ),
        encoding="utf-8",
    )
    assert _count_iterations_and_tools(session) == (3, 2)


def test_count_skips_malformed_lines(tmp_path):
    archive = tmp_path / "s.jsonl"
    archive.write_text(
        '{"role": "assistant"}\nnot-json\n\n{"role": "tool"}\n',
        encoding="utf-8",
    )
    assert _count_iterations_and_tools(archive) == (1, 1)


def test_find_case_archive_prefers_json_over_jsonl(tmp_path):
    older_json = tmp_path / "old.json"
    older_json.write_text('{"messages": []}', encoding="utf-8")
    newer_jsonl_dir = tmp_path / "archive"
    newer_jsonl_dir.mkdir()
    newer_jsonl = newer_jsonl_dir / "new.jsonl"
    _write_jsonl(newer_jsonl, [{"role": "tool", "content": "x"}])

    os.utime(older_json, (time.time() - 10, time.time() - 10))
    os.utime(newer_jsonl, (time.time(), time.time()))

    # .json wins even if older — it carries request_count
    assert _find_case_archive(tmp_path) == older_json


def test_find_case_archive_newest_json(tmp_path):
    older = tmp_path / "a.json"
    older.write_text('{"messages": []}', encoding="utf-8")
    newer = tmp_path / "b.json"
    newer.write_text('{"messages": []}', encoding="utf-8")

    os.utime(older, (time.time() - 10, time.time() - 10))
    os.utime(newer, (time.time(), time.time()))

    assert _find_case_archive(tmp_path) == newer


def test_find_case_archive_excludes_index_json(tmp_path):
    (tmp_path / "index.json").write_text('{"sessions": []}', encoding="utf-8")
    session = tmp_path / "abc.json"
    session.write_text('{"messages": []}', encoding="utf-8")

    # index.json must never be picked as the case transcript
    assert _find_case_archive(tmp_path) == session


def test_find_case_archive_empty_dir_returns_none(tmp_path):
    (tmp_path / "archive").mkdir()
    assert _find_case_archive(tmp_path) is None


def test_find_case_archive_missing_dir(tmp_path):
    assert _find_case_archive(tmp_path) is None
