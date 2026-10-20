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


def _recommended_from_fit(
    model_ids: list[str],
    fit: dict[str, list[str]],
    default: str,
) -> dict[str, str]:
    """Вывести RECOMMENDED_MODEL_BY_MODE из MODEL_IDS + MODEL_MODE_FIT.

    Первая в MODEL_IDS (порядок = приоритет), для которой mode ∈ FIT.
    Fallback — ``default`` (DEFAULT_MODEL_ID).

    Module-level, а не comprehension в теле класса: в class-body
    генератор не видит class-переменные (Python scoping) —
    NameError: name 'MODEL_IDS' is not defined.
    """
    return {
        mode: next(
            (mid for mid in model_ids if mode in fit.get(mid, [])),
            default,
        )
        for mode in ("act", "plan", "chat", "audit")
    }


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
    # Fallback для RECOMMENDED_MODEL_BY_MODE (см. telegram/handlers/project_utils.py).

    # Единый список моделей (B1): UI, роутер, дефолты.
    # Порядок = приоритет авто-выбора. Метаданные — в
    # `agent_coder.models._FALLBACK_CATALOG`.
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

    # Единый список моделей (B1): все модели, доступные в UI и роутере.
    # Порядок = приоритет авто-выбора (resolve берёт первую влезающую).
    # CURATED_MODEL_IDS оставлен как алиас на переходный период.
    MODEL_IDS: ClassVar[list[str]] = CURATED_MODEL_IDS

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

    # Модель по умолчанию (должна входить в MODEL_IDS)
    DEFAULT_MODEL_ID: str = os.getenv("DEFAULT_MODEL_ID", "deepseek/deepseek-v4-flash")

    # B1: соответствие «модель → режимы, для которых подходит».
    # Один источник истины для UI-пометок 🎯 и для
    # RECOMMENDED_MODEL_BY_MODE (ниже выводится автоматически).
    # Пустой список = «универсальная, режим не указан» (разрешено,
    # не блокирует добавление новых моделей).
    # Порядок MODEL_IDS = приоритет авто-выбора (не порядок этого dict).
    MODEL_MODE_FIT: ClassVar[dict[str, list[str]]] = {
        "deepseek/deepseek-v4-flash": ["act", "plan", "chat", "audit"],
        "deepseek/deepseek-v4.1-flash": ["act", "plan", "chat"],
        "qwen/qwen3-coder-next": ["act", "plan", "chat"],
        "moonshotai/kimi-k2.7-code": ["audit"],
        "z-ai/glm-5.3": ["audit", "act"],
        "xiaomi/mimo-v2.6-flash": ["act", "plan"],
        "deepseek/deepseek-r1": ["audit"],
        "openai/gpt-6.1-sol": ["act", "audit"],
        "anthropic/claude-sonnet-5.5": ["act", "audit"],
        "x-ai/grok-4.20": ["act", "chat"],
    }

    # Рекомендованная модель по режиму: ПЕРВАЯ в MODEL_IDS, для которой
    # mode ∈ MODEL_MODE_FIT[mid]. Выводится, а не задаётся руками — чтобы
    # UI-пометки 🎯 и авто-выбор (router.resolve) не разъезжались.
    # Fallback — DEFAULT_MODEL_ID, если ни одна модель не подошла (это
    # поймает validate() ниже, но код не упадёт на импорте).
    RECOMMENDED_MODEL_BY_MODE: ClassVar[dict[str, str]] = _recommended_from_fit(
        MODEL_IDS, MODEL_MODE_FIT, DEFAULT_MODEL_ID
    )

    # /verify: модель-верификатор по умолчанию. Не наследует
    # session.model/model_manual: цель verify — ДЕШЁВЫЙ фильтр ложных
    # findings. Для дорогой проверки — /verify <model_id> явно.
    # glm-5.3: 152₽/1M, 1048k контекст, есть в FIT["audit"].
    VERIFY_MODEL_ID: str = os.getenv("VERIFY_MODEL_ID", "z-ai/glm-5.3")
    # Отдельный бюджет на verify, не съедает MAX_COST_PER_TASK.
    MAX_COST_VERIFY: float = _env_float("MAX_COST_VERIFY", "50.0")

    # /patch (B9.2): модель-исполнитель по confirmed findings. Механическая
    # задача — дешёвая модель. /patch <model> — override. Бюджет отдельный,
    # не съедает MAX_COST_PER_TASK (как MAX_COST_VERIFY).
    # B9.2g: glm-5.3 вместо deepseek v4-flash. В /verify glm следовал
    # промпту (7 итераций, 29 вердиктов); deepseek не тянет архитектурные
    # findings (B9.2: 3 прогона, 17.4₽, 2 BLOCKER не починены, ручной фикс).
    # Fallback — kimi-k2.7-code, если glm не тянет. env PATCH_MODEL_ID
    # для override (в .env не задан — дефолт из этой строки).
    PATCH_MODEL_ID: str = os.getenv("PATCH_MODEL_ID", "z-ai/glm-5.3")
    MAX_COST_PATCH: float = _env_float("MAX_COST_PATCH", "50.0")
    # Потолок итераций /patch: обычно 5-15 на 10-15 findings. 30 — с запасом.
    MAX_PATCH_ITERATIONS: int = _env_int("MAX_PATCH_ITERATIONS", "60")
    # B9.2e: patch — механическая задача, reasoning="low" (medium даёт
    # 10-15K reasoning-токенов на сложных finding'ах без действия).
    PATCH_REASONING_EFFORT: str = os.getenv("PATCH_REASONING_EFFORT", "low")
    # Таймаут одного LLM-вызова в patch (сек). Без него при зависшем
    # провайдере цикл ждёт бесконечно.
    PATCH_CHAT_TIMEOUT: float = _env_float("PATCH_CHAT_TIMEOUT", "180.0")

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
    # B11.0: 60K при context=1M deepseek — 6% использования, compress
    # каждые ~7 итераций. 150K: 2.5x больше контекста, цена ~2.5x,
    # compress в 2.5x реже. env MAX_HISTORY_TOKENS для override.
    MAX_HISTORY_TOKENS: int = _env_int("MAX_HISTORY_TOKENS", "150000")

    # Дефолтная температура генерации (0.0–2.0). Для кода — 0.2.
    TEMPERATURE: float = _env_float("TEMPERATURE", "0.2")
    # Модель для суммаризации старых сообщений. Должна быть в MODEL_IDS
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

    # B9.4: дедуп findings по «файл:строка» в WorkStateHandle.update/render.
    # Схлопывает «services/pricing.py:13» и «pricing.py:13» в один
    # (оставляет более информативный: severity > длина). Default: 1 (вкл).
    WORK_STATE_DEDUP: bool = os.getenv("WORK_STATE_DEDUP", "1").strip().lower() not in (
        "0",
        "false",
        "no",
        "off",
    )

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

        if not cls.MODEL_IDS:
            errors.append(
                "MODEL_IDS пуст — задайте список моделей через .env (через запятую)"
            )
        else:
            seen: set[str] = set()
            dupes: list[str] = []
            for mid in cls.MODEL_IDS:
                if mid in seen and mid not in dupes:
                    dupes.append(mid)
                seen.add(mid)
            if dupes:
                errors.append(
                    "MODEL_IDS содержит дубли: " + ", ".join(repr(d) for d in dupes)
                )
            if cls.DEFAULT_MODEL_ID not in cls.MODEL_IDS:
                errors.append(
                    f"DEFAULT_MODEL_ID={cls.DEFAULT_MODEL_ID!r} не входит в "
                    f"MODEL_IDS={cls.MODEL_IDS!r}"
                )

            # Recommended-модели должны входить в CURATED (иначе дефолт
            # режима поставит модель, которой нет в UI/каталоге).
            for mode_name, mid in cls.RECOMMENDED_MODEL_BY_MODE.items():
                if mid not in seen:
                    errors.append(
                        f"RECOMMENDED_MODEL_BY_MODE[{mode_name!r}]={mid!r} "
                        f"не входит в MODEL_IDS"
                    )
            # VERIFY_MODEL_ID ∈ MODEL_IDS и подходит для audit.
            if cls.VERIFY_MODEL_ID not in seen:
                errors.append(
                    f"VERIFY_MODEL_ID={cls.VERIFY_MODEL_ID!r} не входит в MODEL_IDS"
                )
            elif "audit" not in cls.MODEL_MODE_FIT.get(cls.VERIFY_MODEL_ID, []):
                errors.append(
                    f"VERIFY_MODEL_ID={cls.VERIFY_MODEL_ID!r} не подходит "
                    f"для audit (нет в MODEL_MODE_FIT[VERIFY_MODEL_ID])"
                )

            # MODEL_MODE_FIT: ключи ∈ MODEL_IDS, режимы — известные.
            known_modes = {"act", "plan", "chat", "audit"}
            for mid, modes in cls.MODEL_MODE_FIT.items():
                if mid not in seen:
                    errors.append(
                        f"MODEL_MODE_FIT: модель {mid!r} не входит в MODEL_IDS"
                    )
                unknown_modes = [m for m in modes if m not in known_modes]
                if unknown_modes:
                    errors.append(
                        f"MODEL_MODE_FIT[{mid!r}]: неизвестные режимы "
                        + ", ".join(repr(m) for m in unknown_modes)
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
