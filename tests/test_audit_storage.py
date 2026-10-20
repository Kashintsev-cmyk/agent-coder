"""Тесты audit_storage (B11.2a).

Покрытие: load/save/append/mark_read/дедуп/gitignore/meta-atomic.
"""

from __future__ import annotations

import json
from types import SimpleNamespace

from agent_coder.agent import audit_storage as storage


def _session(workspace, findings=None, read_paths=None, todo=None):
    return SimpleNamespace(
        work_state={"findings": findings or [], "todo": todo or []},
        read_paths=read_paths or {},
        workspace=workspace,
    )


def test_load_empty_returns_defaults(tmp_path):
    state = storage.load(str(tmp_path))
    assert state["findings"] == []
    assert state["coverage"] == {}
    assert state["todo"] == []
    assert state["meta"] == {}


def test_save_creates_dir_files_and_gitignore(tmp_path):
    session = _session(str(tmp_path), findings=["bot.py:10 — None-check отсутствует"])
    storage.save(str(tmp_path), session, storage.load(str(tmp_path)))

    pd = storage.project_dir(str(tmp_path))
    assert pd.is_dir()
    assert (pd / "findings.md").is_file()
    assert (pd / "meta.json").is_file()

    gi = tmp_path / ".gitignore"
    assert gi.is_file()
    assert ".agent-coder/" in gi.read_text(encoding="utf-8")


def test_save_append_no_duplicates(tmp_path):
    session = _session(str(tmp_path), findings=["a.py:1 — x"])
    storage.save(str(tmp_path), session, storage.load(str(tmp_path)))
    storage.save(str(tmp_path), session, storage.load(str(tmp_path)))

    text = (storage.project_dir(str(tmp_path)) / "findings.md").read_text(
        encoding="utf-8"
    )
    assert text.count("a.py:1") == 1


def test_save_appends_new_findings(tmp_path):
    s1 = _session(str(tmp_path), findings=["a.py:1 — x"])
    storage.save(str(tmp_path), s1, storage.load(str(tmp_path)))

    s2 = _session(str(tmp_path), findings=["a.py:1 — x", "b.py:2 — y"])
    storage.save(str(tmp_path), s2, storage.load(str(tmp_path)))

    state = storage.load(str(tmp_path))
    assert len(state["findings"]) == 2


def test_mark_read_appends_coverage(tmp_path):
    storage.mark_read(str(tmp_path), {"a.py": "digest-a"})
    storage.mark_read(str(tmp_path), {"b.py": ""})

    state = storage.load(str(tmp_path))
    assert state["coverage"] == {"a.py": "digest-a", "b.py": ""}


def test_meta_atomic_valid_json(tmp_path):
    session = _session(str(tmp_path), findings=["x.py:1 — z"])
    storage.save(str(tmp_path), session, storage.load(str(tmp_path)))

    meta = json.loads(
        (storage.project_dir(str(tmp_path)) / "meta.json").read_text(encoding="utf-8")
    )
    assert meta["findings_count"] == 1
    assert meta["slug"]
    assert meta["workspace"] == str(tmp_path.resolve())


def test_gitignore_idempotent(tmp_path):
    session = _session(str(tmp_path))
    storage.save(str(tmp_path), session, storage.load(str(tmp_path)))
    storage.save(str(tmp_path), session, storage.load(str(tmp_path)))

    text = (tmp_path / ".gitignore").read_text(encoding="utf-8")
    assert text.count(".agent-coder/") == 1


def test_prepare_fresh_removes_state(tmp_path):
    session = _session(str(tmp_path), findings=["a.py:1 — x"])
    storage.save(str(tmp_path), session, storage.load(str(tmp_path)))
    assert (storage.project_dir(str(tmp_path)) / "findings.md").exists()

    state = storage.prepare(str(tmp_path), fresh=True)
    assert state["findings"] == []
    assert not storage.project_dir(str(tmp_path)).exists()


def test_prepare_resume_loads_state(tmp_path):
    session = _session(str(tmp_path), findings=["a.py:1 — x"])
    storage.save(str(tmp_path), session, storage.load(str(tmp_path)))

    state = storage.prepare(str(tmp_path), fresh=False)
    assert state["findings"] == ["a.py:1 — x"]
