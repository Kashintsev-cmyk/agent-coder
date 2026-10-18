"""F22: placeholder без шума при инициализации; grok-4.20 в fallback."""

from __future__ import annotations

import logging

import pytest

from agent_coder.model_catalog import ModelCatalog, catalog
from agent_coder.models import _FALLBACK_CATALOG, _make_placeholder


def test_grok_4_20_in_fallback() -> None:
    assert "x-ai/grok-4.20" in _FALLBACK_CATALOG
    m = _FALLBACK_CATALOG["x-ai/grok-4.20"]
    assert m.context == 2_000_000
    assert m.reasoning is True
    assert m.tools is True
    assert m.input_price > 0


def test_make_placeholder_warns_by_default(caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.WARNING, logger="agent_coder.models"):
        _make_placeholder("no/such-id-xyz")
    assert any("placeholder" in r.message.lower() for r in caplog.records)


def test_make_placeholder_quiet_when_warn_false(
    caplog: pytest.LogCaptureFixture,
) -> None:
    with caplog.at_level(logging.WARNING, logger="agent_coder.models"):
        _make_placeholder("no/such-id-xyz", warn=False)
    assert not any("placeholder" in r.message.lower() for r in caplog.records)


def test_load_from_fallback_quiet(caplog: pytest.LogCaptureFixture) -> None:
    """Инициализация catalog не должна шуметь placeholder-warning'ами."""
    cat = ModelCatalog()
    with caplog.at_level(logging.WARNING, logger="agent_coder.models"):
        cat.load_from_fallback()
    assert not any("placeholder" in r.message.lower() for r in caplog.records)


def test_singleton_catalog_has_grok_4_20() -> None:
    """После импорта curated-id из config попадают в snapshot."""
    model = catalog.get("x-ai/grok-4.20")
    assert model is not None
    assert model.id == "x-ai/grok-4.20"
