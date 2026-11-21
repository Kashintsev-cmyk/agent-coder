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
from dataclasses import asdict, fields, replace
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

# Версию бампаем при несовместимом изменении полей ModelInfo,
# чтобы старый кэш не подсовывал дефолты вместо новых полей.
_CACHE_VERSION = 4
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
                model = self._fallback_for(mid) or _make_placeholder(mid)
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
            # Один warning — из _make_placeholder (warn=warn).
            # Дублировать здесь не нужно.
            return _make_placeholder(model_id, warn=warn)
        return None

    def load_from_fallback(self) -> None:
        """Инициализировать снимок из ``_FALLBACK_CATALOG``.

        Отсутствующие в fallback curated-id добираются placeholder'ами, чтобы
        ``all()`` не был пустым даже до ``refresh()``.

        Placeholder здесь — ожидаемое поведение (комплектация snapshot'а),
        не «модель потерялась»: warning на import config был бы шумом.
        Если curated-id нет в fallback — одна строка ``logger.info`` с
        полным списком (F22). Реальный warning — в ``all()``/``get()`` при
        фактическом запросе.
        """
        snapshot: dict[str, ModelInfo] = {}
        placeholders: list[str] = []
        for mid in config.CURATED_MODEL_IDS:
            model = self._fallback_for(mid, warn=False)
            if model is None:
                model = _make_placeholder(mid)
                placeholders.append(mid)
            snapshot[mid] = model
        self._models = snapshot
        self._source = "fallback"
        if placeholders:
            logger.info(
                "Catalog fallback: %d curated id(s) без записи в snapshot, "
                "использован placeholder: %s",
                len(placeholders),
                ", ".join(placeholders),
            )

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

        version = payload.get("_version")
        if version != _CACHE_VERSION:
            logger.info(
                "Кэш каталога %s имеет версию %r, ожидается %r — игнорирую "
                "(подтянем из API или fallback)",
                path,
                version,
                _CACHE_VERSION,
            )
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

        # Фильтруем кэш по актуальному config.CURATED_MODEL_IDS —
        # если env поменяли, старые модели не должны мелькать в UI.
        allowed = set(config.CURATED_MODEL_IDS)
        try:
            parsed = {
                mid: _modelinfo_from_dict(data)
                for mid, data in models_raw.items()
                if isinstance(data, dict) and mid in allowed
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
        if client is None:
            client = httpx.AsyncClient(timeout=self._api_timeout)
        # mypy не сужает тип после присваивания в if — фиксируем явно.

        if client is None:
            raise RuntimeError("Client initialization failed")

        url = f"{self._api_base_url.rstrip('/')}/models"
        headers = {"Authorization": f"Bearer {self._api_key}"} if self._api_key else {}

        try:
            try:
                resp = await client.get(url, headers=headers)
                resp.raise_for_status()
                data = resp.json()
            except Exception as exc:
                logger.warning("Не удалось получить /models: %s", exc)
                return False

            try:
                models = self._parse_api_response(data)
            except Exception as exc:
                logger.warning("Не удалось разобрать ответ /models: %s", exc)
                return False

            if not models:
                logger.warning("Ответ /models не дал ни одной известной модели")
                return False

            # Точные capability-флаги, long-context тиры и лимит выхода есть
            # только в /models/{id}/endpoints. Ошибки по отдельной модели не
            # фатальны: остаются данные из /models (tier_threshold=None →
            # legacy-поведение в compression.model_price).
            await self._refresh_endpoints(client, headers, models)
        finally:
            if owns_client:
                await client.aclose()

        self._models = models
        self._source = "api"
        self._write_cache()
        logger.info("Каталог моделей обновлён из API (%d шт.)", len(models))
        return True

    async def _refresh_endpoints(
        self,
        client: httpx.AsyncClient,
        headers: dict[str, str],
        models: dict[str, ModelInfo],
    ) -> None:
        """Дополнить модели данными из ``/models/{id}/endpoints``.

        Зачем: ``/models`` отдаёт АГРЕГАТ ``supported_parameters`` по всем
        провайдерам, и он может обещать параметр, которого нет ни у одного
        endpoint (реальный случай: ``reasoning_effort`` у 6 моделей из 8 —
        ``agg ⊃ union``). Плюс long-context тиры (``variable_pricings``
        type=``prompt-threshold``) в ``/models`` отсутствуют вовсе.

        Каждый запрос обёрнут в try/except: недоступность endpoints не должна
        ронять обновление каталога — останутся значения из ``/models``.
        """
        base = self._api_base_url.rstrip("/")
        for mid, info in models.items():
            url = f"{base}/models/{mid}/endpoints"
            try:
                resp = await client.get(url, headers=headers)
                resp.raise_for_status()
                payload = resp.json()
            except Exception as exc:
                logger.info("Не удалось получить endpoints для %r: %s", mid, exc)
                continue
            try:
                models[mid] = self._merge_endpoints(info, payload)
            except Exception as exc:
                logger.warning("Не удалось разобрать endpoints %r: %s", mid, exc)

    @staticmethod
    def _endpoint_list(payload: Any) -> list[dict[str, Any]]:
        """``data`` в ответе endpoints — объект с массивом ``endpoints``."""
        if not isinstance(payload, dict):
            return []
        data = payload.get("data")
        if not isinstance(data, dict):
            return []
        eps = data.get("endpoints")
        return [e for e in eps if isinstance(e, dict)] if isinstance(eps, list) else []

    def _merge_endpoints(self, info: ModelInfo, payload: Any) -> ModelInfo:
        """Наложить данные endpoints на модель из ``/models``.

        Capability-флаги считаем по ПЕРЕСЕЧЕНИЮ: балансировщик может увести
        запрос на любой endpoint, поэтому «параметр поддерживается моделью»
        означает «его принимают все провайдеры». Иначе мы отправляем параметр,
        который часть провайдеров молча игнорирует (а часть — отвергает).
        """
        eps = self._endpoint_list(payload)
        if not eps:
            return info

        param_sets = [set(e.get("supported_parameters") or []) for e in eps]
        universal = set.intersection(*param_sets) if param_sets else set()

        # web_search — берём ОБЪЕДИНЕНИЕ: важно, есть ли поиск хоть где-то,
        # а конкретный провайдер выбирается роутером.
        web_search = any(
            "web_search" in (e.get("pricing") or {})
            or "web_search_options" in (e.get("supported_parameters") or [])
            for e in eps
        )

        tier_threshold, tier_in, tier_out = self._extract_tier(eps)

        completion_limits = [
            e["max_completion_tokens"]
            for e in eps
            if isinstance(e.get("max_completion_tokens"), int)
            and e["max_completion_tokens"] > 0
        ]

        return replace(
            info,
            tools="tools" in universal,
            reasoning=("reasoning" in universal or "include_reasoning" in universal),
            reasoning_effort="reasoning_effort" in universal,
            verbosity="verbosity" in universal,
            web_search_builtin=web_search,
            tier_threshold=tier_threshold,
            tier_input_factor=tier_in,
            tier_output_factor=tier_out,
            max_completion_tokens=min(completion_limits) if completion_limits else 0,
        )

    @staticmethod
    def _extract_tier(
        eps: list[dict[str, Any]],
    ) -> tuple[int | None, float, float]:
        """Long-context тир из ``variable_pricings``.

        Возвращает ``(threshold, input_factor, output_factor)``:
        ``(0, 1.0, 1.0)`` — endpoints получены, тира нет;
        ``(None, 1.0, 1.0)`` — данных нет (не смогли посчитать).
        """
        rows: list[tuple[int, float, float]] = []
        for e in eps:
            pricing = e.get("pricing") or {}
            base_prompt = pricing.get("prompt")
            base_completion = pricing.get("completion")
            for vp in e.get("variable_pricings") or []:
                if not isinstance(vp, dict) or vp.get("type") != "prompt-threshold":
                    continue
                threshold = vp.get("threshold")
                if not isinstance(threshold, int) or threshold <= 0:
                    continue
                in_f = 1.0
                out_f = 1.0
                if base_prompt and vp.get("prompt"):
                    in_f = float(vp["prompt"]) / float(base_prompt)
                if base_completion and vp.get("completion"):
                    out_f = float(vp["completion"]) / float(base_completion)
                rows.append((threshold, in_f, out_f))

        if not rows:
            return 0, 1.0, 1.0

        # Порог одинаков у всех провайдеров модели; берём самый частый на случай
        # расхождения, чтобы не занизить оценку стоимости.
        threshold = min(r[0] for r in rows)
        same_thr = [r for r in rows if r[0] == threshold]
        in_factor = max(r[1] for r in same_thr)
        out_factor = max(r[2] for r in same_thr)
        return threshold, in_factor, out_factor

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
        verbosity = "verbosity" in params
        description = raw.get("description") or ""
        web_search_builtin = "web_search" in pricing or "web_search_options" in params

        # Инвариант ModelInfo: reasoning_effort ⇒ reasoning. Если API даёт
        # аномалию — не падаем, логируем и принудительно включаем reasoning.
        if reasoning_effort and not reasoning:
            logger.warning(
                "Model %r: API вернул reasoning_effort=True при reasoning=False "
                "— форсирую reasoning=True",
                mid,
            )
            reasoning = True

        units_raw = raw.get("pricing_units") or {}
        # API может отдать pricing_units как dict ({"prompt": "token", ...})
        # или как одну строку ("token"). Поддерживаем оба вида.
        if isinstance(units_raw, str):
            units = {"prompt": units_raw, "completion": units_raw}
        elif isinstance(units_raw, dict):
            units = units_raw
        else:
            units = {}
        input_price = self._price_per_million(
            pricing.get("prompt"),
            units.get("prompt"),
        )
        output_price = self._price_per_million(
            pricing.get("completion"),
            units.get("completion"),
        )
        cache_read_price = self._price_per_million(
            pricing.get("input_cache_read"),
            units.get("input_cache_read", units.get("prompt")),
        )
        cache_write_price = self._price_per_million(
            pricing.get("input_cache_write"),
            units.get("input_cache_write", units.get("prompt")),
        )

        # tag в API нет — берём из fallback, если есть.
        fallback = _FALLBACK_CATALOG.get(mid)
        tag = fallback.tag if fallback is not None else ""

        context = raw.get("context_length")
        try:
            context_int = int(context) if context is not None else 0
        except (TypeError, ValueError):
            context_int = 0

        # Если API вернул 0 (устаревший fallback или аудио-модель) — берём
        # context из fallback-каталога, если он там есть. Иначе 0 — caller
        # (compression.soft_limit) сам подставит MAX_HISTORY_TOKENS.
        if context_int <= 0 and fallback is not None and fallback.context > 0:
            context_int = fallback.context

        name = raw.get("name") or (fallback.name if fallback else mid)

        return ModelInfo(
            id=mid,
            name=name,
            context=context_int,
            reasoning=reasoning,
            vision=vision,
            tools=tools,
            reasoning_effort=reasoning_effort,
            verbosity=verbosity,
            description=description,
            web_search_builtin=web_search_builtin,
            input_price=input_price,
            output_price=output_price,
            cache_read_price=cache_read_price,
            cache_write_price=cache_write_price,
            tag=tag,
        )

    @staticmethod
    def _price_per_million(value: Any, unit: str | None = None) -> float:
        """Цена из API → руб/1M токенов.

        RouterAI отдаёт `pricing.prompt` в руб за 1 токен и
        `pricing_units.prompt == "token"`. Если unit явно не "token"
        (например, "1m" или "request") — не умножаем, только warning.
        Отсутствующее/неположительное значение → 0.0.
        """
        if value is None:
            return 0.0
        try:
            amount = float(value)
        except (TypeError, ValueError):
            return 0.0
        if amount <= 0:
            return 0.0

        if unit in (None, "", "token"):
            return amount * 1_000_000

        if unit == "1m":
            return amount

        logger.warning(
            "pricing_units=%r не поддерживается — цена не применена",
            unit,
        )
        return 0.0


# Глобальный синглтон каталога. При импорте модуля:
# 1) грузим fallback, чтобы all() не был пустым;
# 2) пробуем свежий дисковый кэш (перекрывает fallback).
# Асинхронный refresh() НЕ вызываем — это делает bot.py на старте.
catalog = ModelCatalog()
catalog.load_from_fallback()
catalog.load_from_cache()
