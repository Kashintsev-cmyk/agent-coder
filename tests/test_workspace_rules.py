"""Тесты agent_coder.agent.workspace_rules (AGENTS.md)."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from agent_coder.agent import workspace_rules
from agent_coder.agent.workspace_rules import (
    BUILTIN_FOOTER,
    BUILTIN_HEADER,
    MAX_AGENTS_MD_BYTES,
    SECTION_FOOTER,
    SECTION_HEADER,
    TRUNCATION_MARKER,
    append_agents_md,
    append_builtin_standard,
    append_rules,
    clear_cache,
    load_agents_md,
)


@pytest.fixture(autouse=True)
def _clean_cache():
    clear_cache()
    yield
    clear_cache()


def _write_agents_md(tmp_path: Path, text: str) -> Path:
    p = tmp_path / "AGENTS.md"
    p.write_text(text, encoding="utf-8")
    return p


# (б) нет файла — как раньше, без падений
def test_no_file_returns_none(tmp_path: Path) -> None:
    assert load_agents_md(tmp_path) is None


def test_no_file_append_returns_prompt_unchanged(tmp_path: Path) -> None:
    assert append_agents_md("base prompt", tmp_path) == "base prompt"


# (а) файл есть — содержимое доступно и врезается секцией
def test_file_content_returned(tmp_path: Path) -> None:
    _write_agents_md(tmp_path, "hello rules")
    assert load_agents_md(tmp_path) == "hello rules"


def test_append_adds_section(tmp_path: Path) -> None:
    _write_agents_md(tmp_path, "hello rules")
    out = append_agents_md("base", tmp_path)
    assert out.startswith("base" + SECTION_HEADER)
    assert out.endswith(SECTION_FOOTER)
    assert "hello rules" in out


# (в) файл больше лимита — обрезан + маркер
def test_truncation(tmp_path: Path) -> None:
    _write_agents_md(tmp_path, "a" * (MAX_AGENTS_MD_BYTES + 1))
    out = load_agents_md(tmp_path)
    assert out is not None
    assert out.endswith(TRUNCATION_MARKER)
    body = out[: -len(TRUNCATION_MARKER)]
    assert len(body.encode("utf-8")) == MAX_AGENTS_MD_BYTES


def test_exact_limit_not_truncated(tmp_path: Path) -> None:
    _write_agents_md(tmp_path, "a" * MAX_AGENTS_MD_BYTES)
    out = load_agents_md(tmp_path)
    assert out is not None
    assert TRUNCATION_MARKER not in out
    assert len(out.encode("utf-8")) == MAX_AGENTS_MD_BYTES


# (г) mtime-кэш: повторный вызов не перечитывает; изменение файла — перечитывает
def test_cache_hit_does_not_reread(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _write_agents_md(tmp_path, "hello")
    calls = {"n": 0}
    real_read = workspace_rules._read_text

    def spy(path: Path) -> str | None:
        calls["n"] += 1
        return real_read(path)

    monkeypatch.setattr(workspace_rules, "_read_text", spy)
    assert load_agents_md(tmp_path) == "hello"
    assert load_agents_md(tmp_path) == "hello"
    assert load_agents_md(tmp_path) == "hello"
    assert calls["n"] == 1


def test_cache_invalidated_on_mtime_change(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    p = _write_agents_md(tmp_path, "v1")
    calls = {"n": 0}
    real_read = workspace_rules._read_text

    def spy(path: Path) -> str | None:
        calls["n"] += 1
        return real_read(path)

    monkeypatch.setattr(workspace_rules, "_read_text", spy)
    assert load_agents_md(tmp_path) == "v1"
    p.write_text("v2", encoding="utf-8")
    st = p.stat()
    os.utime(p, ns=(st.st_atime_ns, st.st_mtime_ns + 1_000_000_000))
    assert load_agents_md(tmp_path) == "v2"
    assert calls["n"] == 2


# edge cases: не падать
def test_invalid_utf8_does_not_crash(tmp_path: Path) -> None:
    p = tmp_path / "AGENTS.md"
    p.write_bytes(b"\xff\xfe broken \xff")
    out = load_agents_md(tmp_path)
    assert out is not None
    assert "\ufffd" in out


def test_oserror_does_not_crash(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _write_agents_md(tmp_path, "x")

    def boom(*_a: object, **_kw: object) -> None:
        raise PermissionError("nope")

    monkeypatch.setattr(Path, "open", boom)
    assert load_agents_md(tmp_path) is None
    assert append_agents_md("base", tmp_path) == "base"


def test_workspace_is_file_not_dir(tmp_path: Path) -> None:
    f = tmp_path / "ws_file"
    f.write_text("x")
    assert load_agents_md(f) is None


# --- builtin standard (шаг C) ---


def test_append_builtin_standard_adds_section() -> None:
    out = append_builtin_standard("base")
    assert out.startswith("base" + BUILTIN_HEADER)
    assert out.endswith(BUILTIN_FOOTER)
    assert "Стандарт оформления" in out


def test_append_builtin_standard_skipped_when_empty(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from agent_coder import rules

    monkeypatch.setattr(rules, "load_project_standard", lambda: "")
    assert append_builtin_standard("base") == "base"


def test_append_rules_prefers_agents_md(tmp_path: Path) -> None:
    _write_agents_md(tmp_path, "project-specific rule")
    out = append_rules("base", tmp_path)
    assert "project-specific rule" in out
    assert "Стандарт оформления" not in out


def test_append_rules_uses_builtin_when_no_agents_md(tmp_path: Path) -> None:
    out = append_rules("base", tmp_path)
    assert "Стандарт оформления" in out
    assert "project-specific rule" not in out
