"""Каталог метаданных моделей RouterAI с дисковым кэшем и fallback-цепочкой.

Источники данных (в порядке приоритета):

1. **API** (`/api/v1/models` RouterAI) — ``refresh()``. Актуальные цены,
   context_length и capability-флаги.
2. **Диск** (``~/.cache/agent-coder/models.json``) — ``load_from_cache()``.
   Свежий снимок API, не старше ``MODELS_CACHE_TTL`` часов.
3. **Fallback** (``agent_coder.models._FALLBACK_CATALOG``) —
   ``load_from_fallback()``. Вшитый snapshot на случай, когда нет ни API,
   ни кэша.

Модуль сам по себе НЕ делает сетевых запросов при импорте: он лишь
загружает fallback и (если есть) свежий кэш. Асинхронный ``refresh()``
дёргает вызывающая сторона (bot.py) на старте.

В кэше храним НЕ сырой ответ RouterAI, а уже распарсенные ``ModelInfo``
(as dict) — включая поля, которых в API нет (например, ``tag``). Так кэш
читается без повторного парсинга.
"""
from __future__ import annotations

import json
import logging
import os
import time
from dataclasses import asdict, fields
from pathlib import Path
from typing import Any

import httpx

from agent_coder.config import config
from agent_coder.models import (
    _FALLBACK_CATALOG,
    ModelInfo,
    _make_placeholder,
)

logger = logging.getLogger(__name__)

_CACHE_VERSION = 1
_CACHE_FILENAME = "models.json"


def _default_cache_dir() -> Path:
    """Директория кэша по умолчанию: ``~/.cache/agent-coder``."""
    return Path.home() / ".cache" / "agent-coder"


def _modelinfo_from_dict(data: dict[str, Any]) -> ModelInfo:
    """Собрать ``ModelInfo`` из dict, отбросив неизвестные ключи.

    Кэш может быть записан более старой/новой версией кода с другим набором
    полей — лишнее игнорируем, недостающее добираем дефолтами dataclass'а.
    """
    known = {f.name for f in fields(ModelInfo)}
    filtered = {k: v for k, v in data.items() if k in known}
    return ModelInfo(**filtered)


class ModelCatalog:
    """Загрузчик метаданных моделей RouterAI с кэшем и fallback.

    Экземпляр stateful: держит текущий снимок моделей и источник, из которого
    он получен. Глобальный синглтон — ``catalog`` (см. конец модуля).
    """

    def __init__(self) -> None:
        self._models: dict[str, ModelInfo] = {}
        self._source: str = "fallback"

        # Параметры, переопределяемые через init() (для тестов).
        self._cache_dir: Path = (
            Path(config.MODELS_CACHE_DIR)
            if config.MODELS_CACHE_DIR
            else _default_cache_dir()
        )
        self._cache_ttl_hours: int = config.MODELS_CACHE_TTL
        self._api_timeout: float = config.MODELS_API_TIMEOUT
        self._api_base_url: str = config.ROUTERAI_BASE_URL
        self._api_key: str = config.ROUTERAI_API_KEY

    # ------------------------------------------------------------------
    # Настройка
    # ------------------------------------------------------------------

    def init(
        self,
        *,
        cache_dir: Path | None = None,
        cache_ttl_hours: int | None = None,
        api_timeout: float | None = None,
        api_base_url: str | None = None,
        api_key: str | None = None,
    ) -> None:
        """Переопределить параметры каталога (обычно — в тестах)."""
        if cache_dir is not None:
            self._cache_dir = Path(cache_dir)
        if cache_ttl_hours is not None:
            self._cache_ttl_hours = cache_ttl_hours
        if api_timeout is not None:
            self._api_timeout = api_timeout
        if api_base_url is not None:
            self._api_base_url = api_base_url
        if api_key is not None:
            self._api_key = api_key

    # ------------------------------------------------------------------
    # Публичный доступ
    # ------------------------------------------------------------------

    @property
    def cache_path(self) -> Path:
        return self._cache_dir / _CACHE_FILENAME

    @property
    def source(self) -> str:
        """'api' | 'cache' | 'fallback' — что загружено сейчас."""
        return self._source

    def all(self) -> list[ModelInfo]:
        """Модели в порядке ``config.CURATED_MODEL_IDS``.

        id из configured-списка, которых нет в текущем снимке, добираются
        placeholder'ом (см. ``load_from_fallback``), чтобы порядок и полнота
        всегда соответствовали конфигу.
        """
        result: list[ModelInfo] = []
        for mid in config.CURATED_MODEL_IDS:
            model = self._models.get(mid)
            if model is None:
                model = self._fallback_for(mid)
            result.append(model)
        return result

    def get(self, model_id: str) -> ModelInfo | None:
        """Модель по id или ``None`` (если id нет даже в fallback)."""
        model = self._models.get(model_id)
        if model is not None:
            return model
        return self._fallback_for(model_id, warn=False)

    def default(self) -> ModelInfo:
        """``DEFAULT_MODEL_ID`` или первая доступная. Никогда не None."""
        model = self.get(config.DEFAULT_MODEL_ID)
        if model is not None:
            return model
        models = self.all()
        if models:
            return models[0]
        # Совсем крайний случай: вернём placeholder дефолтного id.
        return _make_placeholder(config.DEFAULT_MODEL_ID)

    # ------------------------------------------------------------------
    # Fallback
    # ------------------------------------------------------------------

    def _fallback_for(self, model_id: str, *, warn: bool = True) -> ModelInfo | None:
        """Модель из ``_FALLBACK_CATALOG`` или placeholder.

        ``None`` только если ``model_id`` не входит в ``CURATED_MODEL_IDS``
        (тогда это чужой id, и подставлять его в UI нет смысла).
        """
        if model_id in _FALLBACK_CATALOG:
            return _FALLBACK_CATALOG[model_id]
        if model_id in config.CURATED_MODEL_IDS:
            if warn:
                logger.warning(
                    "Model %r отсутствует в каталоге и fallback — placeholder",
                    model_id,
                )
            return _make_placeholder(model_id)
        return None

    def load_from_fallback(self) -> None:
        """Инициализировать снимок из ``_FALLBACK_CATALOG``.

        Отсутствующие в fallback curated-id добираются placeholder'ами, чтобы
        ``all()`` не был пустым даже до ``refresh()``.
        """
        snapshot: dict[str, ModelInfo] = {}
        for mid in config.CURATED_MODEL_IDS:
            snapshot[mid] = self._fallback_for(mid, warn=True)  # type: ignore[assignment]
        self._models = snapshot
        self._source = "fallback"

    # ------------------------------------------------------------------
    # Дисковый кэш
    # ------------------------------------------------------------------

    def load_from_cache(self) -> bool:
        """Читает кэш, если он свежий (в пределах TTL).

        Возвращает True, если удалось загрузить. При любой ошибке (нет файла,
        битый JSON, устаревшая запись) логируем на уровне debug/warning и
        оставляем предыдущее состояние.
        """
        path = self.cache_path
        try:
            raw = path.read_text(encoding="utf-8")
        except FileNotFoundError:
            logger.debug("Кэш каталога моделей не найден: %s", path)
            return False
        except OSError as exc:
            logger.warning("Не удалось прочитать кэш каталога %s: %s", path, exc)
            return False

        try:
            payload = json.loads(raw)
        except (ValueError, TypeError) as exc:
            logger.warning("Битый JSON в кэше каталога %s: %s", path, exc)
            return False

        if not isinstance(payload, dict):
            logger.warning("Кэш каталога %s имеет неожиданный формат", path)
            return False

        loaded_at = payload.get("_loaded_at")
        if not isinstance(loaded_at, (int, float)):
            logger.warning("В кэше каталога %s нет валидного _loaded_at", path)
            return False

        ttl_seconds = self._cache_ttl_hours * 3600
        age = time.time() - float(loaded_at)
        if age > ttl_seconds:
            logger.info(
                "Кэш каталога моделей устарел (%.1f ч > %d ч) — refresh нужен",
                age / 3600,
                self._cache_ttl_hours,
            )
            return False

        models_raw = payload.get("models")
        if not isinstance(models_raw, dict):
            logger.warning("В кэше каталога %s нет секции models", path)
            return False

        try:
            parsed = {
                mid: _modelinfo_from_dict(data)
                for mid, data in models_raw.items()
                if isinstance(data, dict)
            }
        except (TypeError, ValueError) as exc:
            logger.warning("Не удалось разобрать модели из кэша %s: %s", path, exc)
            return False

        if not parsed:
            logger.warning("Кэш каталога %s пуст — игнорирую", path)
            return False

        self._models = parsed
        self._source = "cache"
        logger.info("Каталог моделей загружен из кэша: %s (%d шт.)", path, len(parsed))
        return True

    def _write_cache(self) -> None:
        """Атомарно сохранить текущий снимок в кэш. Ошибки — warning, не падаем."""
        path = self.cache_path
        payload = {
            "_version": _CACHE_VERSION,
            "_loaded_at": time.time(),
            "_source": "api",
            "models": {mid: asdict(m) for mid, m in self._models.items()},
        }
        tmp_path = path.with_name(path.name + ".tmp")
        try:
            self._cache_dir.mkdir(parents=True, exist_ok=True)
            tmp_path.write_text(
                json.dumps(payload, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            os.replace(tmp_path, path)
        except OSError as exc:
            logger.warning("Не удалось записать кэш каталога %s: %s", path, exc)
            try:
                tmp_path.unlink(missing_ok=True)
            except OSError:
                pass

    # ------------------------------------------------------------------
    # API
    # ------------------------------------------------------------------

    async def refresh(self, *, client: httpx.AsyncClient | None = None) -> bool:
        """Тянет ``/models``, парсит, обновляет in-memory, пишет кэш.

        Возвращает True при успехе. Никогда не бросает наружу — все ошибки
        логирует, оставляет предыдущее состояние. ``client`` — для тестов
        (MockTransport); если None — создаёт свой короткоживущий AsyncClient
        с ``timeout=api_timeout``.
        """
        owns_client = client is None
        if owns_client:
            client = httpx.AsyncClient(timeout=self._api_timeout)

        url = f"{self._api_base_url.rstrip('/')}/models"
        headers = {"Authorization": f"Bearer {self._api_key}"} if self._api_key else {}
        try:
            resp = await client.get(url, headers=headers)
            resp.raise_for_status()
            data = resp.json()
        except Exception as exc:
            logger.warning("Не удалось получить /models: %s", exc)
            return False
        finally:
            if owns_client and client is not None:
                await client.aclose()

        try:
            models = self._parse_api_response(data)
        except Exception as exc:
            logger.warning("Не удалось разобрать ответ /models: %s", exc)
            return False

        if not models:
            logger.warning("Ответ /models не дал ни одной известной модели")
            return False

        self._models = models
        self._source = "api"
        self._write_cache()
        logger.info("Каталог моделей обновлён из API (%d шт.)", len(models))
        return True

    # ------------------------------------------------------------------
    # Парсинг
    # ------------------------------------------------------------------

    def _parse_api_response(self, data: Any) -> dict[str, ModelInfo]:
        """Распарсить ответ ``/api/v1/models`` в снимок по CURATED_MODEL_IDS.

        Ответ: ``{"data": [{id, name, context_length, architecture, pricing,
        pricing_units, supported_parameters, ...}, ...]}``.

        Фильтруем по ``config.CURATED_MODEL_IDS``. Если id нет в ответе —
        берём ``_FALLBACK_CATALOG[id]`` или placeholder.
        """
        if not isinstance(data, dict):
            raise TypeError("ответ /models — не объект")
        raw_models = data.get("data")
        if not isinstance(raw_models, list):
            raise TypeError("в ответе /models нет списка 'data'")

        by_id: dict[str, dict[str, Any]] = {}
        for item in raw_models:
            if isinstance(item, dict) and isinstance(item.get("id"), str):
                by_id[item["id"]] = item

        result: dict[str, ModelInfo] = {}
        for mid in config.CURATED_MODEL_IDS:
            raw = by_id.get(mid)
            if raw is not None:
                try:
                    result[mid] = self._parse_one(raw)
                    continue
                except Exception as exc:
                    logger.warning(
                        "Не удалось разобрать модель %r из API (%s) — fallback",
                        mid,
                        exc,
                    )
            # Нет в ответе или битые данные — fallback/placeholder.
            fallback = self._fallback_for(mid, warn=False)
            if fallback is not None:
                result[mid] = fallback
                logger.warning(
                    "Model %r отсутствует в ответе /models — взят fallback", mid
                )
        return result

    def _parse_one(self, raw: dict[str, Any]) -> ModelInfo:
        """Собрать ``ModelInfo`` из одной записи ответа API."""
        mid = raw["id"]

        architecture = raw.get("architecture") or {}
        input_modalities = architecture.get("input_modalities") or []
        params = raw.get("supported_parameters") or []
        pricing = raw.get("pricing") or {}

        vision = "image" in input_modalities
        tools = "tools" in params
        reasoning = "reasoning" in params or "include_reasoning" in params
        reasoning_effort = "reasoning_effort" in params
        web_search_builtin = (
            "web_search" in pricing or "web_search_options" in params
        )

        # Инвариант ModelInfo: reasoning_effort ⇒ reasoning. Если API даёт
        # аномалию — не падаем, логируем и принудительно включаем reasoning.
        if reasoning_effort and not reasoning:
            logger.warning(
                "Model %r: API вернул reasoning_effort=True при reasoning=False "
                "— форсирую reasoning=True",
                mid,
            )
            reasoning = True

        input_price = self._price_per_million(pricing.get("prompt"))
        output_price = self._price_per_million(pricing.get("completion"))

        # tag в API нет — берём из fallback, если есть.
        fallback = _FALLBACK_CATALOG.get(mid)
        tag = fallback.tag if fallback is not None else ""

        context = raw.get("context_length")
        try:
            context_int = int(context) if context is not None else 0
        except (TypeError, ValueError):
            context_int = 0

        name = raw.get("name") or (fallback.name if fallback else mid)

        return ModelInfo(
            id=mid,
            name=name,
            context=context_int,
            reasoning=reasoning,
            vision=vision,
            tools=tools,
            reasoning_effort=reasoning_effort,
            web_search_builtin=web_search_builtin,
            input_price=input_price,
            output_price=output_price,
            tag=tag,
        )

    @staticmethod
    def _price_per_million(value: Any) -> float:
        """Цена из API в руб/ТОКЕН → руб/1M. Отсутствует/0 → 0.0."""
        if value is None:
            return 0.0
        try:
            per_token = float(value)
        except (TypeError, ValueError):
            return 0.0
        if per_token <= 0:
            return 0.0
        return per_token * 1_000_000


# Глобальный синглтон каталога. При импорте модуля:
# 1) грузим fallback, чтобы all() не был пустым;
# 2) пробуем свежий дисковый кэш (перекрывает fallback).
# Асинхронный refresh() НЕ вызываем — это делает bot.py на старте.
catalog = ModelCatalog()
catalog.load_from_fallback()
catalog.load_from_cache()
