"""read_file: лимиты, пагинация и отсутствие «двойной обрезки».

Индустриальный стандарт (Claude Code, Cline): один вызов отдаёт не больше
N строк, длинные строки режутся, в ответе есть готовый «следующий offset»,
а объём ответа ограничен в байтах — и для «чтения целиком», и для слайса.
"""

from __future__ import annotations

import importlib

import pytest

from agent_coder.agent.prompts import Mode
from agent_coder.tools import read_file as rf
from agent_coder.tools.read_file import (
    MAX_BYTES,
    MAX_LINE_CHARS,
    MAX_LINES,
    _execute,
)
from agent_coder.tools.registry import ToolContext


def _ctx(tmp_path) -> ToolContext:
    return ToolContext(workspace=str(tmp_path), mode=Mode.ACT)


def test_max_bytes_is_bounded() -> None:
    """MAX_BYTES не должен возвращать ~50k токенов одним сообщением."""
    assert MAX_BYTES <= 100_000, (
        f"MAX_BYTES={MAX_BYTES} — одно read_file вернёт "
        f"~{MAX_BYTES // 4} токенов, это > soft-лимита"
    )


def test_max_lines_is_industry_standard() -> None:
    """2000 строк за вызов — как в Claude Code / Cline."""
    assert MAX_LINES == 2_000


@pytest.mark.asyncio
async def test_large_file_without_slice_rejected(tmp_path) -> None:
    """Файл > MAX_BYTES без offset/limit → ошибка с готовой командой."""
    big = tmp_path / "big.py"
    big.write_text("x" * (MAX_BYTES + 1000), encoding="utf-8")
    out = await _execute({"path": "big.py"}, _ctx(tmp_path))
    assert "слишком большой" in out
    assert "offset" in out and "limit" in out
    # подсказка должна быть исполнимой, а не «укажи offset и limit»
    assert f"limit={MAX_LINES}" in out


@pytest.mark.asyncio
async def test_large_file_with_slice_allowed(tmp_path) -> None:
    """Тот же файл со слайсом — читается (контракт offset/limit)."""
    big = tmp_path / "big.py"
    big.write_text("line\n" * 50000, encoding="utf-8")
    out = await _execute({"path": "big.py", "offset": 0, "limit": 10}, _ctx(tmp_path))
    assert "слишком большой" not in out
    assert "строки 1-10" in out


@pytest.mark.asyncio
async def test_slice_footer_hints_next_offset(tmp_path) -> None:
    """Футер говорит, сколько всего строк и какой offset брать дальше."""
    f = tmp_path / "many.py"
    f.write_text("x = 1\n" * 500, encoding="utf-8")
    out = await _execute({"path": "many.py", "offset": 0, "limit": 100}, _ctx(tmp_path))
    assert "строки 1-100 из 500" in out
    assert "далее offset=100" in out


@pytest.mark.asyncio
async def test_full_read_has_no_next_offset(tmp_path) -> None:
    """Файл прочитан целиком — подсказки про следующий offset нет."""
    f = tmp_path / "small.py"
    f.write_text("a = 1\nb = 2\nc = 3\n", encoding="utf-8")
    out = await _execute({"path": "small.py"}, _ctx(tmp_path))
    assert "строки 1-3 из 3" in out
    assert "далее offset=" not in out


@pytest.mark.asyncio
async def test_slice_respects_byte_cap(tmp_path) -> None:
    """Слайс не может вернуть больше MAX_BYTES (раньше лимит не проверялся)."""
    f = tmp_path / "wide.py"
    chunk = "y" * (MAX_LINE_CHARS - 10)
    f.write_text((chunk + "\n") * MAX_LINES, encoding="utf-8")
    out = await _execute(
        {"path": "wide.py", "offset": 0, "limit": MAX_LINES}, _ctx(tmp_path)
    )
    assert len(out.encode("utf-8")) <= MAX_BYTES + 512, (
        f"слайс вернул {len(out.encode('utf-8'))} байт при лимите {MAX_BYTES}"
    )
    assert f"лимит {MAX_BYTES} байт" in out
    assert "далее offset=" in out


@pytest.mark.asyncio
async def test_long_line_is_clipped(tmp_path) -> None:
    """Строка длиннее MAX_LINE_CHARS режется с пометкой (как в Claude Code)."""
    f = tmp_path / "min.js"
    f.write_text("z" * (MAX_LINE_CHARS + 500) + "\n", encoding="utf-8")
    out = await _execute({"path": "min.js"}, _ctx(tmp_path))
    assert "…[+500 симв.]" in out
    assert "z" * (MAX_LINE_CHARS + 100) not in out


@pytest.mark.asyncio
async def test_limit_over_max_rejected(tmp_path) -> None:
    """limit > MAX_LINES → отказ с указанием потолка."""
    f = tmp_path / "a.py"
    f.write_text("x\n" * 10, encoding="utf-8")
    out = await _execute(
        {"path": "a.py", "offset": 0, "limit": MAX_LINES + 1}, _ctx(tmp_path)
    )
    assert "limit слишком большой" in out
    assert str(MAX_LINES) in out


@pytest.mark.asyncio
async def test_empty_file(tmp_path) -> None:
    """Пустой файл не падает и не отдаёт «строки 1-0»."""
    f = tmp_path / "empty.py"
    f.write_text("", encoding="utf-8")
    out = await _execute({"path": "empty.py"}, _ctx(tmp_path))
    assert "empty.py" in out
    assert "Ошибка" not in out


@pytest.mark.asyncio
async def test_offset_past_eof(tmp_path) -> None:
    """offset за концом файла → «пусто», без трейсбека."""
    f = tmp_path / "a.py"
    f.write_text("x\ny\n", encoding="utf-8")
    out = await _execute({"path": "a.py", "offset": 50, "limit": 5}, _ctx(tmp_path))
    assert "пусто" in out


def test_env_overrides(monkeypatch) -> None:
    """Лимиты настраиваются через env (cline#13263), но с клампом."""
    monkeypatch.setenv("READ_MAX_LINES", "500")
    monkeypatch.setenv("READ_MAX_BYTES", "4096")
    monkeypatch.setenv("READ_MAX_LINE_CHARS", "120")
    try:
        mod = importlib.reload(rf)
        assert mod.MAX_LINES == 500
        assert mod.MAX_BYTES == 4096
        assert mod.MAX_LINE_CHARS == 120

        # мусор и выход за потолок — не ломают импорт
        monkeypatch.setenv("READ_MAX_LINES", "not-a-number")
        monkeypatch.setenv("READ_MAX_BYTES", "999999999")
        mod = importlib.reload(rf)
        assert mod.MAX_LINES == 2_000
        assert mod.MAX_BYTES == 1_000_000
    finally:
        monkeypatch.delenv("READ_MAX_LINES", raising=False)
        monkeypatch.delenv("READ_MAX_BYTES", raising=False)
        monkeypatch.delenv("READ_MAX_LINE_CHARS", raising=False)
        importlib.reload(rf)
