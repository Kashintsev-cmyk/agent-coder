"""Тесты ModelCatalog: refresh из API, дисковый кэш, fallback, парсинг."""
from __future__ import annotations

import json
import time

import httpx
import pytest

from agent_coder.config import config
from agent_coder.model_catalog import _CACHE_VERSION, ModelCatalog
from agent_coder.models import _FALLBACK_CATALOG

# Id, которых гарантированно нет ни в CURATED_MODEL_IDS, ни в fallback.
UNKNOWN_CURATED_ID = "vendor/definitely-not-in-catalog"
NOT_CURATED_ID = "vendor/not-curated-xyz"


@pytest.fixture(autouse=True)
def isolated_cache_dir(tmp_path, monkeypatch):
    """Подменить MODELS_CACHE_DIR на tmp_path (не трогаем реальный кэш)."""
    cache_dir = tmp_path / "models-cache"
    monkeypatch.setattr(config, "MODELS_CACHE_DIR", str(cache_dir))
    return cache_dir


@pytest.fixture
def curated_ids():
    return list(config.CURATED_MODEL_IDS)


@pytest.fixture
def fake_api_response(curated_ids):
    """Ответ /api/v1/models: полная модель, текстовая, без reasoning_effort,
    плюс модель, которой нет в CURATED_MODEL_IDS (должна игнорироваться)."""
    full_id, text_id, no_effort_id = curated_ids[0], curated_ids[1], curated_ids[2]
    return {
        "data": [
            {
                "id": full_id,
                "name": "Full Model",
                "context_length": 1_048_576,
                "architecture": {"input_modalities": ["text", "image"]},
                "supported_parameters": [
                    "tools",
                    "reasoning",
                    "include_reasoning",
                    "reasoning_effort",
                ],
                "pricing": {"prompt": 0.00000239, "completion": 0.000047},
                "pricing_units": "token",
            },
            {
                "id": text_id,
                "name": "Text Only",
                "context_length": 262_144,
                "architecture": {"input_modalities": ["text"]},
                "supported_parameters": ["tools", "reasoning"],
                "pricing": {"prompt": 0.000019, "completion": 0.000080},
                "pricing_units": "token",
            },
            {
                "id": no_effort_id,
                "name": "No Effort",
                "context_length": 131_072,
                "architecture": {"input_modalities": ["text"]},
                "supported_parameters": ["tools"],
                "pricing": {"prompt": 0.0, "completion": 0.0},
                "pricing_units": "token",
            },
            {
                "id": NOT_CURATED_ID,
                "name": "Ignored",
                "context_length": 999,
                "architecture": {"input_modalities": ["text"]},
                "supported_parameters": ["tools"],
                "pricing": {"prompt": 0.5, "completion": 0.5},
                "pricing_units": "token",
            },
        ]
    }


def _make_client(response_json, status_code=200):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status_code, json=response_json)

    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


def _new_catalog() -> ModelCatalog:
    c = ModelCatalog()
    c.load_from_fallback()
    return c


# --- init/конфиг ---

def test_init_overrides_params(tmp_path):
    c = ModelCatalog()
    c.init(
        cache_dir=tmp_path / "x",
        cache_ttl_hours=5,
        api_timeout=1.5,
        api_base_url="https://example.test/api",
        api_key="k",
    )
    assert c.cache_path == tmp_path / "x" / "models.json"
    assert c._cache_ttl_hours == 5
    assert c._api_timeout == 1.5


# --- fallback ---

def test_load_from_fallback_source_and_order(curated_ids):
    c = _new_catalog()
    assert c.source == "fallback"
    assert [m.id for m in c.all()] == curated_ids


def test_load_from_fallback_placeholder_for_missing(monkeypatch):
    monkeypatch.setattr(
        config, "CURATED_MODEL_IDS", [UNKNOWN_CURATED_ID, *config.CURATED_MODEL_IDS[1:]]
    )
    c = ModelCatalog()
    c.load_from_fallback()
    model = c.get(UNKNOWN_CURATED_ID)
    assert model is not None
    assert model.name == UNKNOWN_CURATED_ID
    assert model.tag == "unknown"


def test_global_singleton_ready_at_import():
    from agent_coder.model_catalog import catalog

    assert catalog.all()  # fallback подгружен при импорте
    assert catalog.source in {"cache", "fallback", "api"}


# --- API ---

async def test_refresh_parses_and_sets_source(fake_api_response):
    c = _new_catalog()
    client = _make_client(fake_api_response)
    ok = await c.refresh(client=client)
    await client.aclose()
    assert ok is True
    assert c.source == "api"


async def test_refresh_parses_capabilities(fake_api_response, curated_ids):
    c = _new_catalog()
    client = _make_client(fake_api_response)
    await c.refresh(client=client)
    await client.aclose()

    full = c.get(curated_ids[0])
    assert full.vision is True
    assert full.tools is True
    assert full.reasoning is True
    assert full.reasoning_effort is True
    assert full.web_search_builtin is False  # нет web_search в pricing

    text = c.get(curated_ids[1])
    assert text.vision is False
    assert text.tools is True
    assert text.reasoning is True
    assert text.reasoning_effort is False

    no_effort = c.get(curated_ids[2])
    assert no_effort.reasoning is False
    assert no_effort.reasoning_effort is False


async def test_refresh_pricing_converted_to_per_million(fake_api_response, curated_ids):
    c = _new_catalog()
    client = _make_client(fake_api_response)
    await c.refresh(client=client)
    await client.aclose()

    full = c.get(curated_ids[0])
    assert full.input_price == pytest.approx(2.39)
    assert full.output_price == pytest.approx(47.0)

    no_effort = c.get(curated_ids[2])
    assert no_effort.input_price == 0.0
    assert no_effort.output_price == 0.0


async def test_refresh_ignores_non_curated(fake_api_response, curated_ids):
    c = _new_catalog()
    client = _make_client(fake_api_response)
    await c.refresh(client=client)
    await client.aclose()
    assert c.get(NOT_CURATED_ID) is None


async def test_refresh_web_search_builtin(fake_api_response, curated_ids):
    fake_api_response["data"][0]["pricing"]["web_search"] = 0.001
    c = _new_catalog()
    client = _make_client(fake_api_response)
    await c.refresh(client=client)
    await client.aclose()
    assert c.get(curated_ids[0]).web_search_builtin is True


async def test_refresh_web_search_options_param(fake_api_response, curated_ids):
    fake_api_response["data"][1]["supported_parameters"].append("web_search_options")
    c = _new_catalog()
    client = _make_client(fake_api_response)
    await c.refresh(client=client)
    await client.aclose()
    assert c.get(curated_ids[1]).web_search_builtin is True


async def test_refresh_tag_taken_from_fallback(fake_api_response, curated_ids):
    c = _new_catalog()
    client = _make_client(fake_api_response)
    await c.refresh(client=client)
    await client.aclose()
    expected = _FALLBACK_CATALOG[curated_ids[0]].tag
    assert c.get(curated_ids[0]).tag == expected


async def test_refresh_reasoning_effort_anomaly_forces_reasoning(
    fake_api_response, curated_ids
):
    # Убираем "reasoning", но оставляем "reasoning_effort" — аномалия API.
    fake_api_response["data"][0]["supported_parameters"] = [
        "tools",
        "reasoning_effort",
    ]
    c = _new_catalog()
    client = _make_client(fake_api_response)
    ok = await c.refresh(client=client)
    await client.aclose()
    assert ok is True
    model = c.get(curated_ids[0])
    assert model.reasoning_effort is True
    assert model.reasoning is True  # форсировано


async def test_refresh_missing_id_uses_fallback(fake_api_response, curated_ids):
    # Третьей curated-модели нет в ответе API.
    fake_api_response["data"] = [
        m for m in fake_api_response["data"] if m["id"] != curated_ids[2]
    ]
    c = _new_catalog()
    client = _make_client(fake_api_response)
    await c.refresh(client=client)
    await client.aclose()
    assert c.get(curated_ids[2]) == _FALLBACK_CATALOG[curated_ids[2]]


async def test_refresh_missing_unknown_id_placeholder(fake_api_response, monkeypatch):
    monkeypatch.setattr(
        config, "CURATED_MODEL_IDS", [UNKNOWN_CURATED_ID, *config.CURATED_MODEL_IDS[1:]]
    )
    c = _new_catalog()
    client = _make_client(fake_api_response)
    ok = await c.refresh(client=client)
    await client.aclose()
    assert ok is True
    model = c.get(UNKNOWN_CURATED_ID)
    assert model is not None
    assert model.tag == "unknown"


async def test_refresh_survives_http_error():
    c = _new_catalog()
    before_source = c.source
    client = _make_client({"error": "boom"}, status_code=500)
    ok = await c.refresh(client=client)
    await client.aclose()
    assert ok is False
    assert c.source == before_source  # состояние не изменилось


async def test_refresh_survives_bad_json():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text="not json")

    c = _new_catalog()
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    ok = await c.refresh(client=client)
    await client.aclose()
    assert ok is False


# --- дисковый кэш ---

async def test_refresh_writes_cache(fake_api_response, isolated_cache_dir):
    c = _new_catalog()
    client = _make_client(fake_api_response)
    await c.refresh(client=client)
    await client.aclose()

    cache_file = isolated_cache_dir / "models.json"
    assert cache_file.exists()
    payload = json.loads(cache_file.read_text(encoding="utf-8"))
    assert payload["_version"] == _CACHE_VERSION
    assert payload["_source"] == "api"
    assert isinstance(payload["_loaded_at"], float)
    assert "models" in payload
    # В кэше — наши ModelInfo, а не сырой ответ RouterAI.
    assert "data" not in payload


async def test_load_from_cache_fresh(fake_api_response):
    writer = _new_catalog()
    client = _make_client(fake_api_response)
    await writer.refresh(client=client)
    await client.aclose()

    reader = ModelCatalog()
    reader.load_from_fallback()
    assert reader.load_from_cache() is True
    assert reader.source == "cache"
    assert reader.get(config.DEFAULT_MODEL_ID) is not None


def test_load_from_cache_missing_returns_false():
    c = _new_catalog()
    assert c.load_from_cache() is False
    assert c.source == "fallback"


def test_load_from_cache_expired(fake_api_response, isolated_cache_dir):
    isolated_cache_dir.mkdir(parents=True, exist_ok=True)
    old = time.time() - (config.MODELS_CACHE_TTL + 1) * 3600
    payload = {
        "_version": 1,
        "_loaded_at": old,
        "_source": "api",
        "models": {
            config.DEFAULT_MODEL_ID: {
                "id": config.DEFAULT_MODEL_ID,
                "name": "stale",
                "context": 1,
                "reasoning": False,
            }
        },
    }
    (isolated_cache_dir / "models.json").write_text(
        json.dumps(payload), encoding="utf-8"
    )
    c = _new_catalog()
    assert c.load_from_cache() is False
    assert c.source == "fallback"
    # Не затёрли fallback-данные устаревшей записью.
    assert c.get(config.DEFAULT_MODEL_ID).name != "stale"


def test_load_from_cache_corrupt_json(isolated_cache_dir):
    isolated_cache_dir.mkdir(parents=True, exist_ok=True)
    (isolated_cache_dir / "models.json").write_text("{ broken", encoding="utf-8")
    c = _new_catalog()
    assert c.load_from_cache() is False


def test_load_from_cache_unknown_fields_ignored(isolated_cache_dir):
    isolated_cache_dir.mkdir(parents=True, exist_ok=True)
    payload = {
        "_version": _CACHE_VERSION,
        "_loaded_at": time.time(),
        "_source": "api",
        "models": {
            config.DEFAULT_MODEL_ID: {
                "id": config.DEFAULT_MODEL_ID,
                "name": "cached",
                "context": 42,
                "reasoning": False,
                "totally_unknown_field": "x",
            }
        },
    }
    (isolated_cache_dir / "models.json").write_text(
        json.dumps(payload), encoding="utf-8"
    )
    c = _new_catalog()
    assert c.load_from_cache() is True
    assert c.get(config.DEFAULT_MODEL_ID).name == "cached"
    assert c.get(config.DEFAULT_MODEL_ID).context == 42


# --- get / default ---

async def test_get_unknown_returns_none(fake_api_response):
    c = _new_catalog()
    client = _make_client(fake_api_response)
    await c.refresh(client=client)
    await client.aclose()
    assert c.get("nope/nope") is None


def test_default_is_never_none():
    c = _new_catalog()
    assert c.default() is not None
    assert c.default().id == config.DEFAULT_MODEL_ID


def test_all_order_matches_config(curated_ids):
    c = _new_catalog()
    assert [m.id for m in c.all()] == curated_ids

def test_cache_version_mismatch_ignored(tmp_path):
    """Кэш с другой _version игнорируется — берётся fallback/placeholder."""
    from agent_coder.model_catalog import _CACHE_FILENAME, ModelCatalog

    cache_dir = tmp_path / "cache"
    cache_dir.mkdir()

    # Пишем «старый» кэш с версией 1 (не текущей)
    payload = {
        "_version": 1,
        "_loaded_at": 9999999999.0,  # в будущем — TTL не помешает
        "_source": "api",
        "models": {
            "dummy/x": {
                "id": "dummy/x", "name": "X", "context": 1,
                "reasoning": False, "vision": False, "tools": True,
                "reasoning_effort": False, "web_search_builtin": False,
                "input_price": 0.0, "output_price": 0.0, "tag": "",
            },
        },
    }
    (cache_dir / _CACHE_FILENAME).write_text(
        json.dumps(payload), encoding="utf-8",
    )

    c = ModelCatalog()
    c.init(cache_dir=cache_dir, cache_ttl_hours=24)
    assert c.load_from_cache() is False
    assert c.source == "fallback"  # не "cache"



def test_parse_one_description(fake_api_response):
    """description попадает в ModelInfo из ответа API."""
    from agent_coder.model_catalog import ModelCatalog
    # используем первую модель из fake_api_response, у неё должно быть description
    # если его нет — добавляем искусственно
    item = fake_api_response["data"][0]
    item["description"] = "Тестовое описание модели"

    c = ModelCatalog()
    c.init(api_key="x")
    parsed = c._parse_api_response(fake_api_response)

    # найдём эту модель в результате
    m = parsed.get(item["id"])
    assert m is not None
    assert m.description == "Тестовое описание модели"


def test_parse_one_no_description():
    """Если description отсутствует — ModelInfo.description = ''."""
    from agent_coder.config import config
    from agent_coder.model_catalog import ModelCatalog

    mid = config.CURATED_MODEL_IDS[0]
    fake = {
        "data": [{
            "id": mid,
            "name": "X",
            "context_length": 1000,
            "architecture": {"input_modalities": ["text"]},
            "pricing": {"prompt": 1e-5, "completion": 2e-5},
            "supported_parameters": [],
        }]
    }
    c = ModelCatalog()
    c.init(api_key="x")
    parsed = c._parse_api_response(fake)
    assert parsed[mid].description == ""
