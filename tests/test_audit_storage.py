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


def test_count_py_files_all_workspace(tmp_path):
    (tmp_path / "a.py").write_text("x = 1")
    (tmp_path / "b.py").write_text("y = 2")
    (tmp_path / "c.txt").write_text("z")
    venv = tmp_path / ".venv" / "lib"
    venv.mkdir(parents=True)
    (venv / "skip.py").write_text("no")
    assert storage._count_py_files(str(tmp_path)) == 2


def test_count_py_files_scope(tmp_path):
    (tmp_path / "bot").mkdir()
    (tmp_path / "bot" / "a.py").write_text("x")
    (tmp_path / "bot" / "b.py").write_text("y")
    (tmp_path / "other").mkdir()
    (tmp_path / "other" / "c.py").write_text("z")
    assert storage._count_py_files(str(tmp_path), ["bot"]) == 2
    assert storage._count_py_files(str(tmp_path), ["other"]) == 1
    assert storage._count_py_files(str(tmp_path)) == 3


def test_save_meta_total_files(tmp_path):
    (tmp_path / "a.py").write_text("x")
    (tmp_path / "b.py").write_text("y")
    session = _session(str(tmp_path), findings=["a.py:1 — x"])
    session.read_paths = {"a.py": "digest"}
    loaded = storage.load(str(tmp_path))
    loaded["total_files"] = storage._count_py_files(str(tmp_path))
    storage.save(str(tmp_path), session, loaded)

    meta = json.loads(
        (storage.project_dir(str(tmp_path)) / "meta.json").read_text(encoding="utf-8")
    )
    assert meta["total_files"] == 2
    assert meta["coverage_count"] == 1
    assert meta["coverage_remaining"] == 1
