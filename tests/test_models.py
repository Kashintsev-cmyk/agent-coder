"""Тесты capability-флагов ModelInfo, каталога и хелперов supports/capabilities."""
from __future__ import annotations

import pytest

from agent_coder.config import config
from agent_coder.model_catalog import catalog
from agent_coder.models import (
    CAPABILITIES,
    ModelInfo,
    _make_placeholder,
    capabilities,
    find_model,
    supports,
)

# --- ModelInfo: дефолты и инварианты ---

def test_modelinfo_defaults():
    m = ModelInfo(
        id="x",
        name="x",
        context=1,
        reasoning=False,
        input_price=0,
        output_price=0,
        tag="x",
    )
    assert m.vision is False
    assert m.tools is True
    assert m.reasoning_effort is False
    assert m.web_search_builtin is False


def test_reasoning_effort_requires_reasoning():
    with pytest.raises(ValueError):
        ModelInfo(
            id="x",
            name="x",
            context=1,
            reasoning=False,
            reasoning_effort=True,
        )


# --- Каталог и сборка списка моделей из config ---

def test_catalog_all_matches_config_ids():
    assert [m.id for m in catalog.all()] == config.CURATED_MODEL_IDS


def test_curated_ids_are_unique():
    ids = config.CURATED_MODEL_IDS
    assert len(ids) == len(set(ids))


def test_default_model_in_curated():
    default = catalog.default()
    assert default is not None
    assert default.id == config.DEFAULT_MODEL_ID


def test_placeholder_for_unknown_id():
    m = _make_placeholder("no/such-model-xyz")
    assert m.name == "no/such-model-xyz"
    assert m.context == 0
    assert m.tag == "unknown"
    assert m.vision is False
    assert m.reasoning is False
    assert m.reasoning_effort is False
    assert m.web_search_builtin is False
    assert m.tools is True
    assert m.input_price == 0.0
    assert m.output_price == 0.0


# --- supports / capabilities ---

def test_supports_known_reasoning():
    reasoning_model = next(m for m in catalog.all() if m.reasoning)
    assert supports(reasoning_model.id, "reasoning") is True


def test_supports_unknown_model_defaults():
    unknown = "no/such-model-xyz"
    for cap in CAPABILITIES:
        assert supports(unknown, cap) is False


def test_supports_bad_cap():
    with pytest.raises(ValueError):
        supports("any", "bogus")


def test_capabilities_sorted():
    for model_id in [m.id for m in catalog.all()] + ["no/such-model-xyz"]:
        caps = capabilities(model_id)
        assert caps == sorted(caps)
        assert set(caps) <= set(CAPABILITIES)


def test_capabilities_unknown_model():
    assert capabilities("no/such-model-xyz") == []


def test_capabilities_web_search_builtin():
    # Модели с встроенным web search, помеченные в каталоге.
    searched = [m.id for m in catalog.all() if m.web_search_builtin]
    for mid in searched:
        assert supports(mid, "web_search_builtin") is True
        assert "web_search_builtin" in capabilities(mid)


# --- find_model ---

def test_find_model_known():
    first = catalog.all()[0]
    assert find_model(first.id) == first


def test_find_model_unknown():
    assert find_model("no/such-model-xyz") is None


def test_supports_delegates_to_catalog(monkeypatch):
    """supports() читает флаги из catalog._models, а не из static-списка."""
    fake = ModelInfo(
        id="test/x",
        name="Test X",
        context=1000,
        reasoning=True,
        vision=True,
        tools=True,
    )
    monkeypatch.setattr(catalog, "_models", {"test/x": fake})
    assert supports("test/x", "vision") is True
    assert supports("test/x", "tools") is True
    assert supports("test/x", "web_search_builtin") is False
    assert capabilities("test/x") == sorted(
        ["vision", "tools", "reasoning"]
    )
