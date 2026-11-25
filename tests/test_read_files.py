"""read_files: пакетное чтение через read_file._execute (scope, кап, digest)."""

from __future__ import annotations

import pytest

from agent_coder.agent.prompts import Mode
from agent_coder.tools import read_files as rf
from agent_coder.tools.registry import ToolContext


def _ctx(tmp_path, read_paths=None, scopes=None) -> ToolContext:
    return ToolContext(
        workspace=str(tmp_path),
        mode=Mode.AUDIT,
        read_paths=read_paths,
        audit_scopes=scopes,
    )


@pytest.mark.asyncio
async def test_batch_reads_all_and_writes_digests(tmp_path) -> None:
    (tmp_path / "a.py").write_text("def f():\n    pass\n", encoding="utf-8")
    (tmp_path / "b.py").write_text("class B:\n    pass\n", encoding="utf-8")
    rp: dict[str, str] = {}
    out = await rf._execute({"paths": ["a.py", "b.py"]}, _ctx(tmp_path, rp))
    assert "a.py (строки" in out and "b.py (строки" in out
    assert set(rp) == {"a.py", "b.py"}


@pytest.mark.asyncio
async def test_too_many_paths_rejected(tmp_path) -> None:
    paths = [f"f{i}.py" for i in range(rf.MAX_BATCH_FILES + 1)]
    out = await rf._execute({"paths": paths}, _ctx(tmp_path))
    assert "Слишком много путей" in out


@pytest.mark.asyncio
async def test_paths_must_be_list(tmp_path) -> None:
    out = await rf._execute({"paths": "a.py"}, _ctx(tmp_path))
    assert out.startswith("Ошибка")


@pytest.mark.asyncio
async def test_out_of_scope_path_rejected_others_read(tmp_path) -> None:
    (tmp_path / "a.py").write_text("x = 1\n", encoding="utf-8")
    (tmp_path / "b.py").write_text("y = 2\n", encoding="utf-8")
    rp: dict[str, str] = {}
    out = await rf._execute(
        {"paths": ["a.py", "b.py"]},
        _ctx(tmp_path, rp, scopes=["a.py"]),
    )
    assert "вне области аудита" in out
    assert "a.py (строки" in out
    assert "b.py" not in rp


@pytest.mark.asyncio
async def test_total_cap_skips_rest(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(rf, "MAX_TOTAL_BYTES", 200)
    (tmp_path / "a.py").write_text("x = 1\n" * 10, encoding="utf-8")
    (tmp_path / "b.py").write_text("y = 2\n", encoding="utf-8")
    out = await rf._execute({"paths": ["a.py", "b.py"]}, _ctx(tmp_path))
    assert "лимит" in out and "b.py" in out


@pytest.mark.asyncio
async def test_already_read_full_read_not_overwritten(tmp_path) -> None:
    (tmp_path / "a.py").write_text("x = 1\n", encoding="utf-8")
    rp = {"a.py": "old-digest"}
    out = await rf._execute({"paths": ["a.py"]}, _ctx(tmp_path, rp))
    assert "уже прочитан" in out
    assert rp["a.py"] == "old-digest"
