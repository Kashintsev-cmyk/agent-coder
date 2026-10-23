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


def test_count_skips_malformed_lines(tmp_path):
    archive = tmp_path / "s.jsonl"
    archive.write_text(
        '{"role": "assistant"}\nnot-json\n\n{"role": "tool"}\n',
        encoding="utf-8",
    )
    assert _count_iterations_and_tools(archive) == (1, 1)


def test_find_case_archive_matches_content(tmp_path):
    archive_dir = tmp_path / "archive"
    archive_dir.mkdir()

    other = archive_dir / "a.jsonl"
    _write_jsonl(other, [{"role": "user", "content": "other-task model-x"}])

    target = archive_dir / "b.jsonl"
    _write_jsonl(
        target,
        [{"role": "user", "content": "task-42 model-y go"}],
    )
    # target должен быть новее, но фильтр идёт и по содержимому
    os.utime(other, (time.time() - 10, time.time() - 10))
    os.utime(target, (time.time(), time.time()))

    assert _find_case_archive(tmp_path, "task-42", "model-y") == target


def test_find_case_archive_ignores_wrong_content(tmp_path):
    archive_dir = tmp_path / "archive"
    archive_dir.mkdir()
    _write_jsonl(
        archive_dir / "a.jsonl",
        [{"role": "user", "content": "some-other-task model-z"}],
    )
    # ни один архив не содержит искомого task_id/model — None,
    # а не «первый попавшийся», иначе failed-кейс возьмёт чужой архив
    assert _find_case_archive(tmp_path, "task-42", "model-y") is None


def test_find_case_archive_missing_dir(tmp_path):
    assert _find_case_archive(tmp_path, "task-42", "model-y") is None
