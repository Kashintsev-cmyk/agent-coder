# Стандарт оформления Python-проектов в /home/botuser/bots/

Применимо: создание нового проекта с нуля, правка существующего, аудит.
Файлы-шаблоны (pyproject-snippet.toml, ci-template.yml,
requirements-dev-base.in, .gitignore-base) —
в пакете `agent_coder/templates/`.
Создание проекта: `agent-coder scaffold --project <name>` (копирует всё сам).

Если в workspace есть AGENTS.md — он главнее этого стандарта:
в system prompt попадает только он, стандарт не подмешивается.

## Обязательные файлы (наличие = MAJOR при аудите)

- pyproject.toml — [project], [tool.pytest.ini_options], [tool.ruff],
  [tool.ruff.lint], [tool.coverage.run]
- requirements.in — прод-зависимости
- requirements-dev.in — из agent_coder/templates/requirements-dev-base.in (scaffold копирует автоматически)
- requirements-dev.txt — `uv pip compile --universal requirements-dev.in`
  (pip-tools архивирован; uv читает `requires-python` из pyproject.toml)
- .gitignore — из agent_coder/templates/.gitignore-base (scaffold копирует автоматически)
- tests/ — pytest
- README.md — запуск, структура, известные ограничения
- AGENTS.md — правила проекта (см. agent_coder/templates/AGENTS.md)
- .github/workflows/ci.yml — если есть git remote;
  из agent_coder/templates/ci-template.yml (scaffold копирует автоматически)

## Обязательные секции pyproject.toml

- `[project]`:
  - `requires-python = ">=3.12"`
  - `license = "<SPDX>"` — строка, не `{ text = "..." }`
    (PEP 639; setuptools>=77 с license-files отвергает старую форму)
    - Open-source: валидный SPDX-id, например `MIT`, `Apache-2.0`.
    - Closed-source / proprietary: `license = "LicenseRef-Proprietary"`.
      `LicenseRef-*` — валидный SPDX-expression (SPDX 2.3),
      `packaging.licenses` его принимает. `license = "Proprietary"` —
      **не** SPDX, сборка падает.
  - `dependencies` (или зависимости в `requirements.in` — выбрать одно и следовать)
- `[build-system]`: `requires = ["setuptools>=68"]`, `build-backend = "setuptools.build_meta"`
- `[tool.setuptools.packages.find]` (или `py-modules`) — иначе `pip install -e .`
  падает на package discovery
- `[tool.ruff]`: `line-length = 88`, `target-version = "py312"`
- `[tool.ruff.lint]`: `select ⊇ {E, F, W, I}`
- `[tool.coverage.run]`: `source = [<package>]` (не `"app"`)
- `[tool.pytest.ini_options]`: `testpaths = ["tests"]`

## Конвенции

- Python 3.12+
- ruff line-length=88, target-version="py312",
  select=["E","F","W","I","UP","B","C4","SIM"]
- Пакет = snake_case от имени проекта
- coverage source = имя пакета проекта (не "app")
- База dev: ruff, uv, pytest, pytest-asyncio, pytest-cov (5 шт.)
- mypy — отдельной миграцией, не в базе
- CI: ruff check . && pytest -q (без знания имени пакета)

## Как применять

Создание с нуля:
1. `agent-coder scaffold --project <name>` — скелет + шаблоны + git init.
2. Проверить/дозаполнить README.md (описание проекта).
3. Сгенерировать lock: `uv pip compile --universal requirements-dev.in`.
4. Перед финальным отчётом вызвать check_project_standard(workspace),
   устранить замечания, вызвать повторно — должно быть 0 отклонений.

Аудит / приведение к стандарту:
1. Вызвать check_project_standard(workspace).
2. В PLAN — план правок; в ACT — применить; в AUDIT — включить
   результат в отчёт.
3. Не переписывать существующие файлы без необходимости:
   сначала read, потом edit_file.

## Что НЕ требует этого стандарта

- Проекты под /home/botuser/bots/_shared-dev/ — отдельный репозиторий (legacy-справочник для weather-bot/futures_3.0). Стандарт agent-coder к нему не применяется.
- Проекты с явным AGENTS.md, переопределяющим стандарт.
