# agent-coder

[![CI](https://github.com/Kashintsev-cmyk/agent-coder/actions/workflows/ci.yml/badge.svg)](https://github.com/Kashintsev-cmyk/agent-coder/actions/workflows/ci.yml)
![Python](https://img.shields.io/badge/python-3.10%20%7C%203.11%20%7C%203.12-blue)
![License](https://img.shields.io/badge/license-MIT-green)

Автономный coding-агент для сервера. CLI и Telegram-бот на базе RouterAI:
читает, правит и запускает код прямо на машине — без SSH-туннелей, без
remote-helper'ов, с точным учётом стоимости каждой генерации.

## Возможности

**Агент**
- Три режима работы: `plan` (только чтение), `act` (полный доступ),
  `audit` (строгий аудитор)
- 10 инструментов: чтение/запись/правки файлов, поиск (`ripgrep`),
  shell с whitelist, git
- Подтверждения на опасные операции; безопасные read-only команды — без вопросов
- Точная стоимость от RouterAI (`usage.cost`), без оценок по токенам

**Модели**
- Каталог из `/api/v1/models` RouterAI, кэшируется на диск (TTL 24 ч)
- Список моделей задаётся в `.env` (`CURATED_MODEL_IDS`) — без правок кода
- Адаптивное меню: кнопки (reasoning / web search / tier / verbosity) только
  для возможностей, которые модель реально поддерживает
- Карточка модели с описанием, контекстом и ценами

**LLM-фичи**
- Встроенный web search RouterAI (`plugins=[{id:web}]`)
- Service tier: `default` / `flex` (−50% цены) / `priority` (быстрее)
- Verbosity, reasoning — с per-session настройкой; sampling (`temperature`/`top_p`/`top_k`) — per-model, см. «Sampling-параметры (per-model)» в разделе «Модели»
- Сжатие истории: старые сообщения резюмируются дешёвой моделью
- Приём изображений (vision-модели): фото передаётся как `image_url`

**Проекты и GitHub**
- `/new <name>` — создать проект с нуля (`git init` + `README.md` +
  `.gitignore` + initial commit)
- `/clone <url>` — клонировать любой GitHub-репозиторий в `~/bots/<repo>`
- `/ghnew <name>` — создать репо на GitHub (публичный/приватный) +
  локально + push
- После `/new` — опционально предложить создание GitHub-репо
- SSH-ключ (для clone/push) + `GITHUB_TOKEN` (для создания через API)

**Безопасность**
- Whitelist read-only команд; pipe без `awk`/`sed`/`env`/`printenv`
- Маскирование секретов (`ROUTERAI_API_KEY`, `TELEGRAM_TOKEN`, `sk-*`,
  `Bearer *`) в логах и выводе команд
- Path traversal защищён в `search`, `read_file`, `write_file`,
  `list_files`, `/new`, `/clone`
- systemd hardening: `NoNewPrivileges`, `ProtectSystem=strict`,
  `ReadWritePaths` для нужных директорий

**Инфраструктура**
- Сессии на диске + sidecar-индекс метаданных (быстрый `/sessions`)
- Медиа-retention: файлы старше 30 дней удаляются фоновой задачей
- Retry с jitter и уважением `Retry-After`
- CI: pytest + ruff + mypy на Python 3.10–3.12

## Быстрый старт

### Требования

| Что | Зачем | Обязательно |
|---|---|---|
| Python **≥ 3.10** | `requires-python` в `pyproject.toml` | да |
| `ROUTERAI_API_KEY` | доступ к моделям через [routerai.ru](https://routerai.ru) | да |
| `ripgrep` (`rg`) | инструмент `search` | для поиска |
| `git` + SSH-ключ на GitHub | инструменты `git_*`, `/clone`, `/ghnew` | для GitHub |
| `GITHUB_TOKEN` | создание репо на GitHub через API | для `/ghnew` |

### Установка

    git clone git@github.com:Kashintsev-cmyk/agent-coder.git
    cd agent-coder
    python3 -m venv .venv
    source .venv/bin/activate
    pip install -e .
    cp .env.example .env
    # отредактируй .env — вставь ROUTERAI_API_KEY

Устанавливаются две команды:
- `agent-coder` — CLI
- `agent-coder-bot` — Telegram-бот (нужен `TELEGRAM_TOKEN`)

### Первый запуск (CLI)

    agent-coder --cwd /path/to/project "проанализируй структуру проекта"

Без задачи — откроется REPL.

### Первый запуск (Telegram)

    # в .env: TELEGRAM_TOKEN, TELEGRAM_USER_ID
    agent-coder-bot

Открой бота → `/start` → `/projects` → выбери проект → отправь задачу.

## Использование

### CLI

    # Одна задача — выполнить и выйти
    agent-coder --cwd /path/to/project "создай README.md"

    # REPL (без аргумента-задачи)
    agent-coder --cwd /path/to/project

    # Plan-режим: только чтение
    agent-coder --plan "предложи рефакторинг"

    # Без подтверждений (осторожно)
    agent-coder --yolo "прогони тесты и почини падения"

    # Возобновить сессию / продолжить последнюю
    agent-coder --resume 7db6a452
    agent-coder --continue --cwd /path/to/project

    # Выбрать модель и включить debug-логи
    agent-coder --model anthropic/claude-sonnet-5.5 -v "отрефактори парсер"

### CLI-флаги

| Флаг | По умолчанию | Описание |
|---|---|---|
| `task` (позиционный) | — | Задача; без неё запускается REPL |
| `--cwd DIR` | `DEFAULT_WORKSPACE` (`~/projects`) | Workspace |
| `--model ID` | `MODEL_MAIN` | Модель RouterAI |
| `--plan` | выкл. | Plan-режим |
| `--yolo` | выкл. | Без подтверждений |
| `--resume ID` | — | Возобновить сессию |
| `--continue` | выкл. | Продолжить последнюю для workspace |
| `--max-iterations N` | из `.env` | Лимит итераций (переопределяет `MAX_ITERATIONS_*`) |
| `--verbose`, `-v` | выкл. | DEBUG-логи |
| `--version` | — | Версия |

### Slash-команды (REPL)

| Команда | Действие |
|---|---|
| `/plan`, `/act`, `/audit` | Сменить режим |
| `/cost` | Стоимость и число запросов |
| `/session` | Информация о текущей сессии |
| `/sessions` | Список сессий (до 20, свежие сверху) |
| `/switch <id>` | Переключиться на сессию |
| `/title <текст>` | Заголовок сессии |
| `/history` | Последние 20 сообщений |
| `/clear` | Очистить историю (стоимость сохраняется) |
| `/model` | Выбрать модель |
| `/reasoning` | Уровень reasoning |
| `/verify [model]` | Проверить findings аудита на модели-фильтре |
| `/patch [model]` | Починить confirmed findings (дефолт deepseek) |
| `/help`, `/exit`, `/quit`, `/q` | Справка / выход |

## Режимы

| Режим | Что разрешено | Лимит итераций |
|---|---|---|
| `plan` | read-only инструменты + whitelist-команды | `MAX_ITERATIONS_PLAN` (30) |
| `act` | всё: запись, правки, любые команды, git-коммит | `MAX_ITERATIONS_ACT` (50) |
| `audit` | как `plan`, но с промптом строгого аудитора; в Telegram — Markdown-отчёт | `MAX_ITERATIONS_AUDIT` (100) |

По умолчанию — `act`. В `plan`/`audit` попытки изменить файлы
отбиваются на уровне реестра инструментов.

**История сообщений при смене режима:**
- Любой переход между `plan`/`act`/`audit` — история **очищается**

### Правила workspace (AGENTS.md)

Если в workspace есть файл `AGENTS.md`, его содержимое добавляется
в конец system prompt для режимов `plan` и `act` — отдельной
секцией `--- AGENTS.md ---` … `--- end AGENTS.md ---`. Файла нет —
поведение не меняется. `audit` его не подхватывает (структурный
промт аудитора).

- Лимит размера — 8192 байта; хвост обрезается с маркером
  `[AGENTS.md truncated]`. Переопределяется
  через `AGENT_CODER_AGENTS_MD_MAX_BYTES`.
- Содержимое кэшируется по `mtime` и размеру: если файл не менялся,
  повторные запросы его не перечитывают.
- Ошибки чтения (нет прав, битый UTF-8) не роняют агент — system prompt
  остаётся базовым.

### Стандарт оформления проекта (builtin)

Агент знает стандарт Python-проектов в `/home/botuser/bots/`. Текст —
`agent_coder/rules/project_standard.md` (~2 KB); попадает в system prompt
для `plan` и `act`.

Приоритет: `workspace/AGENTS.md` > builtin. Если в workspace есть
`AGENTS.md` (даже пустой) — вставляется только он, стандарт не
подмешивается. Иначе — вставляется стандарт.

Проверка соответствия — tool `check_project_standard` (read-only):
обязательные файлы, секции `pyproject.toml` (`testpaths`, `ruff`
`line-length=88`, `select ⊇ {E, F, I}`, `coverage source != "app"`),
`tests/`, CI при наличии git remote. Возвращает JSON
`{score, majors, minors, issues}`. Доступен в `plan`, `audit`, `act`.

Шаблоны для копирования — в `SHARED_DEV_DIR` (default
`/home/botuser/bots/_shared-dev/project-template/`):
`pyproject-snippet.toml`, `ci-template.yml`, `requirements-dev-base.in`,
`.gitignore-base`, `AGENTS.md`.

## Инструменты

| Инструмент | Назначение | Подтверждение |
|---|---|---|
| `read_file` | Чтение файла с нумерацией строк (`offset`/`limit`, до 2000 строк) | нет |
| `list_files` | Рекурсивный список (glob-шаблон, до 500 записей) | нет |
| `search` | Поиск через `ripgrep` (до 200 совпадений, таймаут 60 с) | нет |
| `write_file` | Создать / перезаписать файл | нет |
| `edit_file` | Точечная замена `old_string` → `new_string` | нет |
| `run_command` | Shell в workspace. Whitelist read-only + pipe — без вопросов; остальное (`rm`, `kill`, `sudo`, `awk`, `sed`, `env`, `;`) — через approval. Таймаут 300 с | по команде |
| `git_status` | `git status --porcelain --branch` | нет |
| `git_diff` | Diff изменений | нет |
| `git_log` | Последние N коммитов (1–50) | нет |
| `git_commit` | Коммит того, что в индексе | да |

`read_file`, `write_file`, `edit_file`, `list_files`, `search` — только внутри
workspace (path traversal защищён).

## Модели

Список моделей для UI задаётся через `.env` → `CURATED_MODEL_IDS`.
Метаданные (цены, контекст, capabilities) тянутся из `/api/v1/models`
RouterAI и кэшируются на диск.

Дефолтный набор:

| Модель | ID | Роль | Контекст | ₽ in / out (за 1M) | Бейджи |
|---|---|---|---|---|---|
| **DeepSeek V4.1 Flash** *(default)* | `deepseek/deepseek-v4.1-flash` | default, patch, audit fallback | 1 048 576 | 3.82 / 15.28 | 👁⚙🔧 |
| Claude Haiku 5.5 | `anthropic/claude-haiku-5.5` | verify, summary, act, audit | 1 000 000 | 10.53 / 52.63 | 👁⚙🔧📏 |
| Qwen 3.7 Flash | `qwen/qwen3.7-flash` | plan / act fallback | 1 000 000 | 3.16 / 13.68 | ⚙🔧 |
| GLM 5.3 Flash | `z-ai/glm-5.3-flash` | plan / act fallback | 1 048 576 | 3.62 / 9.80 | 👁⚙🔧 |
| Mistral Nemo | `mistralai/mistral-nemo` | ручной `/model` (дешёвый выход) | 131 072 | 1.74 / 2.27 | — |
| GPT-6 Luna | `openai/gpt-6-luna` | plan / act fallback (drop-in Haiku) | 1 050 000 | 10.53 / 52.63 | 🔧 |

**Бейджи:** 👁 vision · 🌐 встроенный web search · 🧠 reasoning · ⚙ reasoning
с градацией · 🔧 tools · 📏 verbosity.

Цены — снимок 2026-11-03 (синхронизированы с `/api/v1/models`). Живые цены всегда подтягиваются из API и кэшируются; `tests/test_fallback_prices.py` следит, что у моделей из `MODEL_IDS` в fallback ненулевые цены и context.

Модели из `MODEL_IDS` доступны в UI (`/model`). Модели вне `MODEL_IDS`
(например, `google/gemini-4-argon`, `anthropic/claude-sonnet-5.5`) остаются
в `_FALLBACK_CATALOG` и доступны через `/model` вручную — удобно для
разовых задач (security second-pass и т.п.).

### Sampling-параметры (per-model)

`temperature`, `top_p`, `top_k` для моделей из `MODEL_IDS` задаются в
`agent_coder/llm/profiles.py` (`MODEL_PROFILES`), а **не** через `TEMPERATURE`
в `.env`. Три контракта:

1. **Модель в реестре, профиль не пустой** — sampling-параметры берутся из
   профиля; явный `temperature`, переданный вызывающим кодом, **игнорируется**.
   Примеры: Qwen 0.6 / 0.95 / 20, GLM 1.0 / 0.95, Nemo 0.35.
2. **Модель в реестре, профиль `{}`** — sampling **не отправляется вообще**.
   Для закрытых reasoning-моделей (Claude 4.7+, GPT-5/6), которые отклоняют
   любое не-дефолтное значение (400).
3. **Модель вне реестра** — legacy: явный `temperature` уходит как есть,
   без профиля (`_FALLBACK_CATALOG`, ручной `/model`).

`reasoning_effort` — пользовательский выбор (UI 💭 Reasoning,
`session.reasoning_effort`) **перебивает** baseline из профиля
(DeepSeek `high`, GLM `max`). Если явно `none`/`""` — baseline применяется.

Контракт покрыт `tests/test_model_profiles.py` (19 тестов).

### Роутер моделей

Рекомендованная модель по режиму — `RECOMMENDED_MODEL_BY_MODE` (выводится
автоматически из `MODEL_IDS` + `MODEL_MODE_FIT`, с override для `act`/`audit`):

| Режим | Рекомендованная модель | Почему |
|---|---|---|
| `plan` | `deepseek/deepseek-v4.1-flash` | дешёвый универсал, 1M ctx |
| `act` | `anthropic/claude-haiku-5.5` | benchmark 6×3 (2026-10-24): best bootstrap signal |
| `audit` | `anthropic/claude-haiku-5.5` | weather-bot benchmark (2026-11-03): находит BLOCKER, ×2.6 дешевле v4.1 |

Fallback-цепочка при 429/timeout — модели из `MODEL_IDS`, у которых
`mode ∈ MODEL_MODE_FIT[mid]` (порядок в `MODEL_IDS` = приоритет):
`v4.1-flash` → `qwen3.7-flash` / `glm-5.3-flash` / `gpt-6-luna`.

Логика (вариант «б» — ручной выбор приоритетен):

- Если модель выбрана вручную (`/model` или меню сессии) — роутер её **не
  трогает**; работает только fallback-цепочка внутри группы этой модели.
- Если модель не выбрана и `MODEL_ROUTER_ENABLED=1` — при старте задачи
  подставляется **первая влезающая по контексту** модель группы режима.
- **Проверка контекста:** перед выбором роутер сверяет оценку входных токенов
  с `context_limit * CONTEXT_FILL_RATIO` (по умолчанию `0.8`). Модели, в
  которые задача не влезает, пропускаются — иначе большой diff обрежется или
  провайдер вернёт ошибку. Модели с небольшим ctx (например, `mistralai/mistral-nemo`,
  131k) получают только небольшие задачи, если выбраны вручную.
- **Fallback при 429 / timeout:** клиент сначала сам делает retry с backoff,
  затем роутер переключается на следующую **влезающую** модель цепочки и
  повторяет итерацию. Если цепочка исчерпана — задача завершается с ошибкой.

## Переменные окружения

Все — в `.env` (см. `.env.example`). Обязательна только `ROUTERAI_API_KEY`.

**RouterAI / LLM**

| Переменная | По умолчанию | Описание |
|---|---|---|
| `ROUTERAI_API_KEY` | — (**обязательна**) | API-ключ |
| `ROUTERAI_BASE_URL` | `https://routerai.ru/api/v1` | Базовый URL |
| `ROUTERAI_TIMEOUT` | `300.0` | Таймаут LLM, с |
| `MAX_RETRIES` | `3` | Число попыток при ошибках |
| `RETRY_BACKOFF` | `2.0` | База экспоненциального backoff |
| `TEMPERATURE` | `0.2` | Для моделей **вне** `MODEL_PROFILES` (ручной `/model` из `_FALLBACK_CATALOG`). Для `MODEL_IDS` — per-model, см. `agent_coder/llm/profiles.py` |
| `SERVICE_TIER` | `default` | `default` / `flex` / `priority` |
| `WEB_SEARCH_ENGINE` | `native` | `native` / `exa` |
| `WEB_SEARCH_MAX_RESULTS` | `5` | Сколько результатов запрашивать |

**Модели**

| Переменная | По умолчанию | Описание |
|---|---|---|
| `MODEL_MAIN` | `deepseek/deepseek-v4.1-flash` | Модель для новых сессий |
| `DEFAULT_MODEL_ID` | `deepseek/deepseek-v4.1-flash` | Должна входить в `MODEL_IDS` |
| `CURATED_MODEL_IDS` | пусто (= 6 моделей из `config.py`) | Список для UI; пусто — использовать дефолт `MODEL_IDS` |
| `MODEL_ROUTER_ENABLED` | `1` | Подставлять recommended-модель по режиму, если не выбрана вручную |
| `CONTEXT_FILL_RATIO` | `0.8` | Доля `context_limit` под входные токены (0.1–1.0) |

**Каталог моделей**

| Переменная | По умолчанию | Описание |
|---|---|---|
| `MODELS_CACHE_TTL` | `24` | TTL дискового кэша, ч |
| `MODELS_CACHE_DIR` | `~/.cache/agent-coder` | Куда писать кэш |
| `MODELS_API_TIMEOUT` | `5.0` | Таймаут запроса к `/models` |

**Сжатие истории и медиа**

| Переменная | По умолчанию | Описание |
|---|---|---|
| `MAX_HISTORY_TOKENS` | `90000` | Порог = `min(model.context * 0.6, MAX_HISTORY_TOKENS)` |
| `SUMMARY_MODEL` | `anthropic/claude-haiku-5.5` | Модель для резюме старых сообщений |
| `VERIFY_MODEL_ID` | `anthropic/claude-haiku-5.5` | Модель для `/verify` |
| `PATCH_MODEL_ID` | `deepseek/deepseek-v4.1-flash` | Модель для `/patch` |
| `MAX_COST_VERIFY` | `50.0` | Отдельный бюджет verify, ₽ |
| `MEDIA_RETENTION_DAYS` | `30` | Retention медиа-вложений, дней |

**Агент и окружение**

| Переменная | По умолчанию | Описание |
|---|---|---|
| `DEFAULT_WORKSPACE` | `~/projects` | Workspace по умолчанию |
| `AGENT_CODER_AGENTS_MD_MAX_BYTES` | `8192` | Лимит размера `AGENTS.md`, байт |
| `SHARED_DEV_DIR` | `/home/botuser/bots/_shared-dev/project-template/` | Каталог шаблонов оформления проекта |
| `MAX_ITERATIONS` | `50` | Fallback-лимит |
| `MAX_ITERATIONS_PLAN` | `30` | Plan |
| `MAX_ITERATIONS_ACT` | `50` | Act |
| `MAX_ITERATIONS_AUDIT` | `100` | Audit |
| `LOG_LEVEL` | `INFO` | Зарезервировано |

**Telegram**

| Переменная | По умолчанию | Описание |
|---|---|---|
| `TELEGRAM_TOKEN` | — | Токен бота |
| `TELEGRAM_USER_ID` | `0` | Единственный разрешённый пользователь |
| `TELEGRAM_PROJECTS_DIRS` | `~/projects,~/bots` | Папки для `/projects` |
| `APPROVAL_TIMEOUT` | `900` | Таймаут approval, с |

**GitHub**

| Переменная | По умолчанию | Описание |
|---|---|---|
| `GITHUB_TOKEN` | — | Classic PAT со scope `repo` (для `/ghnew`) |

## Telegram-бот

    agent-coder-bot

**Полная справка:** [`docs/TELEGRAM.md`](docs/TELEGRAM.md) — команды,
режимы, approval, меню, env, где данные.

**Команды:** `/start`, `/help`, `/projects`, `/new <name>`, `/clone <url>`,
`/ghnew <name>`, `/migrate <name>`, `/sessions`, `/current`, `/history`,
`/delete [id]`, `/model`, `/reasoning`, `/mode plan|act|audit`,
`/cost`, `/balance`, `/server`, `/audit`, `/cancel`, `/yolo [on|off]`.

> `/new <name>` создаёт проект **по стандарту** (scaffold: pyproject,
> AGENTS.md, CI, tests, git init). `/migrate <name>` — dry-run отчёт
> миграции, затем «Применить» / «diff» / «откат».

> **⚡ YOLO** (`/yolo`) — per-session режим без подтверждений опасных
> операций. Переключается командой или кнопкой ⚡ в меню сессии.
> Деструктивные команды (`rm -rf /`, `mkfs`, …) заблокированы всегда.

**Меню сессии** (кнопки):
- **🧠 Модель** — список моделей с бейджами; клик → карточка с описанием,
  контекстом, ценами и кнопкой «✅ Выбрать».
- **💭 Reasoning** — уровень thinking (только у моделей с поддержкой).
- **🌐 Web search** — toggle встроенного поиска (только у моделей с ним).
- **⚡ Tier** — циклическое `default → flex → priority`.
- **📏 Verbosity** — уровень многословности.
- **Plan / Act / Audit**, **Стоимость**, **Новая**, **История**,
  **Удалить**.

**Меню `/projects`:**
- Список проектов (по одному в строке)
- **➕ Новый** — подсказка по `/new`
- **🐙 Клонировать** — подсказка по `/clone`
- **🌐 GitHub-репо** — подсказка по `/ghnew`
- **📚 Все сессии**

**Приём задач:**
- Текст сообщения — обычная задача.
- `.txt` / `.md` и т.п. — длинный промт файлом (до 1 МБ).
- **Фото** — прикладывается как `image_url` (base64). Модель должна
  поддерживать vision, иначе бот предупредит.
- **Вопрос** (заканчивается на `?`, < 200 символов) — быстрый ответ


**Меню `/server` и `/audit`** выполняют read-only команды напрямую, минуя
LLM — бесплатно, сгруппированы по категориям.

## Где хранятся данные

Всё в `BASE_DIR` репозитория; `sessions/` — в `.gitignore`.

| Что | Путь |
|---|---|
| Сессии (история, стоимость, настройки) | `sessions/<id>.json` |
| Индекс метаданных | `sessions/index.json` |
| Медиа-вложения | `sessions/media/<id>/` |
| Audit-отчёты (Markdown) | `sessions/audits/` |
| Экспорт истории | `sessions/history/` |
| Активная сессия Telegram | `sessions/telegram_state.json` |
| `.env` | корень репозитория |

**Проекты по умолчанию:**

- `/new <name>` → `~/bots/<name>`
- `/clone` → `~/bots/<repo>`
- `/ghnew <name>` → `~/bots/<name>` + репо на GitHub

ID сессии — 8 hex-символов.

## Архитектура

    agent_coder/
    ├── __main__.py          # CLI: argparse, REPL
    ├── config.py            # конфиг из .env
    ├── models.py            # ModelInfo + fallback-каталог
    ├── model_catalog.py     # ModelCatalog: API + кэш + fallback
    ├── github_api.py        # create_repo, whoami (через httpx)
    ├── log_safety.py        # маскирование секретов
    ├── shell_runner.py      # единый subprocess с таймаутом и killpg
    ├── audit_ops.py         # read-only проверки проекта
    ├── server_ops.py        # read-only команды диагностики
    ├── agent/
    │   ├── loop.py          # агентный цикл
    │   ├── session.py       # история, стоимость, save/load
    │   ├── session_index.py # sidecar-индекс
    │   ├── compression.py   # сжатие истории
    │   └── prompts.py       # системные промпты (PLAN/ACT/AUDIT)
    ├── llm/
    │   ├── client.py        # AsyncOpenAI + retry
    │   └── types.py         # Usage, ToolCall, format_cost
    ├── tools/               # 10 инструментов
    ├── telegram/            # bot, handlers, keyboards, renderer
    └── ui/                  # rich-вывод для CLI

## Разработка

    # установка с dev-зависимостями
    pip install -e ".[dev]"

    # тесты (311)
    pytest -q

    # линтер
    ruff check .

    # типы
    mypy agent_coder tests

CI (GitHub Actions) прогоняет `ruff check .`, `mypy agent_coder tests` и
`pytest -q -n auto` на Python 3.10 / 3.11 / 3.12 при каждом push в `master`
и в PR.

## Известные ограничения

- Агент работает только через RouterAI; других провайдеров нет.
- `--max-iterations N` переопределяет `MAX_ITERATIONS_*` **только если
  указан явно**. Без флага используются значения из `.env`.
- `MODEL_FAST` и `LOG_LEVEL` читаются из окружения, но в коде пока
  не используются — зарезервированы.
- Стоимость хранится как `float`. Для UI этого достаточно; для биллинга
  точность не гарантируется (переход на `Decimal` — при появлении задачи).
- PDF-отчётов нет — только Markdown.
- Медиа удаляются по retention: файлы старше `MEDIA_RETENTION_DAYS` (30)
  подчищает фоновая задача раз в сутки. При `/delete` — сразу.
- `mypy` в CI исключает `agent_coder/telegram/handlers/**`:
  ~150 ошибок `union-attr` от aiogram 3.x
  (`Message | InaccessibleMessage | None`). Все одного класса —
  mypy не видит защитных `if not session`, которые есть в коде.
  Чинить через хелпер `_msg(query)` — 2–3 часа рефакторинга без
  функциональной пользы; отложено.
- systemd hardening даёт боту право писать в `~/bots` и `~/projects`
  (для `/new`, `/clone`, `/ghnew`). В пределах одного пользователя
  это приемлемо.

## Лицензия

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

MIT — можно свободно использовать, модифицировать и распространять.
См. [`LICENSE`](LICENSE).