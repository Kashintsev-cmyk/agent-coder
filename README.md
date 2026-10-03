# agent-coder

[![CI](https://github.com/Kashintsev-cmyk/agent-coder/actions/workflows/ci.yml/badge.svg)](https://github.com/Kashintsev-cmyk/agent-coder/actions/workflows/ci.yml)
![Python](https://img.shields.io/badge/python-3.10%20%7C%203.11%20%7C%203.12-blue)
![License](https://img.shields.io/badge/license-MIT-green)

Автономный coding-агент для сервера. CLI и Telegram-бот на базе RouterAI:
читает, правит и запускает код прямо на машине — без SSH-туннелей, без
remote-helper'ов, с точным учётом стоимости каждой генерации.

## Возможности

**Агент**
- Три режима: `plan` (только чтение), `act` (полный доступ), `audit` (строгий аудитор)
- 10 инструментов: чтение/запись/правки файлов, поиск (`ripgrep`), shell с whitelist, git
- Подтверждения на опасные операции; безопасные read-only команды — без вопросов
- Точная стоимость от RouterAI (`usage.cost`), без оценок по токенам

**Модели**
- Каталог из `/api/v1/models` RouterAI, кэшируется на диск (TTL 24 ч)
- Список моделей задаётся в `.env` (`CURATED_MODEL_IDS`) — без правок кода
- Адаптивное меню: кнопки (reasoning / web search / tier / verbosity) только для
  возможностей, которые модель реально поддерживает
- Карточка модели с описанием, контекстом и ценами

**LLM-фичи**
- Встроенный web search RouterAI (`plugins=[{id:web}]`)
- Service tier: `default` / `flex` (−50% цены) / `priority` (быстрее)
- Verbosity, reasoning, temperature — с per-session настройкой
- Сжатие истории: старые сообщения резюмируются дешёвой моделью
- Приём изображений (vision-модели): фото передаётся как `image_url`

**Безопасность**
- Whitelist read-only команд; pipe без `awk`/`sed`/`env`/`printenv`
- Маскирование секретов (`ROUTERAI_API_KEY`, `TELEGRAM_TOKEN`, `sk-*`, `Bearer *`)
  в логах и выводе команд
- Path traversal защищён в `search`, `read_file`, `write_file`, `list_files`
- systemd hardening: `NoNewPrivileges`, `ProtectSystem=strict`, `ReadWritePaths`

**Инфраструктура**
- Сессии на диске + sidecar-индекс метаданных (быстрый `/sessions`)
- Медиа-retention: файлы старше 30 дней удаляются фоновой задачей
- Retry с jitter и уважением `Retry-After`
- GitHub Actions: pytest + ruff на Python 3.10–3.12

## Быстрый старт

### Требования

| Что | Зачем | Обязательно |
|---|---|---|
| Python **≥ 3.10** | `requires-python` в `pyproject.toml` | да |
| `ROUTERAI_API_KEY` | доступ к моделям через [routerai.ru](https://routerai.ru) | да |
| `ripgrep` (`rg`) | инструмент `search` | для поиска |
| `git` | инструменты `git_*` | для git-работы |

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

    agent-coder --cwd /path/to/project "проанализируй структуру и предложи план рефакторинга"

Без задачи — откроется REPL.

### Первый запуск (Telegram)

    # в .env должен быть TELEGRAM_TOKEN и TELEGRAM_USER_ID
    agent-coder-bot

Открой бота в Telegram → `/start` → `/projects` → выбери проект → отправь
задачу текстом.

## Использование

### CLI

    # Одна задача — выполнить и выйти
    agent-coder --cwd /path/to/project "создай README.md с описанием проекта"

    # REPL (без аргумента-задачи)
    agent-coder --cwd /path/to/project

    # Plan-режим: только чтение
    agent-coder --plan "предложи рефакторинг"

    # Без подтверждений (осторожно)
    agent-coder --yolo "прогони тесты и почини падения"

    # Возобновить сессию / продолжить последнюю в директории
    agent-coder --resume 7db6a452
    agent-coder --continue --cwd /path/to/project

    # Выбрать модель и включить debug-логи
    agent-coder --model anthropic/claude-sonnet-5.5 -v "отрефактори модуль парсера"

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
| `--max-iterations N` | из `.env` | Лимит итераций (переопределяет `MAX_ITERATIONS_*` для всех режимов) |
| `--verbose`, `-v` | выкл. | DEBUG-логи |
| `--version` | — | Версия |

### Slash-команды (REPL)

| Команда | Действие |
|---|---|
| `/plan`, `/act`, `/audit` | Сменить режим (история очищается) |
| `/cost` | Стоимость и число запросов |
| `/session` | Информация о текущей сессии |
| `/sessions` | Список сессий (до 20, свежие сверху) |
| `/switch <id>` | Переключиться на сессию |
| `/title <текст>` | Заголовок сессии |
| `/history` | Последние 20 сообщений |
| `/clear` | Очистить историю (стоимость сохраняется) |
| `/model` | Выбрать модель (интерактивно) |
| `/reasoning` | Уровень reasoning |
| `/help`, `/exit`, `/quit`, `/q` | Справка / выход |

## Режимы

| Режим | Что разрешено | Лимит итераций |
|---|---|---|
| `plan` | read-only инструменты + whitelist-команды | `MAX_ITERATIONS_PLAN` (30) |
| `act` | всё: запись, правки, любые команды, git-коммит | `MAX_ITERATIONS_ACT` (50) |
| `audit` | как `plan`, но с промптом строгого аудитора; в Telegram — Markdown-отчёт | `MAX_ITERATIONS_AUDIT` (100) |

По умолчанию — `act`. В `plan`/`audit` попытки изменить файлы отбиваются
на уровне реестра инструментов.

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
Метаданные (цены, контекст, capabilities) тянутся из `/api/v1/models` RouterAI
и кэшируются на диск.

Дефолтный набор:

| Модель | ID | ₽ in / out (за 1M) | Бейджи |
|---|---|---|---|
| **DeepSeek V4.1 Flash** *(default)* | `deepseek/deepseek-v4.1-flash` | ~11 / ~21 | 👁⚙🔧 |
| GPT-5.6 Luna | `openai/gpt-5.6-luna` | ~22 / ~130 | 👁🌐⚙🔧📏 |
| GLM 5.3 Flash | `z-ai/glm-5.3-flash` | ~9 / ~30 | 👁⚙🔧 |
| Gemini 3.8 Flash | `google/gemini-3.8-flash` | ~81 / ~406 | 👁🌐⚙🔧 |
| Claude Sonnet 5.5 | `anthropic/claude-sonnet-5.5` | ~216 / ~1082 | 👁🌐⚙🔧📏 |
| Grok 4.7 | `x-ai/grok-4.7` | ~216 / ~649 | 👁🌐⚙🔧 |
| Kimi K2.7 Code | `moonshotai/kimi-k2.7-code` | ~77 / ~325 | 👁🧠🔧 |

**Бейджи:** 👁 vision · 🌐 встроенный web search · 🧠 reasoning · ⚙ reasoning
с градацией · 🔧 tools · 📏 verbosity.

Цены — ориентир на момент публикации. Актуальные всегда подтягиваются из API.

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
| `TEMPERATURE` | `0.2` | Температура (0.0–2.0) |
| `SERVICE_TIER` | `default` | `default` / `flex` / `priority` |
| `WEB_SEARCH_ENGINE` | `native` | `native` / `exa` |
| `WEB_SEARCH_MAX_RESULTS` | `5` | Сколько результатов запрашивать |

**Модели**

| Переменная | По умолчанию | Описание |
|---|---|---|
| `MODEL_MAIN` | `deepseek/deepseek-v4.1-flash` | Модель для новых сессий |
| `MODEL_FAST` | `MODEL_MAIN` | Зарезервировано |
| `CURATED_MODEL_IDS` | 7 id через запятую | Список для UI |
| `DEFAULT_MODEL_ID` | `deepseek/deepseek-v4.1-flash` | Должна входить в `CURATED_MODEL_IDS` |

**Каталог моделей**

| Переменная | По умолчанию | Описание |
|---|---|---|
| `MODELS_CACHE_TTL` | `24` | TTL дискового кэша, ч |
| `MODELS_CACHE_DIR` | `~/.cache/agent-coder` | Куда писать кэш |
| `MODELS_API_TIMEOUT` | `5.0` | Таймаут запроса к `/models` |

**Сжатие истории**

| Переменная | По умолчанию | Описание |
|---|---|---|
| `MAX_HISTORY_TOKENS` | `128000` | Порог = `min(model.context * 0.6, MAX_HISTORY_TOKENS)` |
| `SUMMARY_MODEL` | `z-ai/glm-5.3-flash` | Модель для резюме |
| `MEDIA_RETENTION_DAYS` | `30` | Retention медиа-вложений, дней |

**Агент и окружение**

| Переменная | По умолчанию | Описание |
|---|---|---|
| `DEFAULT_WORKSPACE` | `~/projects` | Workspace по умолчанию |
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

## Telegram-бот

    agent-coder-bot

**Команды:** `/start`, `/help`, `/projects`, `/new <путь>`, `/sessions`,
`/current`, `/history`, `/delete [id]`, `/model`, `/reasoning`,
`/mode plan|act|audit`, `/cost`, `/balance`, `/server`, `/audit`, `/cancel`.

**Меню сессии** (кнопки):
- **🧠 Модель** — список моделей с бейджами; клик → карточка с описанием,
  контекстом, ценами и кнопкой «✅ Выбрать».
- **💭 Reasoning** — уровень thinking (только у моделей с поддержкой).
- **🌐 Web search** — toggle встроенного поиска (только у моделей с ним).
- **⚡ Tier** — циклическое `default → flex → priority`.
- **📏 Verbosity** — уровень многословности.
- **Plan / Act / Audit**, **Стоимость**, **Новая**, **История**, **Удалить**.

Quick-настройки раскладываются в 1, 2, 3 или 2+2 кнопки в ряд — в зависимости
от того, сколько возможностей поддерживает выбранная модель.

**Приём задач:**
- Текст сообщения — обычная задача.
- `.txt` / `.md` и т.п. — длинный промт файлом (до 1 МБ).
- **Фото** — прикладывается как `image_url` (base64). Модель должна
  поддерживать vision, иначе бот предупредит.

**Меню `/server` и `/audit`** выполняют read-only команды напрямую, минуя LLM —
бесплатно, сгруппированы по категориям.

## Где хранятся данные

Всё в `BASE_DIR` репозитория; `sessions/` — в `.gitignore`.

| Что | Путь |
|---|---|
| Сессии (история, стоимость, настройки) | `sessions/<id>.json` |
| Индекс метаданных | `sessions/index.json` |
| Медиа-вложения | `sessions/media/<id>/` |
| Audit-отчёты (Markdown) | `sessions/audits/` |
| Экспорт истории | `sessions/history/` |
| Активная сессия Telegram | `telegram_state.json` |
| `.env` | корень репозитория |

ID сессии — 8 hex-символов.

## Архитектура

    agent_coder/
    ├── __main__.py          # CLI: argparse, REPL
    ├── config.py            # конфиг из .env
    ├── models.py            # ModelInfo + fallback-каталог
    ├── model_catalog.py     # ModelCatalog: API + кэш + fallback
    ├── log_safety.py        # маскирование секретов
    ├── shell_runner.py      # единый subprocess с таймаутом и killpg
    ├── audit_ops.py         # read-only проверки проекта
    ├── server_ops.py        # read-only команды диагностики
    ├── agent/
    │   ├── loop.py          # агентный цикл
    │   ├── session.py       # история, стоимость, save/load
    │   ├── session_index.py # sidecar-индекс
    │   ├── compression.py   # сжатие истории
    │   └── prompts.py       # системные промпты
    ├── llm/
    │   ├── client.py        # AsyncOpenAI + retry
    │   └── types.py         # Usage, ToolCall, format_cost
    ├── tools/               # 10 инструментов
    ├── telegram/            # bot, handlers, keyboards, renderer
    └── ui/                  # rich-вывод для CLI

## Разработка

    # установка с dev-зависимостями
    pip install -e ".[dev]"

    # тесты (276)
    pytest -q

    # линтер
    ruff check .

    # mypy (запускается вручную — см. Известные ограничения)
    mypy agent_coder tests

CI (GitHub Actions) прогоняет `ruff check .` и `pytest -q` на Python
3.10 / 3.11 / 3.12 при каждом push в `master` и в PR.

## Известные ограничения

- Агент работает только через RouterAI; других провайдеров нет.
- `--max-iterations N` переопределяет `MAX_ITERATIONS_*` **только если указан
  явно**. Без флага используются значения из `.env`.
- `MODEL_FAST` и `LOG_LEVEL` читаются из окружения, но в коде пока
  не используются — зарезервированы.
- Стоимость хранится как `float`. Для UI этого достаточно; для биллинга
  точность не гарантируется (переход на `Decimal` — при появлении такой задачи).
- PDF-отчётов нет — только Markdown.
- Медиа удаляются по retention: файлы старше `MEDIA_RETENTION_DAYS` (30)
  подчищает фоновая задача раз в сутки. При `/delete` — сразу.
- `mypy` настроен в `pyproject.toml`, но **не входит в CI**: 98 из 105 ошибок —
  `union-attr` от aiogram в `handlers.py`. Разбирать вместе с разбиением
  `handlers.py` на модули.

## Лицензия

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

MIT — можно свободно использовать, модифицировать и распространять.
См. [`LICENSE`](LICENSE).