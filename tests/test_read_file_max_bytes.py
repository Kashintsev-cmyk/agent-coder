"""read_file: MAX_BYTES ограничивает «чтение целиком» (Риск 2)."""

from __future__ import annotations

import pytest

from agent_coder.agent.prompts import Mode
from agent_coder.tools.read_file import MAX_BYTES, _execute
from agent_coder.tools.registry import ToolContext


def _ctx(tmp_path) -> ToolContext:
    return ToolContext(workspace=str(tmp_path), mode=Mode.ACT)


def test_max_bytes_is_bounded() -> None:
    """MAX_BYTES не должен возвращать ~50k токенов одним сообщением."""
    assert MAX_BYTES <= 100_000, (
        f"MAX_BYTES={MAX_BYTES} — одно read_file вернёт "
        f"~{MAX_BYTES // 4} токенов, это > soft-лимита"
    )


@pytest.mark.asyncio
async def test_large_file_without_slice_rejected(tmp_path) -> None:
    """Файл > MAX_BYTES без offset/limit → ошибка с подсказкой."""
    big = tmp_path / "big.py"
    big.write_text("x" * (MAX_BYTES + 1000), encoding="utf-8")
    out = await _execute({"path": "big.py"}, _ctx(tmp_path))
    assert "слишком большой" in out
    assert "offset" in out and "limit" in out


@pytest.mark.asyncio
async def test_large_file_with_slice_allowed(tmp_path) -> None:
    """Тот же файл со слайсом — читается (контракт offset/limit)."""
    big = tmp_path / "big.py"
    big.write_text("line\n" * 50000, encoding="utf-8")
    out = await _execute({"path": "big.py", "offset": 0, "limit": 10}, _ctx(tmp_path))
    assert "слишком большой" not in out
    assert "строки 1-10" in out
