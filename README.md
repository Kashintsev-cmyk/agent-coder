# agent-coder

CLI coding agent на базе RouterAI. Работает прямо на сервере, без SSH-туннелей
и remote-helper'ов, и считает точную стоимость каждой генерации.

## Возможности

- **Три режима** — `plan` (только чтение), `act` (полный доступ), `audit` (строгий аудитор кода)
- **Точная стоимость** — `usage.cost` от RouterAI напрямую, без оценок по токенам
- **10 инструментов**: чтение, запись, точечные правки, поиск, терминал, git
- **Подтверждения** — опасные команды спрашивают разрешения, безопасные read-only
  выполняются сразу; `--yolo` отключает вопросы
- **Сессии на диске** — история сообщений, стоимость и настройки сохраняются,
  можно возобновить (`--resume <id>`) или продолжить последнюю (`--continue`)
- **Telegram-бот** — тот же агент в мессенджере, плюс бесплатные (без LLM)
  меню диагностики сервера и аудита проекта
- **Audit-отчёты** — в Telegram после задачи в режиме `audit` результат
  автоматически выгружается в Markdown и PDF (`sessions/audits/`)

## Требования

| Что | Зачем | Обязательно |
|-----|-------|-------------|
| Python **≥ 3.10** | указан в `pyproject.toml` (`requires-python`), проверено на 3.10.12 | да |
| `ROUTERAI_API_KEY` | доступ к моделям через routerai.ru | да |
| `ripgrep` (`rg`) | инструмент `search` (без него вернёт «rg не найден») | для поиска |
| `git` | инструменты `git_status/diff/log/commit` | для git-работы |
| шрифты DejaVu | кириллица в PDF-отчётах (`/usr/share/fonts/truetype/dejavu/`) | нет, есть fallback |

## Установка

```bash
cd /home/botuser/bots/agent-coder
python3 -m venv .venv
source .venv/bin/activate
pip install -e .
cp .env.example .env
# отредактируй .env — вставь ROUTERAI_API_KEY
```

Устанавливаются две команды:

- `agent-coder` — CLI (`agent_coder.__main__:main`)
- `agent-coder-bot` — Telegram-бот (`agent_coder.telegram.bot:main`)

CLI также запускается как модуль: `python3 -m agent_coder`.

## Использование

```bash
# Одна задача — выполнить и выйти
agent-coder --cwd /path/to/project "создай README.md с описанием проекта"

# Интерактивный режим (REPL), если задача не передана
agent-coder --cwd /path/to/project

# Plan-режим: только чтение, без изменений файлов
agent-coder --plan "проанализируй проект и предложи план рефакторинга"

# Без подтверждений (осторожно)
agent-coder --yolo "прогони тесты и почини падения"

# Возобновить сессию / продолжить последнюю в этой директории
agent-coder --resume 7db6a452
agent-coder --continue --cwd /path/to/project

# Выбрать модель и включить подробные логи
agent-coder --model anthropic/claude-sonnet-4.6 -v "отрефактори модуль парсера"
```

### CLI-флаги

| Флаг | По умолчанию | Описание |
|------|--------------|----------|
| `task` (позиционный, необязательный) | — | Задача. Если не указана — запускается REPL |
| `--cwd DIR` | `DEFAULT_WORKSPACE` (`~/projects`) | Рабочая директория (workspace) |
| `--model ID` | `MODEL_MAIN` (`deepseek/deepseek-v4.1-flash`) | Модель RouterAI для новой сессии |
| `--plan` | выкл. | Создать (или перевести при `--resume`) сессию в Plan-режим |
| `--yolo` | выкл. | Не спрашивать подтверждений |
| `--resume ID` | — | Возобновить сессию по ID |
| `--continue` | выкл. | Продолжить последнюю сессию для этой workspace |
| `--max-iterations N` | `MAX_ITERATIONS` (50) | Лимит итераций агентного цикла |
| `--verbose`, `-v` | выкл. | Логи уровня DEBUG |
| `--version` | — | Показать версию |

## Режимы

| Режим | Что разрешено | Лимит итераций |
|-------|---------------|----------------|
| `plan` | только read-only инструменты + безопасные (`whitelist`) команды | `MAX_ITERATIONS_PLAN` (30) |
| `act` | всё: запись, правки, любые команды, git-коммит | `MAX_ITERATIONS_ACT` (50) |
| `audit` | как `plan`, но с промптом строгого аудитора; в Telegram результат дополнительно выгружается в `.md` + `.pdf` | `MAX_ITERATIONS_AUDIT` (100) |

Режим по умолчанию — `act`. В CLI audit включается командой `/audit` в REPL
(флага `--audit` нет), в Telegram — `/mode audit` или кнопкой в меню сессии.

В `plan` и `audit` любые попытки изменить файлы отбиваются на уровне реестра
инструментов: агент получает сообщение «инструмент недоступен в этом режиме».

## Slash-команды (REPL)

| Команда | Действие |
|---------|----------|
| `/plan` | Переключиться в Plan-режим (история сообщений очищается) |
| `/act` | Переключиться в Act-режим (история очищается) |
| `/audit` | Переключиться в Audit-режим (история очищается) |
| `/cost` | Показать накопленную стоимость и число запросов |
| `/session` | Информация о текущей сессии |
| `/sessions` | Список всех сессий (до 20, свежие сверху) |
| `/switch <id>` | Переключиться на другую сессию |
| `/title <текст>` | Задать/посмотреть заголовок сессии |
| `/history` | Последние 20 сообщений |
| `/clear` | Очистить историю сообщений (стоимость сохраняется) |
| `/model` | Выбрать модель из списка (интерактивно, история очищается) |
| `/reasoning` | Выбрать уровень reasoning |
| `/help` | Справка по командам |
| `/exit`, `/quit`, `/q` | Выход |

## Инструменты

| Инструмент | Назначение | Подтверждение |
|------------|-----------|---------------|
| `read_file` | Чтение файла с нумерацией строк, есть `offset`/`limit` (до 2000 строк за раз; целиком — файлы до 200 КБ) | нет |
| `list_files` | Рекурсивный список файлов и папок (glob-шаблон, до 500 записей) | нет |
| `search` | Поиск по проекту через `ripgrep` (до 200 совпадений) | нет |
| `write_file` | Создать или полностью перезаписать файл | нет |
| `edit_file` | Точечная замена `old_string` → `new_string` (есть `replace_all`) | нет |
| `run_command` | Shell-команда в workspace. Whitelist read-only команд и pipe между ними — без вопросов; остальное (`rm`, `kill`, `sudo`, редиректы, `;`) требует подтверждения. Таймаут 300 с, вывод до 30 000 байт на поток | по команде |
| `git_status` | `git status --porcelain --branch` | нет |
| `git_diff` | Diff изменений (`staged`, `path`) | нет |
| `git_log` | Последние N коммитов (1–50) | нет |
| `git_commit` | Коммит того, что уже в индексе (`git add` не делает) | да |

`read_file`, `write_file`, `edit_file` и `list_files` работают только внутри
workspace: путь за его пределы отклоняется.

## Модели

Кураторский список (`agent_coder/models.py`) — 12 моделей, доступных для выбора
через `/model` (CLI) и кнопку «🧠 Модель» (Telegram). Цены — ₽ за 1M токенов, контекст — в токенах.

| Модель | ID | Контекст | ₽ in / out | Reasoning |
|--------|----|----------|-----------|-----------|
| DeepSeek V4.1 Flash *(по умолчанию)* | `deepseek/deepseek-v4.1-flash` | 1 048 576 | 2.39 / 47 | да |
| DeepSeek V4 Pro | `deepseek/deepseek-v4-pro` | 1 048 576 | 35 / 70 | да |
| Claude Sonnet 4.6 | `anthropic/claude-sonnet-4.6` | 1 000 000 | 80 / 400 | да |
| Claude Haiku 4.5 | `anthropic/claude-haiku-4.5` | 200 000 | 27 / 135 | да |
| GPT-5 Nano | `openai/gpt-5-nano` | 400 000 | 1.4 / 11 | да |
| GPT-5.4 Mini | `openai/gpt-5.4-mini` | 400 000 | 20 / 120 | да |
| GPT-5.4 | `openai/gpt-5.4` | 1 050 000 | 68 / 400 | да |
| Gemini 3.5 Flash | `google/gemini-3.5-flash` | 1 048 576 | 40 / 240 | да |
| Gemini 2.5 Flash | `google/gemini-2.5-flash` | 1 048 576 | 8 / 67 | да |
| Qwen3 Coder Next | `qwen/qwen3-coder-next` | 262 144 | 3.3 / 22 | нет |
| Kimi K2.7 Code | `moonshotai/kimi-k2.7-code` | 262 144 | 19 / 80 | нет |
| GLM 5.3 Flash | `z-ai/glm-5.3-flash` | 1 310 720 | 1.3 / 4.1 | да |

Сменить модель для сессии: `--model <id>` при запуске или `/model` в REPL
(в Telegram — кнопка «🧠 Модель» в меню сессии). Смена модели очищает историю.

**Reasoning.** Уровни: `none`, `minimal`, `low`, `medium`, `high`, `xhigh`,
`max`, `ultra`; по умолчанию `medium`. `none` означает, что параметр вообще
не передаётся в API. Переключение — `/reasoning` (в REPL) или `/reasoning`
и кнопка в Telegram; сохраняется в сессии.

## Где хранятся данные

Всё лежит в корне репозитория (`config.BASE_DIR`) — папка `sessions/`
в `.gitignore`, так что история не попадает в git.

| Что | Путь |
|-----|------|
| Сессии (JSON: id, workspace, модель, режим, reasoning, история, стоимость) | `sessions/<session_id>.json` |
| Audit-отчёты | `sessions/audits/<id>_<YYYYMMDD_HHMMSS>.md` и `.pdf` |
| Экспорт истории (кнопка «Скачать полную (.md)» в Telegram) | `sessions/history/<id>_<YYYYMMDD_HHMMSS>.md` |
| Активная сессия Telegram-чата (`chat_id` → `session_id`) | `telegram_state.json` |
| `.env` с ключами и настройками | корень репозитория |

ID сессии — 8 hex-символов (`uuid4().hex[:8]`). Каталог сессий переопределяется
константой `SESSIONS_DIR` в `agent_coder/config.py`. Audit-отчёт в Telegram
дополнительно копируется в сам проект как `AUDIT_REPORT.md` / `AUDIT_REPORT.pdf`.

## Переменные окружения

Читаются из `.env` (см. `.env.example`).

| Переменная | По умолчанию | Описание |
|------------|--------------|----------|
| `ROUTERAI_API_KEY` | — (**обязательна**) | API-ключ routerai.ru |
| `ROUTERAI_BASE_URL` | `https://routerai.ru/api/v1` | Базовый URL API |
| `ROUTERAI_TIMEOUT` | `300.0` | Таймаут запроса к LLM, секунды |
| `MODEL_MAIN` | `deepseek/deepseek-v4.1-flash` | Модель по умолчанию |
| `MODEL_FAST` | `MODEL_MAIN` | Зарезервировано (в коде пока не используется) |
| `MAX_RETRIES` | `3` | Число попыток при ошибках сети/лимитов |
| `RETRY_BACKOFF` | `2.0` | База экспоненциального backoff |
| `DEFAULT_WORKSPACE` | `~/projects` | Workspace по умолчанию для `--cwd` |
| `MAX_ITERATIONS` | `50` | Общий лимит итераций (fallback) |
| `MAX_ITERATIONS_PLAN` | `30` | Лимит итераций в Plan-режиме |
| `MAX_ITERATIONS_ACT` | `50` | Лимит итераций в Act-режиме |
| `MAX_ITERATIONS_AUDIT` | `100` | Лимит итераций в Audit-режиме |
| `LOG_LEVEL` | `INFO` | Зарезервировано (CLI задаёт уровень через `-v`) |
| `TELEGRAM_TOKEN` | — | Токен бота (нужен только для `agent-coder-bot`) |
| `TELEGRAM_USER_ID` | `0` | Единственный разрешённый пользователь бота |
| `TELEGRAM_PROJECTS_DIRS` | `~/projects,~/bots` | Папки, из которых бот показывает проекты |

## Telegram-бот

```bash
agent-coder-bot
```

Команды: `/start`, `/help`, `/projects`, `/new <путь>`, `/sessions`,
`/current`, `/history`, `/delete [id]`, `/reasoning`, `/mode plan|act|audit`,
`/cost`, `/balance` (баланс RouterAI), `/server` (диагностика сервера),
`/audit` (меню проверок проекта), `/cancel`.

Выбор модели делается кнопкой «🧠 Модель» в меню сессии; команда `/model`
зарегистрирована в списке команд Telegram, но хендлера для неё нет.
Переключение режима также доступно кнопками Plan / Act / Audit в меню сессии.

- Доступ ограничен одним `TELEGRAM_USER_ID` (фильтр на роутере).
- Долгие команды выполняются в фоне, прогресс обновляется в одном сообщении;
  `/cancel` прерывает задачу.
- Подтверждения приходят инлайн-кнопками «Разрешить / Отклонить» (таймаут 900 с).
- Меню `/server` и `/audit` выполняют заранее отобранные read-only команды
  напрямую, минуя LLM — это бесплатно (см. `server_ops.py`, `audit_ops.py`).
- В Audit-режиме после завершения задачи бот присылает PDF-отчёт.

## Структура проекта

```
agent_coder/
├── __main__.py          # CLI: argparse, REPL, slash-команды
├── config.py            # конфиг из .env, пути, лимиты
├── models.py            # кураторский список моделей и уровни reasoning
├── audit_ops.py         # read-only проверки проекта (ruff, bandit, mypy, ...)
├── server_ops.py        # read-only команды диагностики сервера
├── agent/
│   ├── loop.py          # агентный цикл: LLM → tool_call → результат → LLM
│   ├── session.py       # сессия: история, стоимость, save/load
│   ├── prompts.py       # режимы Plan/Act и выбор системного промпта
│   └── audit_prompt.py  # системный промпт аудитора
├── llm/
│   ├── client.py        # AsyncOpenAI + retry, разбор usage.cost
│   └── types.py         # Usage, ToolCall, ChatResponse, format_cost
├── tools/               # реестр и 10 инструментов
├── reports/
│   └── audit_saver.py   # MD → PDF отчёт аудита
├── telegram/            # bot, handlers, keyboards, renderer, state
└── ui/
    └── terminal.py      # rich-вывод, диалог подтверждения
```

## Статус и известные ограничения

- Агент общается с LLM только через RouterAI; других провайдеров нет.
- `--max-iterations` переопределяет `config.MAX_ITERATIONS`, но фактически
  не влияет на лимит: для всех трёх режимов заданы свои значения
  (`MAX_ITERATIONS_PLAN/ACT/AUDIT`). Меняйте лимиты через `.env`.
- `MODEL_FAST` и `LOG_LEVEL` прочитаны из окружения, но в коде не используются.
- Кириллица в PDF зависит от наличия шрифтов DejaVu на сервере.
