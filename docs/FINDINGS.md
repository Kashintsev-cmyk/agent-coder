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

## Агентный цикл и память

| ID | sev | what | fix | status | source |
|---|---|---|---|---|---|
| A1 | major | tool-выводы 85–95% контента; худший случай — 3 062 201 симв. в одной сессии (search/grep), модель зависала; всё уезжало в LLM каждый запрос и оплачивалось | per-tool extraction (без LLM: regex + head/tail + счётчики) + полный текст в `history_archive` (search_history) + `format_for_summary` через `extract(2048)` | **done** `ff28184` (extract+loop) + `af226b5` (compression) + `5974e69` (фикстуры, агрессивные дефолты; 93.5% взвешенно, 71–79% per-fixture) | session 2026-10-19 |
| A2 | major | error-path `_append_tool_result` (`tool is None` / `not allowed` / `rejected`) не сохранял сессию на диск — только success-path | `save()` внутрь `_append_tool_result` (одна точка, покрывает все 4 call-site) | **done** `ff28184` | session 2026-10-19 |
| A3 | major | `run_command` с heredoc (`python3 - <<'PYEOF'`): `exit code: N` на строке 50+, форматер сохранял только первые 2 строки → терялся. Найдено на реальных фикстурах, синтетика не ловила | `exit code` regex всегда; `$ cmd` обрезается до 200 симв. (`[команда сокращена]`); stdout/stderr маркеры сохраняются | **done** `5974e69` | session 2026-10-19 |
| A4 | minor | `git_diff` режет тело через `_truncate(MAX_OUTPUT)` **до** extract → `+N -M` недосчитывает на огромных diff'ах | точные числа в `history_archive` (search_history); docstring предупреждает | **open** (приемлемо) | session 2026-10-19 |
| A5 | major | модель «искала заново» после compress: детали теряются LLM-суммаризацией, structured memory нет. Подтверждено на реальных сессиях (галлюцинации, повторный поиск) | `Session.work_state` (user_requests/findings/done/todo/open_questions) + tool `update_work_state` (точечный patch: set/+/−) + `[WORK_STATE]` в system prompt **каждую итерацию** + правило 14 в `SYSTEM_ACT` | **done** `f3e879e` | session 2026-10-19 |
| A6 | major | `compress()` терял факты: LLM-narrative + нет детерминированного скелета; work_state-факты, которых нет в transcript, ложно помечались `(не подтверждено)` | work_state как скелет: `[ИЗВЕСТНЫЕ ФАКТЫ]` для LLM + детерминированный suffix после валидатора (не LLM-генерирован); `_validate_summary(extra_known=...)` | **done** `d102286` | session 2026-10-19 |

## Стандарт и проверки

| ID | sev | what | fix | status | source |
|---|---|---|---|---|---|
| F3 | major | `ruff format` не применялся: ai_image_bot 49/66, agent-coder 74/106, rectifyme_bot 134, builder_bot 82 | `ruff format .` отдельным style-коммитом | **done**: ai_image_bot `07923d8`, agent-coder `bc9af70`, rectifyme_bot `a8facd4` (134 файла), builder_bot `a8facd4`-серия (82 файла). **open**: futures_3.0 (280, src-layout — отдельно), ai_bot_2.0 (43, deprecated), crypto_bot (11, deprecated) | drift |
| F3b | major | agent-coder сам дрейфанул на 74 файла — стандарт не поймал | см. F16 | **done** `bc9af70` | drift |
| F4/D6 | minor | `E501` в lint ignore vs `ruff format line-length=88` | убрать `E501` из ignore; либо документировать зачем | **done (documented)** `257b112` (ai_image_bot): 24 случая, 22 — string-литералы данных; ignore оставлен с обоснованием | ai_image_bot |
| F6/D1 | major | стандарт требует `license = "<SPDX>"`, про proprietary молчит | правило: closed-source → `LicenseRef-Proprietary` | **done** `b1720c7` | ai_image_bot `705054b` |
| F7/D2 | major | `_shared-dev` требует `uv`, ai_image_bot на `pip-tools`; «aiogram приватный» — неверно | `requirements-dev.in` без pip-tools, `uv pip compile --universal`, CI на uv | **done** `1a35b9c` (lock) + `badbf65` (CI) | ai_image_bot |
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
| F20 | minor | `[tool.mypy] python_version = "3.10"` в agent-coder при `requires-python >=3.12` | выровнять до `"3.12"` | **done** (отдельный коммит вместе с этим патчем FINDINGS) | peek pyproject |

| F28 | minor | `builder_bot`: UP046 в `app/calculators/base.py` — `class CalcResult(BaseModel, Generic[ValuesT])` vs PEP 695. ruff fix помечен **unsafe** (меняет runtime generic pydantic) | снять per-file-ignore в `builder_bot/pyproject.toml` после миграции на `class CalcResult[ValuesT: BaseModel](BaseModel)` + прогон 525 тестов. Не блокирует standard | open | builder_bot 2026-10-18 |
| F29 | major | `futures_3.0`: src-layout (`src/trading/`) без корневых `pyproject.toml`, `README.md`, `tests/`, `requirements*.in`; `D .cursorrules`, `?? .promt` в git status | (a) поднять корневые pyproject/README/tests, код оставить в `src/`; (b) признать особым случаем и не применять стандарт. Решение владельца — структурный ACT | open | 2026-10-18 |

## Per-project (историческое, `_migrations/<project>.md`)

| ID | project | what | status |
|---|---|---|---|
| D4 | ai_image_bot | `aiogram==3.31.0` — проверить 3.32+ breaking | open (решение владельца) |
| D8 | ai_image_bot | `README.md:7` бейдж `license-MIT` vs pyproject `LicenseRef-Proprietary` | **done** `c73ffb5` (owner: proprietary) |

## Порядок работ (рекомендация)

Закрыто в этой сессии: F1/S2, F4/D6, F6, F7/D2, F11/S6, F13, F16, F21, F22, F23, F24, F26, F27; D8 (см. статусы выше).

Остаётся (не блокирует restart ботов):
1. **F29** — `futures_3.0`: src-layout, нет корневых pyproject/README/tests. Решение: поднять корень или особый случай (структурный ACT).
2. **F17** — канон шаблонов (`_shared-dev` vs `agent_coder/templates`).
3. **F3** — `ruff format`: futures_3.0 (280), ai_bot_2.0 (43, deprecated), crypto_bot (11, deprecated).
4. **F28** — UP046 в `builder_bot` (PEP 695 generics + pydantic, unsafe fix).

Закрыто в сессии 2026-10-19 (агентный цикл и память): A1 (extraction), A2 (save в error-path), A3 (heredoc exit code), A5 (work_state), A6 (work_state как скелет compress). Открыто: A4 (git_diff truncation, приемлемо — точные числа в history_archive).
