# AGENTS.md — правила проекта

Этот проект следует единому стандарту Python-проектов в
/home/botuser/bots/. Стандарт и его применение описаны в agent-coder:
agent_coder/rules/project_standard.md.

Файлы-шаблоны (pyproject-snippet.toml, ci-template.yml,
requirements-dev-base.in) — в пакете agent_coder/templates/.
Bootstrap нового проекта: `agent-coder scaffold --project <name>`.

## Если проект ещё не оформлен

При первом открытии проекта в agent-coder (PLAN/ACT) — проверь
наличие и корректность:

- pyproject.toml — секции [project], [tool.pytest.ini_options],
  [tool.ruff], [tool.ruff.lint], [tool.mypy], [tool.coverage.run]
- requirements.in — прод-зависимости
- requirements-dev.in — из agent_coder/templates/requirements-dev-base.in
- requirements-dev.txt — `uv pip compile --universal requirements-dev.in`
- .gitignore — базовый (Python + .env + AGENTS.md)
- tests/ — pytest, asyncio_mode=auto, testpaths=["tests"]
- .github/workflows/ci.yml — из agent_coder/templates/ci-template.yml
- README.md — запуск, структура, известные ограничения
- .venv/ + lock-файлы — пересборка через `uv pip sync`

Если чего-то нет — предложи план (в PLAN) или создай по шаблону
(в ACT). Не переписывай существующие файлы без необходимости:
сначала read, потом edit_file.

## Конвенции

- Python 3.12+, ruff line-length=88, select=E,F,W,I,UP,B,C4,SIM
- Пакет = snake_case от имени проекта
- База dev: ruff, uv, pytest, pytest-asyncio, pytest-cov, mypy (6 шт.)
- mypy — мягкий профиль (files=<пакет>, check_untyped_defs,
  warn_unused_ignores, ignore_missing_imports); ужесточать до strict
  по мере покрытия типами
- CI: ruff check . && ruff format --check . && mypy && pytest -q
  (без знания имени пакета — mypy берёт files из pyproject.toml)
- Перезапуск бота: `sudo systemctl restart <проект>-bot.service`
  или `sudo botctl restart <проект>-bot` (см. README проекта)
- coverage source — имя пакета, не "app"

## Проверка

В PLAN/ACT доступен tool check_project_standard(workspace) —
детерминированно возвращает список отклонений от стандарта.
Используй его перед финальным отчётом и после правок.

## Что этот файл НЕ описывает

- Специфику домена проекта (платежи, боты, API) — это ваши правила,
  добавьте в этот же файл отдельной секцией.
- Архитектурные решения — в README.md или docs/.
