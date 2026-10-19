"""Конфигурация agent-coder."""

import os
from pathlib import Path
from typing import ClassVar

from dotenv import load_dotenv

# BASE_DIR — до load_dotenv, чтобы .env искался относительно кода,
# а не текущей рабочей директории процесса.
BASE_DIR = Path(__file__).resolve().parent.parent

# override=False: не затирать уже установленные переменные окружения
# (приоритет у того, что реально экспортировано в shell/systemd).
load_dotenv(BASE_DIR / ".env", override=False)


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
        raise ValueError(f"{name}={raw!r} должен быть числом (env {name})") from exc


class Config:
    BASE_DIR: Path = BASE_DIR

    # RouterAI
    ROUTERAI_API_KEY: str = os.getenv("ROUTERAI_API_KEY", "")
    ROUTERAI_BASE_URL: str = os.getenv(
        "ROUTERAI_BASE_URL", "https://routerai.ru/api/v1"
    )
    ROUTERAI_TIMEOUT: float = float(os.getenv("ROUTERAI_TIMEOUT", "300.0"))

    # Модели
    MODEL_MAIN: str = os.getenv("MODEL_MAIN", "deepseek/deepseek-v4-flash")
    # Зарезервировано на будущее (не используется в коде)

    # Список curated-моделей для меню выбора (через запятую). Порядок
    # сохраняется в UI. Метаданные моделей — в `agent_coder.models._FALLBACK_CATALOG`.
    _curated_raw: str = os.getenv("CURATED_MODEL_IDS", "")
    CURATED_MODEL_IDS: ClassVar[list[str]] = [
        mid.strip() for mid in _curated_raw.split(",") if mid.strip()
    ] or [
        "deepseek/deepseek-v4-flash",
        "deepseek/deepseek-v4.1-flash",
        "qwen/qwen3-coder-next",
        "moonshotai/kimi-k2.7-code",
        "z-ai/glm-5.3",
        "xiaomi/mimo-v2.6-flash",
        "deepseek/deepseek-r1",
        "openai/gpt-6.1-sol",
        "anthropic/claude-sonnet-5.5",
        "x-ai/grok-4.20",
    ]

    # Группа coding: дефолт для ACT/PLAN/CHAT + fallback-цепочка при
    # 429/timeout (порядок = приоритет). Все id должны входить в
    # CURATED_MODEL_IDS.
    _coding_raw: str = os.getenv("CODING_MODEL_IDS", "")
    CODING_MODEL_IDS: ClassVar[list[str]] = [
        mid.strip() for mid in _coding_raw.split(",") if mid.strip()
    ] or [
        "deepseek/deepseek-v4-flash",
        "deepseek/deepseek-v4.1-flash",
        "qwen/qwen3-coder-next",
    ]

    # Группа audit: дефолт для режима AUDIT + fallback-цепочка (порядок =
    # приоритет). resolve берёт ПЕРВУЮ влезающую модель группы. Порядок —
    # «сильнейшая для аудита первой» (270901b), не по размеру контекста:
    # kimi (262k) перед glm (1048k). Для крупных задач router пропускает
    # kimi и берёт glm (см. llm/router.py: resolve / fits_context).
    _audit_raw: str = os.getenv("AUDIT_MODEL_IDS", "")
    AUDIT_MODEL_IDS: ClassVar[list[str]] = [
        mid.strip() for mid in _audit_raw.split(",") if mid.strip()
    ] or [
        "moonshotai/kimi-k2.7-code",
        "z-ai/glm-5.3",
        "xiaomi/mimo-v2.6-flash",
    ]

    # Доля context_limit модели, которую разрешено занимать входным токенам.
    # Если задача не влезает — модель пропускается (fallback берёт следующую).
    # 0.8 — запас на ответ и на неточность оценки токенов.
    CONTEXT_FILL_RATIO: float = _env_float("CONTEXT_FILL_RATIO", "0.8")

    # Роутер моделей: подставлять дефолт группы по режиму сессии, только
    # если пользователь не выбрал модель вручную (/model). Fallback-цепочки
    # при 429/timeout работают независимо от этого флага.
    MODEL_ROUTER_ENABLED: bool = os.getenv(
        "MODEL_ROUTER_ENABLED", "1"
    ).strip().lower() in ("1", "true", "yes", "on")

    # Модель по умолчанию (должна входить в CURATED_MODEL_IDS)
    DEFAULT_MODEL_ID: str = os.getenv("DEFAULT_MODEL_ID", "deepseek/deepseek-v4-flash")

    # Рекомендованная модель по режиму: ставится при создании сессии и при
    # смене режима (если пользователь не выбрал модель вручную). Это
    # РЕКОМЕНДАЦИЯ, не ограничение: пользователь может выбрать любую из
    # CURATED_MODEL_IDS, роутер не перекрывает ручной выбор.
    # Порядок ключей = Mode.value.
    #
    # Аудит по умолчанию — deepseek-v4-flash (дёшево): реальный аудит
    # модуля на kimi стоил 13.57₽ за 10 итераций (сессия a1b0a44b).
    # Для сложного аудита — kimi вручную через /model (в карточке модели
    # помечен как «рекомендуем для аудита»).
    RECOMMENDED_MODEL_BY_MODE: ClassVar[dict[str, str]] = {
        "act": "deepseek/deepseek-v4-flash",
        "plan": "deepseek/deepseek-v4-flash",
        "chat": "deepseek/deepseek-v4-flash",
        "audit": "deepseek/deepseek-v4-flash",
    }

    # Модели, которые рекомендуется выбирать вручную для конкретного
    # режима (пометка в UI-карточке, не авто-выбор). Пусто — нет пометок.
    RECOMMENDED_MANUAL_BY_MODE: ClassVar[dict[str, str]] = {
        "audit": "moonshotai/kimi-k2.7-code",
    }

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
    MAX_HISTORY_TOKENS: int = _env_int("MAX_HISTORY_TOKENS", "60000")

    # Дефолтная температура генерации (0.0–2.0). Для кода — 0.2.
    TEMPERATURE: float = _env_float("TEMPERATURE", "0.2")
    # Модель для суммаризации старых сообщений. Должна быть в CURATED_MODEL_IDS
    # (или в _FALLBACK_CATALOG). Дешёвая — для вспомогательной задачи.
    SUMMARY_MODEL: str = os.getenv("SUMMARY_MODEL", "moonshotai/kimi-k2.7-code")

    # Retention медиа-вложений сессий: файлы старше N дней удаляются
    # фоновой задачей бота (раз в сутки + один раз при старте).
    MEDIA_RETENTION_DAYS: int = _env_int("MEDIA_RETENTION_DAYS", "30")

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
    # Каталог с шаблонами оформления проекта (pyproject-snippet.toml,
    # ci-template.yml, requirements-dev-base.in, .gitignore-base).
    # Используется check_project_standard и подсказками агенту в промте.
    # Путь намеренно абсолютный: агент работает из разных workspace'ов.
    SHARED_DEV_DIR: str = os.getenv(
        "SHARED_DEV_DIR",
        "/home/botuser/bots/_shared-dev/project-template/",
    )
    MAX_ITERATIONS: int = int(os.getenv("MAX_ITERATIONS", "200"))
    MAX_ITERATIONS_PLAN: int = int(os.getenv("MAX_ITERATIONS_PLAN", "100"))
    MAX_ITERATIONS_ACT: int = int(os.getenv("MAX_ITERATIONS_ACT", "200"))
    MAX_ITERATIONS_AUDIT: int = int(os.getenv("MAX_ITERATIONS_AUDIT", "200"))

    # Авто-продолжение: сколько раз бот сам шлёт "продолжи", если агент
    # остановился на лимите, но задача не завершена.
    MAX_AUTO_CONTINUE: int = int(os.getenv("MAX_AUTO_CONTINUE", "3"))

    # Watchdog: если столько tool-результатов подряд начинаются с "Ошибка" —
    # останавливаемся, чтобы не зациклиться и не сжечь баланс.
    MAX_CONSECUTIVE_ERRORS: int = int(os.getenv("MAX_CONSECUTIVE_ERRORS", "5"))

    # Бюджет: жёсткие потолки трат. Ловят любое сжигание токенов
    # (loop на read_file, длинный аудит с автопродолжениями) — не только 429.
    MAX_COST_PER_TASK: float = _env_float("MAX_COST_PER_TASK", "200.0")
    MAX_COST_PER_SESSION: float = _env_float("MAX_COST_PER_SESSION", "300.0")

    # Watchdog прогресса: сколько итераций подряд без изменения work_state
    # и без успешной мутации (write_file/edit_file/git_commit) считать
    # «залип». 10 — эмпирически: read_file-loop упирался за 9 перечитываний.
    MAX_ITERATIONS_NO_PROGRESS: int = int(os.getenv("MAX_ITERATIONS_NO_PROGRESS", "10"))

    # Логи
    # Зарезервировано на будущее (не используется в коде)
    LOG_LEVEL: str = os.getenv("LOG_LEVEL", "INFO")

    # Telegram
    TELEGRAM_TOKEN: str = os.getenv("TELEGRAM_TOKEN", "")

    # GitHub integration (используется для /ghnew, /clone)
    GITHUB_TOKEN: str = os.getenv("GITHUB_TOKEN", "")
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
    SESSIONS_DIR: Path = Path(os.getenv("SESSIONS_DIR", str(BASE_DIR / "sessions")))

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

            # Группы coding/audit: непустые, без дублей, подмножества curated.
            for group_name, group_ids in (
                ("CODING_MODEL_IDS", cls.CODING_MODEL_IDS),
                ("AUDIT_MODEL_IDS", cls.AUDIT_MODEL_IDS),
            ):
                if not group_ids:
                    errors.append(f"{group_name} пуст — задайте список через .env")
                    continue
                g_seen: set[str] = set()
                g_dupes: list[str] = []
                for mid in group_ids:
                    if mid in g_seen and mid not in g_dupes:
                        g_dupes.append(mid)
                    g_seen.add(mid)
                if g_dupes:
                    errors.append(
                        f"{group_name} содержит дубли: "
                        + ", ".join(repr(d) for d in g_dupes)
                    )
                unknown = [mid for mid in group_ids if mid not in seen]
                if unknown:
                    errors.append(
                        f"{group_name} содержит модели вне CURATED_MODEL_IDS: "
                        + ", ".join(repr(u) for u in unknown)
                    )
            # Recommended-модели должны входить в CURATED (иначе дефолт
            # режима поставит модель, которой нет в UI/каталоге).
            for mode_name, mid in cls.RECOMMENDED_MODEL_BY_MODE.items():
                if mid not in seen:
                    errors.append(
                        f"RECOMMENDED_MODEL_BY_MODE[{mode_name!r}]={mid!r} "
                        f"не входит в CURATED_MODEL_IDS"
                    )

            overlap = [
                mid for mid in cls.CODING_MODEL_IDS if mid in cls.AUDIT_MODEL_IDS
            ]
            if overlap:
                errors.append(
                    "CODING_MODEL_IDS и AUDIT_MODEL_IDS пересекаются: "
                    + ", ".join(repr(m) for m in overlap)
                )

        # Доля контекста под входные токены: разумные границы.
        if not (0.1 <= cls.CONTEXT_FILL_RATIO <= 1.0):
            errors.append(
                f"CONTEXT_FILL_RATIO={cls.CONTEXT_FILL_RATIO} должен быть "
                "в диапазоне 0.1–1.0"
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
                f'"native" или "exa"'
            )
        if cls.WEB_SEARCH_MAX_RESULTS < 1:
            errors.append(
                f"WEB_SEARCH_MAX_RESULTS={cls.WEB_SEARCH_MAX_RESULTS} должен быть >= 1"
            )

        if cls.SERVICE_TIER not in ("default", "flex", "priority"):
            errors.append(
                f"SERVICE_TIER={cls.SERVICE_TIER!r} должен быть "
                f'"default", "flex" или "priority"'
            )

        if cls.MAX_HISTORY_TOKENS < 1000:
            errors.append(
                f"MAX_HISTORY_TOKENS={cls.MAX_HISTORY_TOKENS} должен быть >= 1000"
            )
        if not cls.SUMMARY_MODEL:
            errors.append("SUMMARY_MODEL пуст")

        if cls.MEDIA_RETENTION_DAYS < 1:
            errors.append(
                f"MEDIA_RETENTION_DAYS={cls.MEDIA_RETENTION_DAYS} должен быть >= 1"
            )

        if not (0.0 <= cls.TEMPERATURE <= 2.0):
            errors.append(
                f"TEMPERATURE={cls.TEMPERATURE} должен быть в диапазоне 0.0–2.0"
            )

        if errors:
            raise ValueError("\n".join(f"❌ {e}" for e in errors))
        return True


config = Config()
