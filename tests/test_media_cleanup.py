"""Тесты retention медиа-сессий."""

from __future__ import annotations

import os
import time

from agent_coder.agent.media_cleanup import cleanup_old_media


def _mk_media(base, session_id: str, filename: str, age_days: float) -> str:
    d = base / "media" / session_id
    d.mkdir(parents=True, exist_ok=True)
    f = d / filename
    f.write_bytes(b"x")
    mtime = time.time() - age_days * 86400
    os.utime(f, (mtime, mtime))
    return str(f)


def test_no_media_dir(tmp_path):
    files, dirs = cleanup_old_media(sessions_dir=tmp_path)
    assert (files, dirs) == (0, 0)


def test_empty_media_dir(tmp_path):
    (tmp_path / "media").mkdir()
    files, dirs = cleanup_old_media(sessions_dir=tmp_path)
    assert (files, dirs) == (0, 0)


def test_removes_old_keeps_fresh(tmp_path):
    old_file = _mk_media(tmp_path, "s1", "old.jpg", age_days=40)
    fresh_file = _mk_media(tmp_path, "s1", "fresh.jpg", age_days=5)
    files, _ = cleanup_old_media(sessions_dir=tmp_path, retention_days=30)
    assert files == 1
    assert not os.path.exists(old_file)
    assert os.path.exists(fresh_file)


def test_removes_empty_session_dir(tmp_path):
    _mk_media(tmp_path, "s1", "old.jpg", age_days=40)
    cleanup_old_media(sessions_dir=tmp_path, retention_days=30)
    assert not (tmp_path / "media" / "s1").exists()


def test_keeps_dir_with_fresh_file(tmp_path):
    _mk_media(tmp_path, "s1", "old.jpg", age_days=40)
    _mk_media(tmp_path, "s1", "fresh.jpg", age_days=5)
    cleanup_old_media(sessions_dir=tmp_path, retention_days=30)
    assert (tmp_path / "media" / "s1").exists()


def test_multiple_sessions(tmp_path):
    _mk_media(tmp_path, "s1", "a.jpg", age_days=50)
    _mk_media(tmp_path, "s2", "b.jpg", age_days=50)
    _mk_media(tmp_path, "s3", "c.jpg", age_days=1)
    files, dirs = cleanup_old_media(sessions_dir=tmp_path, retention_days=30)
    assert files == 2
    assert dirs == 2
    assert (tmp_path / "media" / "s3").exists()


def test_retention_zero_noop(tmp_path):
    _mk_media(tmp_path, "s1", "x.jpg", age_days=100)
    files, _ = cleanup_old_media(sessions_dir=tmp_path, retention_days=0)
    assert files == 0


def test_now_parameter(tmp_path):
    _mk_media(tmp_path, "s1", "x.jpg", age_days=10)
    # «Сейчас» сильно в будущем — файл станет «старым»
    future = time.time() + 100 * 86400
    files, _ = cleanup_old_media(
        sessions_dir=tmp_path,
        retention_days=30,
        now=future,
    )
    assert files == 1
