"""Unit tests for benchmark/runner.py worktree prep (sentinel-фикс B9.2g.bench)."""

from pathlib import Path


def _make_workspace(tmp_path: Path, *, sentinel: bool) -> Path:
    (tmp_path / "pyproject.toml").write_text(
        '[project]\nname = "x"\n\n[tool.pytest.ini_options]\ntestpaths = ["tests"]\n',
        encoding="utf-8",
    )
    if sentinel:
        (tmp_path / "test_benchmark_target.py").write_text(
            "def test_x():\n    assert True\n", encoding="utf-8"
        )
    return tmp_path


def test_prepare_fixture_extends_testpaths_with_sentinel(tmp_path, monkeypatch):
    from benchmark import runner

    ws = _make_workspace(tmp_path, sentinel=True)
    monkeypatch.setattr(
        runner.subprocess, "run", lambda *a, **kw: type("R", (), {"returncode": 0})()
    )
    runner._prepare_fixture(ws, "create_parser_fixture.py", "create-03")
    text = (ws / "pyproject.toml").read_text(encoding="utf-8")
    assert "test_benchmark_target.py" in text
    assert "testpaths" in text


def test_prepare_fixture_no_sentinel_keeps_testpaths(tmp_path, monkeypatch):
    from benchmark import runner

    ws = _make_workspace(tmp_path, sentinel=False)
    monkeypatch.setattr(
        runner.subprocess, "run", lambda *a, **kw: type("R", (), {"returncode": 0})()
    )
    runner._prepare_fixture(ws, "create_parser_fixture.py", "create-03")
    text = (ws / "pyproject.toml").read_text(encoding="utf-8")
    assert "test_benchmark_target.py" not in text
    assert 'testpaths = ["tests"]' in text


def test_restore_fixture_files_runs_git_checkout(tmp_path, monkeypatch):
    from benchmark import runner

    calls: list[list[str]] = []
    monkeypatch.setattr(
        runner.subprocess,
        "run",
        lambda cmd, **kw: calls.append(list(cmd)) or type("R", (), {"returncode": 0})(),
    )
    runner._restore_fixture_files(tmp_path, "abc123")
    assert any(
        "checkout" in c and "test_benchmark_target.py" in c and "pyproject.toml" in c
        for c in calls
    ), calls
