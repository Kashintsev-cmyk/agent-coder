# Telegram-бот agent-coder

Полная справка. Общий обзор — в `README.md` (`## Telegram-бот`).

## Запуск

    agent-coder-bot

systemd: `agent-coder-bot.service` (пользователь `botuser`).

## Доступ

Один пользователь. Router (`telegram/handlers/shared.py`) фильтрует
по `TELEGRAM_USER_ID`; остальные не получают ответа.

## Команды

### Основное

| Команда | Действие |
|---|---|
| `/start` | Приветствие, меню проектов |
| `/help` | Справка |
| `/cancel` | Остановить текущую задачу |
| `/yolo [on\|off]` | Без подтверждений (per-session; ⚡ деструктивные команды заблокированы всегда) |

### Проекты

| Команда | Действие |
|---|---|
| `/projects` | Список проектов (`TELEGRAM_PROJECTS_DIRS`) |
| `/new <name>` | Создать проект **по стандарту** (scaffold: pyproject, AGENTS.md, CI, tests, git init) — после подтверждения |
| `/new /abs/path` | Открыть существующий проект по явному пути |
| `/clone <url>` | Клонировать git-репо |
| `/ghnew <name>` | Создать локально + репо на GitHub + push (нужен `GITHUB_TOKEN`) |

### Сессии

| Команда | Действие |
|---|---|
| `/sessions` | Все сессии (кнопки) |
| `/current` | Текущая сессия |
| `/history` | Последние сообщения |
| `/delete [id]` | Удалить сессию |
| `/model` | Выбрать модель (кнопки) |
| `/reasoning` | Уровень reasoning (модели с support) |
| `/mode plan\|act\|audit\|chat` | Сменить режим |
| `/verify [model]` | Верифицировать findings аудита (дефолт glm-5.3, LLM) |
| `/cost` | Стоимость и число запросов сессии |
| `/balance` | Баланс RouterAI |
| `/chat` | Быстрый переход в CHAT-режим |

### Диагностика и аудит (без LLM)

| Команда | Действие |
|---|---|
| `/server` | Read-only команды сервера, сгруппированы |
| `/audit` | Аудит проекта: read-only, свой набор op'ов |

`/server` и `/audit` выполняются **напрямую**, минуя LLM — бесплатно.

### Миграция

| Команда | Действие |
|---|---|
| `/migrate` | Список проектов (кнопки) |
| `/migrate <name>` | Dry-run отчёт: `score`, `state path`, `md changed/unchanged`, `notes lines` |

Двухшаговый UX:

1. `/migrate <name>` → dry-run отчёт → кнопки `[✅ Применить] [❌ Отмена]`.
2. Применить → `state.json` + перегенерировать `<name>.md`;
   кнопки `[📊 diff] [↩️ откат] [ok]`.

Файлы:
- `<name>.md` — генерируемый view (`agent_coder-migrate`), детерминированный.
- `<name>.notes.md` — ручные Deferred/Done, **не трогается** ботом.
- `state.json` — `~/.agent-coder/projects/<name>/`, вне git.

Откат трогает только `<name>.md` (`git checkout -- <name>.md` в `_migrations`),
`.notes.md` — никогда.

## Режимы

| Режим | Доступ | Когда |
|---|---|---|
| `plan` | read-only + safe `run_command` | планирование |
| `act` | все инструменты | правки, коммиты |
| `audit` | read-only, промпт аудитора, Markdown-отчёт | аудит проекта |
| `chat` | **без инструментов** | обсуждение |

CHAT — без tools: с урезанным набором DeepSeek уходил в нативный DSML-формат.

## Approval

Опасные операции (`write_file`, `edit_file`, `run_command` с destructive)
требуют подтверждения: inline `[✅ Разрешить] [❌ Отклонить]`.
Таймаут — `APPROVAL_TIMEOUT` (сек, default 900). `/yolo` отключает.

## Меню сессии (кнопки)

- **🧠 Модель** — список с бейджами, карточка с ценой/контекстом, «✅ Выбрать».
- **💭 Reasoning** — уровень thinking.
- **🌐 Web search** — toggle (у моделей с поддержкой).
- **⚡ Tier** — `default → flex → priority`.
- **📏 Verbosity** — многословность.
- **Plan / Act / Audit / Chat**, **Стоимость**, **Новая**, **История**, **Удалить**.

## Приём задач

- Текст — обычная задача.
- `.txt` / `.md` (до 1 МБ) — длинный промт файлом.
- **Фото** — `image_url` (base64); модель должна поддерживать vision.
- **Вопрос** (кончается на `?`, < 200 символов) — быстрый ответ без
  агентного цикла (CHAT-промпт).

## Переменные окружения

| Переменная | Default | Описание |
|---|---|---|
| `TELEGRAM_TOKEN` | — (**обязательна**) | Токен бота |
| `TELEGRAM_USER_ID` | `0` | Единственный разрешённый пользователь |
| `TELEGRAM_PROJECTS_DIRS` | `~/projects,~/bots` | Папки для `/projects` |
| `APPROVAL_TIMEOUT` | `900` | Таймаут approval, с |
| `GITHUB_TOKEN` | — | Classic PAT, scope `repo` (для `/ghnew`/`/clone`) |

## Где хранятся данные

| Что | Путь |
|---|---|
| Активная сессия chat_id → session_id | `sessions/telegram_state.json` |
| Сессии | `sessions/<id>.json` |
| Медиа | `sessions/media/` (`MEDIA_RETENTION_DAYS`, default 30) |
| Состояние миграции | `~/.agent-coder/projects/<project>/state.json` (вне git) |

## Связанные файлы

- `agent_coder/telegram/bot.py` — запуск, `BOT_COMMANDS`, error handler.
- `agent_coder/telegram/handlers/` — по темам: `commands.py`, `task.py`,
  `approval.py`, `github.py`, `migrate.py`, `ops.py`, `session_menu.py`,
  `runner.py`, `shared.py` (router + глобальное состояние), `project_utils.py`.
- `agent_coder/telegram/keyboards.py` — все inline-клавиатуры.
- `agent_coder/telegram/renderer.py` — рендер tool-call/usage/assistant.

## Известные ограничения

- `F22` (docs/FINDINGS.md) — если `MODEL_MAIN` в `.env` неизвестна
  каталогу, при импорте `config` печатается warning; не влияет на работу.
- CHAT без tools: не проси в CHAT «примени правку» — нужен ACT.
- Approval-сообщение одно на tool call; при параллельных задачах
  в одном чате приоритет у последней.
