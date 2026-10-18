# FINDINGS — архитектурные находки и долги

Baseline: 2026-10-18, после self-migration `ai_image_bot` и переноса
инфраструктуры в `agent-coder`. Обновлять при закрытии/появлении пунктов.

Поля: **ID | severity | what | fix | status | source**.
Severity: major (ломает цикл/данные), minor (долг/неудобство).

## Инфраструктура агента

| ID | sev | what | fix | status | source |
|---|---|---|---|---|---|
| S1/F10 | major | `_migrations/generate.py` импортировал приватные `_collect_issues`/`_summarize` | публичный `agent_coder.standard` (adapter; ядро не переписано — отдельный ACT) | **done** `40fa7aa` | arch review |
| S2/F1/D9 | major | `generate.py` перетирает ручные `## Deferred`/`## Done` | `state.json` (machine) + `notes.md` (manual), `.md` — view; либо маркеры `<!-- BEGIN manual -->` | open | ai_image_bot 2026-10-18 |
| S3 | major | agent-coder не знал про `_shared-dev`/`_migrations` | templates в пакете, state в `~/.agent-coder/` (шаги 2–3) | partial: templates `80c6e9f` | arch review |
| S4/F15 | major | fix-hints — текст, ACT не мог применить по id | `FixSpec` (issue_id, kind, hint) в `standard/fixes.py` | **done** `40fa7aa` | arch review |
| S5/F2 | major | `BOTS_DIR` хардкод в `generate.py` | `standard/paths.py`, env `AGENT_CODER_BOTS_DIR` | **done** `40fa7aa` | arch review |
| S6/F11 | major | bootstrap с 0 отсутствует как tool | `tools/scaffold_project.py` + CLI `scaffold` | open | arch review |
| S7/F14 | minor | `MANUAL_NOTES` в коде `generate.py` — второй источник | убрать, notes в `notes.md` | open | arch review |

## Стандарт и проверки

| ID | sev | what | fix | status | source |
|---|---|---|---|---|---|
| F3 | major | `ruff format` не применялся: ai_image_bot 49/66, agent-coder 74/106 | `ruff format .` отдельным style-коммитом | **done** `07923d8` (ai_image_bot), `bc9af70` (agent-coder) | drift |
| F3b | major | agent-coder сам дрейфанул на 74 файла — стандарт не поймал | см. F16 | **done** `bc9af70` | drift |
| F4/D6 | minor | `E501` в lint ignore vs `ruff format line-length=88` | убрать `E501` из ignore; либо документировать зачем | open | ai_image_bot |
| F6/D1 | major | стандарт требует `license = "<SPDX>"`, про proprietary молчит | правило: closed-source → `LicenseRef-Proprietary` | open | ai_image_bot `705054b` |
| F7/D2 | major | `_shared-dev` требует `uv`, ai_image_bot на `pip-tools`; «aiogram приватный» — неверно | `requirements-dev.in` без pip-tools, `uv pip compile --universal`, CI на uv | open | ai_image_bot |
| F8/D3 | major | `coverage source = ["."]` для flat layout; стандарт требует `["<pkg>"]` | разрешить явный список пакетов (как в `packages.find`) | open | ai_image_bot |
| F16 | major | `check_project_standard` не проверяет `ruff format --check` | новый issue id `ruff_format_not_applied`: subprocess `ruff format --check .` в base | open | agent-coder drift |
| F17 | minor | два источника шаблонов: `_shared-dev/project-template` + `agent_coder/templates` | (a) канон — `agent_coder/templates`, `_shared-dev` ссылается; или (b) `sync_templates.py` + CI diff | open | step 2 |
| F18 | minor | `ci-template.yml` — `pip install`, не `uv` | обновить шаблон на `uv pip compile --universal` (связано с F7) | open | step 2 |
| F19 | minor | `pyproject-snippet.toml` содержит `source = ["app"]` placeholder | `scaffold_project` обязан заменить на имя пакета; тест scaffold проверяет отсутствие `app` | open | step 2 |
| F23 | major | `_migrations` без git remote: трекер миграций и `notes.md` (история ручных решений) только локально | создать remote-репо (GitHub, приватный) + `git remote add` + push; либо влить `_migrations/` в agent-coder как `state/` и пушить с ним | open | 2026-10-18 |
| F24 | minor | `_migrations/README.md` описывает старый `generate.py`; новый путь — `agent-coder-migrate --project X`, `.md` = view, `.notes.md` = manual | переписать README: usage, layout (state в ~/.agent-coder, notes в _migrations), откат | open | 2026-10-18 |
| F22 | minor | Model 'x-ai/grok-4.20' отсутствует в каталоге: warning при `import config` → placeholder без метаданных (pytest output, 2026-10-18) | (a) добавить модель в `_FALLBACK_CATALOG`; (b) логировать только при первом использовании, не на import; (c) убрать из .env | open | pytest |
| F21 | major | локальный pytest не ловит файлы, отсутствующие в git: `templates/AGENTS.md` был на диске, но gitignored → локально 3 passed, CI 2 failed (2026-10-18) | (a) `test_templates.py` дополнительно проверяет `git ls-files agent_coder/templates/` (skip вне git-репо); (b) pre-push CI-симуляция `git clone /tmp && pytest` | **partial** file+rule: `5966543` + `4562084`; test-hardening open | CI 2026-10-18 |
| F20 | minor | `[tool.mypy] python_version = "3.10"` в agent-coder при `requires-python >=3.12` | выровнять до `"3.12"` (отдельно, mypy не в CI) | open | peek pyproject |

## Per-project (историческое, `_migrations/<project>.md`)

| ID | project | what | status |
|---|---|---|---|
| D4 | ai_image_bot | `aiogram==3.31.0` — проверить 3.32+ breaking | open (решение владельца) |
| D8 | ai_image_bot | `README.md:7` бейдж `license-MIT` vs pyproject `LicenseRef-Proprietary` | open (решение владельца) |

## Порядок работ (рекомендация)

1. **F1/S2** — state/notes, идемпотентный `migrate` (следующий шаг).
2. **F16** — `ruff_format_not_applied` в tool (иначе дрейф повторится).
3. **F6, F7/F18, F8** — стандарт (closed-source license, uv, coverage flat).
4. **F11/S6** — `scaffold_project` (bootstrap с 0), проверяет F19.
5. Остальное — по мере боли; F17 — решить канон шаблонов.
