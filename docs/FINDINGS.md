# FINDINGS — архитектурные находки и долги

Baseline: 2026-10-18, после self-migration `ai_image_bot` и переноса
инфраструктуры в `agent-coder`. Обновлять при закрытии/появлении пунктов.

Поля: **ID | severity | what | fix | status | source**.
Severity: major (ломает цикл/данные), minor (долг/неудобство).

## Инфраструктура агента

| ID | sev | what | fix | status | source |
|---|---|---|---|---|---|
| S1/F10 | major | `_migrations/generate.py` импортировал приватные `_collect_issues`/`_summarize` | публичный `agent_coder.standard` (adapter; ядро не переписано — отдельный ACT) | **done** `40fa7aa` | arch review |
| S2/F1/D9 | major | `generate.py` перетирает ручные `## Deferred`/`## Done` | `state.json` (machine) + `notes.md` (manual), `.md` — view; либо маркеры `<!-- BEGIN manual -->` | **done** `3d21b50` (render) + `7e11186` (state) + `3171837` (CLI) + `77d9736` (TG); generate.py удалён (`3266021`) | ai_image_bot 2026-10-18 |
| S3 | major | agent-coder не знал про `_shared-dev`/`_migrations` | templates в пакете, state в `~/.agent-coder/` (шаги 2–3) | partial: templates `80c6e9f` | arch review |
| S4/F15 | major | fix-hints — текст, ACT не мог применить по id | `FixSpec` (issue_id, kind, hint) в `standard/fixes.py` | **done** `40fa7aa` | arch review |
| S5/F2 | major | `BOTS_DIR` хардкод в `generate.py` | `standard/paths.py`, env `AGENT_CODER_BOTS_DIR` | **done** `40fa7aa` | arch review |
| S6/F11 | major | bootstrap с 0 отсутствует как tool | `tools/scaffold_project.py` + CLI `scaffold` | **done** `d95afd7` (core) + `3e88e4f` (CLI) + `f7f6ce4` (/new, B) | arch review |
| S7/F14 | minor | `MANUAL_NOTES` в коде `generate.py` — второй источник | убрать, notes в `notes.md` | open | arch review |

## Стандарт и проверки

| ID | sev | what | fix | status | source |
|---|---|---|---|---|---|
| F3 | major | `ruff format` не применялся: ai_image_bot 49/66, agent-coder 74/106 | `ruff format .` отдельным style-коммитом | **done** `07923d8` (ai_image_bot), `bc9af70` (agent-coder) | drift |
| F3b | major | agent-coder сам дрейфанул на 74 файла — стандарт не поймал | см. F16 | **done** `bc9af70` | drift |
| F4/D6 | minor | `E501` в lint ignore vs `ruff format line-length=88` | убрать `E501` из ignore; либо документировать зачем | **done (documented)** `257b112` (ai_image_bot): 24 случая, 22 — string-литералы данных; ignore оставлен с обоснованием | ai_image_bot |
| F6/D1 | major | стандарт требует `license = "<SPDX>"`, про proprietary молчит | правило: closed-source → `LicenseRef-Proprietary` | **done** `b1720c7` | ai_image_bot `705054b` |
| F7/D2 | major | `_shared-dev` требует `uv`, ai_image_bot на `pip-tools`; «aiogram приватный» — неверно | `requirements-dev.in` без pip-tools, `uv pip compile --universal`, CI на uv | open | ai_image_bot |
| F8/D3 | major | `coverage source = ["."]` для flat layout; стандарт требует `["<pkg>"]` | разрешить явный список пакетов (как в `packages.find`) | open | ai_image_bot |
| F16 | major | `check_project_standard` не проверяет `ruff format --check` | новый issue id `ruff_format_not_applied`: subprocess `ruff format --check .` в base | **done** `944e30f` | agent-coder drift |
| F17 | minor | два источника шаблонов: `_shared-dev/project-template` + `agent_coder/templates` | (a) канон — `agent_coder/templates`, `_shared-dev` ссылается; или (b) `sync_templates.py` + CI diff | open | step 2 |
| F18 | minor | `ci-template.yml` — `pip install`, не `uv` | обновить шаблон на `uv pip compile --universal` (связано с F7) | **done** `6409403` | step 2 |
| F19 | minor | `pyproject-snippet.toml` содержит `source = ["app"]` placeholder | `scaffold_project` обязан заменить на имя пакета; тест scaffold проверяет отсутствие `app` | **done** `d95afd7` (scaffold заменяет; `test_pyproject_no_app_placeholder`) | step 2 |
| F23 | major | `_migrations` без git remote: трекер миграций и `notes.md` (история ручных решений) только локально | создать remote-репо (GitHub, приватный) + `git remote add` + push; либо влить `_migrations/` в agent-coder как `state/` и пушить с ним | **done** `git@github.com:Kashintsev-cmyk/migrations.git` (private), push `d500b07` | 2026-10-18 |
| F24 | minor | `_migrations/README.md` описывает старый `generate.py`; новый путь — `agent-coder-migrate --project X`, `.md` = view, `.notes.md` = manual | переписать README: usage, layout (state в ~/.agent-coder, notes в _migrations), откат | **done** `436fe91` (_migrations) | 2026-10-18 |
| F26 | minor | `check_project_standard` применяется к самому agent-coder, но tool — не проект под bots/: 4 major (нет requirements*.in, root AGENTS.md) + 3 minor, шум в PLAN/AUDIT | (a) исключить agent-coder в tool (сейчас только в `_migrations/generate.py`); (b) или документировать "tool не под стандартом" в README/AGENTS | **done** `e998305`: EXCLUDED_NAMES + score="skipped" с reason | self-check 2026-10-18 |
| F22 | minor | Model 'x-ai/grok-4.20' отсутствует в каталоге: warning при `import config` → placeholder без метаданных (pytest output, 2026-10-18) | (a) добавить модель в `_FALLBACK_CATALOG`; (b) логировать только при первом использовании, не на import; (c) убрать из .env | **done** `c720bee`: grok-4.20 в fallback (реальные данные), `_make_placeholder(warn=...)`, init тихий | pytest |
| F21 | major | локальный pytest не ловит файлы, отсутствующие в git: `templates/AGENTS.md` был на диске, но gitignored → локально 3 passed, CI 2 failed (2026-10-18) | (a) `test_templates.py` дополнительно проверяет `git ls-files agent_coder/templates/` (skip вне git-репо); (b) pre-push CI-симуляция `git clone /tmp && pytest` | **done** `5966543` (file) + `4562084` (rule) + `00885a1` (test) | CI 2026-10-18 |
| F20 | minor | `[tool.mypy] python_version = "3.10"` в agent-coder при `requires-python >=3.12` | выровнять до `"3.12"` (отдельно, mypy не в CI) | open | peek pyproject |

## Per-project (историческое, `_migrations/<project>.md`)

| ID | project | what | status |
|---|---|---|---|
| D4 | ai_image_bot | `aiogram==3.31.0` — проверить 3.32+ breaking | open (решение владельца) |
| D8 | ai_image_bot | `README.md:7` бейдж `license-MIT` vs pyproject `LicenseRef-Proprietary` | open (решение владельца) |

## Порядок работ (рекомендация)

Закрыто в этой сессии: F1/S2, F6, F11/S6, F13, F16, F21, F24 (см. статусы выше).

Остаётся (не блокирует restart ботов):
1. **F3** — `ruff format` для 8 проектов (теперь видно через F16).
2. **F17** — канон шаблонов (`_shared-dev` vs `agent_coder/templates`).
3. **D2/D8** — решения владельца (uv-lock, README badge).
