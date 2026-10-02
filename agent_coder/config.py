"""Конфигурация agent-coder."""
import os
from pathlib import Path
from typing import ClassVar

from dotenv import load_dotenv

load_dotenv()

BASE_DIR = Path(__file__).resolve().parent.parent


def _env_int(name: str, default: str) -> int:
    """int из переменной окружения с понятной ошибкой при кривом значении."""
    raw = os.getenv(name, default)
    try:
        return int(raw)
    except (TypeError, ValueError) as exc:
        raise ValueError(
            f"{name}={raw!r} должен быть целым числом (env {name})"
        ) from exc


def _env_float(name: str, default: str) -> float:
    """float из переменной окружения с понятной ошибкой при кривом значении."""
    raw = os.getenv(name, default)
    try:
        return float(raw)
    except (TypeError, ValueError) as exc:
        raise ValueError(
            f"{name}={raw!r} должен быть числом (env {name})"
        ) from exc


class Config:
    BASE_DIR: Path = BASE_DIR

    # RouterAI
    ROUTERAI_API_KEY: str = os.getenv("ROUTERAI_API_KEY", "")
    ROUTERAI_BASE_URL: str = os.getenv(
        "ROUTERAI_BASE_URL", "https://routerai.ru/api/v1"
    )
    ROUTERAI_TIMEOUT: float = float(os.getenv("ROUTERAI_TIMEOUT", "300.0"))

    # Модели
    MODEL_MAIN: str = os.getenv("MODEL_MAIN", "deepseek/deepseek-v4.1-flash")
    # Зарезервировано на будущее (не используется в коде)
    MODEL_FAST: str = os.getenv("MODEL_FAST", MODEL_MAIN)

    # Список curated-моделей для меню выбора (через запятую). Порядок
    # сохраняется в UI. Метаданные моделей — в `agent_coder.models._FALLBACK_CATALOG`.
    _curated_raw: str = os.getenv("CURATED_MODEL_IDS", "")
    CURATED_MODEL_IDS: ClassVar[list[str]] = [
        mid.strip() for mid in _curated_raw.split(",") if mid.strip()
    ] or [
        "deepseek/deepseek-v4.1-flash",
        "openai/gpt-5.6-luna",
        "z-ai/glm-5.3-flash",
        "google/gemini-3.8-flash",
        "moonshotai/kimi-k2.7-code",
        "anthropic/claude-sonnet-5.5",
        "x-ai/grok-4.7",
    ]

    # Модель по умолчанию (должна входить в CURATED_MODEL_IDS)
    DEFAULT_MODEL_ID: str = os.getenv(
        "DEFAULT_MODEL_ID", "deepseek/deepseek-v4.1-flash"
    )

    # Model catalog (кэш метаданных моделей из RouterAI)
    # TTL дискового кэша каталога моделей, часы (минимум 1)
    MODELS_CACHE_TTL: int = _env_int("MODELS_CACHE_TTL", "24")
    # Директория кэша. Пусто — использовать ~/.cache/agent-coder
    MODELS_CACHE_DIR: str = os.getenv("MODELS_CACHE_DIR", "")
    # Таймаут запроса /api/v1/models, секунды
    MODELS_API_TIMEOUT: float = _env_float("MODELS_API_TIMEOUT", "5.0")

    # Web search (встроенный в RouterAI, через plugins=[{id:web}])
    # engine: "native" (встроенный поиск провайдера) | "exa"
    WEB_SEARCH_ENGINE: str = os.getenv("WEB_SEARCH_ENGINE", "native").strip().lower()
    WEB_SEARCH_MAX_RESULTS: int = _env_int("WEB_SEARCH_MAX_RESULTS", "5")

    # Сжатие истории сессии
    # Верхняя граница input-токенов на один запрос (жёсткий потолок).
    # Итоговый порог = min(model.context * 0.6, MAX_HISTORY_TOKENS).
    MAX_HISTORY_TOKENS: int = _env_int("MAX_HISTORY_TOKENS", "128000")
    # Модель для суммаризации старых сообщений. Должна быть в CURATED_MODEL_IDS
    # (или в _FALLBACK_CATALOG). Дешёвая — для вспомогательной задачи.
    SUMMARY_MODEL: str = os.getenv("SUMMARY_MODEL", "z-ai/glm-5.3-flash")

    # Service tier (приоритет обработки у провайдера)
    # default — стандарт; flex — ~−50% цены, медленнее; priority — быстрее
    SERVICE_TIER: str = os.getenv("SERVICE_TIER", "default").strip().lower()

    # Retry
    MAX_RETRIES: int = int(os.getenv("MAX_RETRIES", "3"))
    RETRY_BACKOFF: float = float(os.getenv("RETRY_BACKOFF", "2.0"))

    # Агент
    DEFAULT_WORKSPACE: str = os.getenv(
        "DEFAULT_WORKSPACE", str(Path.home() / "projects")
    )
    MAX_ITERATIONS: int = int(os.getenv("MAX_ITERATIONS", "50"))
    MAX_ITERATIONS_PLAN: int = int(os.getenv("MAX_ITERATIONS_PLAN", "30"))
    MAX_ITERATIONS_ACT: int = int(os.getenv("MAX_ITERATIONS_ACT", "50"))
    MAX_ITERATIONS_AUDIT: int = int(os.getenv("MAX_ITERATIONS_AUDIT", "100"))

    # Логи
    # Зарезервировано на будущее (не используется в коде)
    LOG_LEVEL: str = os.getenv("LOG_LEVEL", "INFO")

    # Telegram
    TELEGRAM_TOKEN: str = os.getenv("TELEGRAM_TOKEN", "")
    TELEGRAM_USER_ID: int = int(os.getenv("TELEGRAM_USER_ID", "0"))
    _telegram_dirs_raw: str = os.getenv(
        "TELEGRAM_PROJECTS_DIRS",
        f"{Path.home() / 'projects'},{Path.home() / 'bots'}",
    )
    TELEGRAM_PROJECTS_DIRS: ClassVar[list[Path]] = [
        Path(p.strip()) for p in _telegram_dirs_raw.split(",") if p.strip()
    ]
    # Таймаут ожидания подтверждения опасной команды, секунды
    APPROVAL_TIMEOUT: float = float(os.getenv("APPROVAL_TIMEOUT", "900"))
    
    # Путь сессий
    SESSIONS_DIR: Path = BASE_DIR / "sessions"

    @classmethod
    def validate(cls) -> bool:
        errors: list[str] = []
        if not cls.ROUTERAI_API_KEY:
            errors.append("ROUTERAI_API_KEY не установлен")

        if not cls.CURATED_MODEL_IDS:
            errors.append(
                "CURATED_MODEL_IDS пуст — задайте список моделей через .env "
                "(через запятую)"
            )
        else:
            seen: set[str] = set()
            dupes: list[str] = []
            for mid in cls.CURATED_MODEL_IDS:
                if mid in seen and mid not in dupes:
                    dupes.append(mid)
                seen.add(mid)
            if dupes:
                errors.append(
                    "CURATED_MODEL_IDS содержит дубли: "
                    + ", ".join(repr(d) for d in dupes)
                )
            if cls.DEFAULT_MODEL_ID not in cls.CURATED_MODEL_IDS:
                errors.append(
                    f"DEFAULT_MODEL_ID={cls.DEFAULT_MODEL_ID!r} не входит в "
                    f"CURATED_MODEL_IDS={cls.CURATED_MODEL_IDS!r}"
                )

        if cls.MODELS_CACHE_TTL < 1:
            errors.append(
                f"MODELS_CACHE_TTL={cls.MODELS_CACHE_TTL} должен быть >= 1 (часы)"
            )
        if cls.MODELS_API_TIMEOUT <= 0:
            errors.append(
                f"MODELS_API_TIMEOUT={cls.MODELS_API_TIMEOUT} должен быть > 0 (секунды)"
            )

        if cls.WEB_SEARCH_ENGINE not in ("native", "exa"):
            errors.append(
                f"WEB_SEARCH_ENGINE={cls.WEB_SEARCH_ENGINE!r} должен быть "
                f"\"native\" или \"exa\""
            )
        if cls.WEB_SEARCH_MAX_RESULTS < 1:
            errors.append(
                f"WEB_SEARCH_MAX_RESULTS={cls.WEB_SEARCH_MAX_RESULTS} должен быть >= 1"
            )

        if cls.SERVICE_TIER not in ("default", "flex", "priority"):
            errors.append(
                f"SERVICE_TIER={cls.SERVICE_TIER!r} должен быть "
                f"\"default\", \"flex\" или \"priority\""
            )

        if cls.MAX_HISTORY_TOKENS < 1000:
            errors.append(
                f"MAX_HISTORY_TOKENS={cls.MAX_HISTORY_TOKENS} должен быть >= 1000"
            )
        if not cls.SUMMARY_MODEL:
            errors.append("SUMMARY_MODEL пуст")

        if errors:
            raise ValueError("\n".join(f"❌ {e}" for e in errors))
        return True


config = Config()
