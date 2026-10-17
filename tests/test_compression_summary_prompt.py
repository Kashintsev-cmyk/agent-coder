"""Тесты: промт суммаризации запрещает додумывание, temperature=0.0."""

from __future__ import annotations

import uuid
from types import SimpleNamespace

import pytest

from agent_coder.agent import compression


def test_summary_system_forbids_hallucinations() -> None:
    text = compression._SUMMARY_SYSTEM
    assert "ТОЛЬКО факты" in text
    assert "НЕ додумывай" in text
    assert "не подтверждён" in text


def test_summary_system_has_structure() -> None:
    text = compression._SUMMARY_SYSTEM
    for section in ("### Задача", "### Сделано", "### Проверено", "### Осталось"):
        assert section in text, f"missing section: {section}"


def test_summary_update_system_warns_about_previous_hallucinations() -> None:
    text = compression._SUMMARY_UPDATE_SYSTEM
    assert "(не подтверждено)" in text
    assert "НЕ додумывай" in text
    for section in ("### Задача", "### Сделано", "### Проверено", "### Осталось"):
        assert section in text, f"missing section: {section}"


def _long_session(n: int = 12) -> SimpleNamespace:
    msgs = [{"role": "system", "content": "sys"}]
    for i in range(n - 1):
        role = "user" if i % 2 == 0 else "assistant"
        msgs.append({"role": role, "content": f"message {i} " + "x" * 100})
    return SimpleNamespace(
        id=uuid.uuid4().hex,
        messages=msgs,
        model="test/model",
        total_usage=None,
        summary_count=0,
        last_input_tokens=0,
    )


@pytest.mark.asyncio
async def test_compress_passes_temperature_zero() -> None:
    session = _long_session()
    calls: list[dict] = []

    async def fake_chat(**kwargs):
        calls.append(kwargs)
        raise RuntimeError("stop-here")

    client = SimpleNamespace(chat=fake_chat)
    result = await compression.compress(session, client)
    assert result is None
    assert len(calls) == 1
    assert calls[0]["temperature"] == 0.0
