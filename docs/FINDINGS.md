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
| S7/F14 | minor | `MANUAL_NOTES` в `generate.py` — второй источник | убрать, notes в `notes.md` | **closed** (MANUAL_NOTES не найден в коде; единый источник — `standard/notes.py` → `<project>.notes.md`) | arch review |

## Агентный цикл и память

| ID | sev | what | fix | status | source |
|---|---|---|---|---|---|
| A1 | major | tool-выводы 85–95% контента; худший случай — 3 062 201 симв. в одной сессии (search/grep), модель зависала; всё уезжало в LLM каждый запрос и оплачивалось | per-tool extraction (без LLM: regex + head/tail + счётчики) + полный текст в `history_archive` (search_history) + `format_for_summary` через `extract(2048)` | **done** `ff28184` (extract+loop) + `af226b5` (compression) + `5974e69` (фикстуры, агрессивные дефолты; 93.5% взвешенно, 71–79% per-fixture) | session 2026-10-19 |
| A2 | major | error-path `_append_tool_result` (`tool is None` / `not allowed` / `rejected`) не сохранял сессию на диск — только success-path | `save()` внутрь `_append_tool_result` (одна точка, покрывает все 4 call-site) | **done** `ff28184` | session 2026-10-19 |
| A3 | major | `run_command` с heredoc (`python3 - <<'PYEOF'`): `exit code: N` на строке 50+, форматер сохранял только первые 2 строки → терялся. Найдено на реальных фикстурах, синтетика не ловила | `exit code` regex всегда; `$ cmd` обрезается до 200 симв. (`[команда сокращена]`); stdout/stderr маркеры сохраняются | **done** `5974e69` | session 2026-10-19 |
| A4 | minor | `git_diff` режет тело через `_truncate(MAX_OUTPUT)` **до** extract → `+N -M` недосчитывает на огромных diff'ах | детект маркера `_truncate` → header помечается «неполные: точные — search_history»; целый diff — без пометки. На реальных фикстурах усечение не воспроизводится (страховка) | **done** (отдельный коммит после `2776d10`) | session 2026-10-19 |
| A5 | major | модель «искала заново» после compress: детали теряются LLM-суммаризацией, structured memory нет. Подтверждено на реальных сессиях (галлюцинации, повторный поиск) | `Session.work_state` (user_requests/findings/done/todo/open_questions) + tool `update_work_state` (точечный patch: set/+/−) + `[WORK_STATE]` в system prompt **каждую итерацию** + правило 14 в `SYSTEM_ACT` | **done** `f3e879e` | session 2026-10-19 |
| A6 | major | `compress()` терял факты: LLM-narrative + нет детерминированного скелета; work_state-факты, которых нет в transcript, ложно помечались `(не подтверждено)` | work_state как скелет: `[ИЗВЕСТНЫЕ ФАКТЫ]` для LLM + детерминированный suffix после валидатора (не LLM-генерирован); `_validate_summary(extra_known=...)` | **done** `d102286` | session 2026-10-19 |
| A7 | major | AUDIT не видел work_state: `system_prompt()` делал ранний return `build_audit_prompt()` до инъекции `[WORK_STATE]`, правила про `update_work_state` не было в `audit_standard.md`. На живой аудит-сессии `93108d9a` — 11 сжатий, `work_state_items=0` (самый дорогой режим: 300 итераций × 3 автопродолжения) | единое правило `_WORK_STATE_RULE` (одна константа, не копии) инжектится в PLAN/ACT/AUDIT + блок `[WORK_STATE]`; из `SYSTEM_ACT` правило 14 убрано (один источник); AUDIT без `_with_rules` (вариант a) | **done** `0ed979b` | session 2026-10-19 |
| A8 | major | три риска после A1–A7: (1) `MIN_OLD_SHARE` блокировал сжатие при огромном recent (несколько `read_file` по 80–200 КБ) → old копится, loop может упереться; (2) `read_file.MAX_BYTES=200k` → одно чтение ~51k токенов (85% soft); (3) `MAX_ITERATIONS_AUDIT=300 × 4 = 1200` итераций за запуск — дорого | (1) аварийный режим: total > soft×2 И old ≥ 3000 → сжимать принудительно; (2) MAX_BYTES 200k→80k (~20k токенов); (3) AUDIT 300→200 (800 макс) | **done** `3d1d70f` + `a499783` + `983560b` | session 2026-10-19 |
| A9 | major | аудит `93108d9a` сжёг ~1000₽ с нулевым результатом: (1) loop на `read_file` — watchdog по ошибкам молчал (результат «успешный»); (2) ротация моделей при 429/timeout множила расход (итерация × 3 модели); (3) не было потолка трат | (1) бюджетные cap `MAX_COST_PER_TASK=200₽` / `MAX_COST_PER_SESSION=300₽`; (2) ротация убрана: 429/timeout → стоп `provider_error` (сессия сохранена, продолжение кнопкой), 402 → `LLMError(retryable=False)`; (3) watchdog «нет прогресса» `MAX_ITERATIONS_NO_PROGRESS=10` (прогресс = изменение work_state или успешный мутирующий tool) | **done** (коммит в сессии 2026-10-19) | session 2026-10-19 |
| A10 | major | рассинхрон UI и истории при смене режима: `/mode audit` менял только `session.mode`, `session.model` оставался deepseek-v4-flash; UI показывал deepseek, роутер при запуске подменял на kimi. Плюс `Session.new(model=MODEL_MAIN)` при создании AUDIT-сессии сразу ставил coding-модель | `config.RECOMMENDED_MODEL_BY_MODE` (рекомендация, не ограничение) + `_apply_recommended_model` при смене режима (обе точки) + `Session.new` по режиму; `model_manual=True` не перекрывается; валидация recommended ∈ CURATED. Группы/роутер не тронуты (единый `MODEL_IDS` — отдельный ACT) | **done** (коммит в сессии 2026-10-19) | session 2026-10-19 |
| A11 | major | прогон `pytest --noconftest` (так запускает `run_checks`) не грузит `tests/conftest.py` → `SESSIONS_DIR` не изолирован → каждый прогон оставлял в боевом `sessions/` ~56 файлов (`model="m"`, `workspace="/tmp"`, `messages=[]`). Замер: 341 → 397 за один прогон; всего накопилось 66 таких файлов + 122 с пустым title | изоляция — в коде, а не только в фикстуре: `config._resolve_sessions_dir()` (SESSIONS_DIR из env → уважается; прогон pytest без env → `tempfile.mkdtemp()`; иначе `<repo>/sessions`) + `_running_under_pytest()` (маркеры читаются через `os.environ`, чтобы не попасть в `.env.example`/README) | **done** (дельта 341→397 стала 0; `tests/test_sessions_dir_guard.py`, 5 тестов) | session 2026-11-20 |
| A12 | major | `Session.save()` на «лёгкой» сессии из `list_all()`/`from_meta()` (объект без `messages`/`read_paths`/`work_state`) затирал историю файла на диске пустым списком. Следы: сессия `a6a74ebf` — `cost=75.99₽`, `messages=0`, при заполненном `work_state`; ещё 11 записей «cost>1 при request_count=0» | `Session._light` (флаг происхождения: `repr=False, compare=False, init=False`, из `to_dict()` вырезается) выставляется в `from_meta()`; `save()` на light-сессии бросает `RuntimeError` с указанием на `Session.load(id)` | **done** (`tests/test_session_light_save_guard.py`, 5 тестов) | session 2026-11-20 |
| A13 | major | `telegram_state.json` лежит в `SESSIONS_DIR` рядом с сессиями. `list_all()` считал его сессией (`glob *.json` минус `index.json`), а `rebuild()` пропускал → счётчики на диске и в индексе не сходились НИКОГДА → полная пересборка индекса (ре-парс всех файлов) при каждом `/sessions`, `/open`, `cb_sessions_page`. Замер: 0.294s на первый вызов после починки, 0.002s повторный | `session_index.NON_SESSION_FILES` + `is_session_file(name)` — единый предикат для `list_all()` и `rebuild()` (одно место, а не два разных условия) | **done** (`tests/test_session_index_non_session_files.py`, 3 теста; rebuild на каждом вызове → 0) | session 2026-11-20 |
| A14 | major | `/sessions` и пагинация (`cb_sessions`, `cb_sessions_page`) показывали **весь** каталог сессий без привязки к чату: 346 сессий = 44 страницы карусели, из них 203 — мусор от pytest (`workspace=/tmp`, `model="m"`), остальные неотличимы от своих. Юзер листал чужой каталог и попадал в пустые сессии (`messages=0`) → выглядело как «после рестарта пропала история» (`/current`, `/cost`, `/mode` при этом работали верно — они берут активную сессию из `telegram_state.json`). Данные целы: `Session.load()` по всем 346 — 0 ошибок, cost индекс/файл — 0 расхождений | владелец сессии: `Session.chat_id` (0 = неизвестен: legacy/CLI) + `Session.new(chat_id=)`, прокинут из `_create_session`; `SessionMeta.chat_id`/`project` в sidecar (`INDEX_VERSION=2` → старый индекс пересобирается сам); `list_all(chat_id=)`/`latest(chat_id=)` фильтруют, хендлеры передают `message.chat.id`; одноразовый `Session.backfill_chat_id()` на старте бота (мусор `/tmp` намеренно остаётся без владельца) + `scripts/backfill_chat_id.py` (dry-run по умолчанию). Итог: 346 → 142 своих, 44 → 18 страниц | **done** (`tests/test_session_chat_id.py`, 23 теста; полный pytest 1273 passed; ruff/bandit чисто; миграция применена на 143 сессиях) | session 2026-11-20 |
| A15 | major | 203 сессии-фикстуры pytest (`workspace=/tmp`) остались на диске после фикса A11 — файлы уже были, код их больше не создаёт. Существующий `cleanup_cheap_sessions.py` их **не берёт**: критерий там — цена, а 21 из 203 дороже 1₽, и 196 имеют непустой `work_state` (`findings: f0,f1,f2` — литералы фикстуры), т.е. попадают под «ценное, не удалять». `--no-keep-valuable` тоже не годится: он сносит и настоящие дешёвые сессии юзера | критерий — происхождение, не цена: `scripts/cleanup_scrap_sessions.py` (только `session_index.is_scrap_workspace()`, т.е. workspace в tempdir); `Session.delete()` как в UI (json, archive, audits, media, индекс); бэкап json в `sessions/.cleanup-backup-<ts>/` (откат — копирование обратно), `--dry-run`/`--no-backup`. Чистка выполнена: 203 сессии / 233 файла, осталось 143 (у всех `chat_id=201821839`), 0 битых; проверено после прогона — `sessions/*.json` без записи в индексе нет, сирот в `sessions/archive` чистка не оставила (155 сиротских `.jsonl` имеют mtime не позже 14:31, т.е. появились от удалений ДО этой сессии, все по 0–2 КБ) | **done** (`tests/test_cleanup_scrap_sessions.py`, 5 тестов; полный pytest 1281 passed; ruff чисто) | session 2026-11-20 |
| A16 | major | смена режима (`/mode`, кнопка режима в меню сессии, CLI `/plan` `/act` `/audit`) обнуляла `session.messages` — юзер терял контекст диалога, хотя режим влияет только на system prompt и набор инструментов, а system prompt пересобирается на каждом запуске (`AgentLoop._refresh_system_prompt`) | убран `session.messages = []` во всех четырёх местах (`commands.py` cmd_mode/cb_mode, `__main__.py` plan/act/audit); новое поле/флаг не вводилось — достаточно не стирать | **done** (`tests/test_mode_switch_keeps_history.py`, 3 теста: Telegram cmd, Telegram callback, CLI; проверено, что на старом коде 2 из 3 падают) | session 2026-11-20 |
| A18 | major | `/models` отдаёт АГРЕГАТ `supported_parameters` по всем провайдерам, и он может обещать параметр, которого нет ни у одного endpoint: `reasoning_effort` заявлен у всех 8 curated-моделей, а в `/models/{id}/endpoints` — 0/N у haiku/gpt-6-*/sonnet/opus, 11/49 у deepseek-flash, 1/20 у deepseek-v4-pro (`agg ⊃ union`). Код отправлял параметр безусловно (`Session.reasoning_effort="medium"` по умолчанию), UI показывал уровень, который ни на что не влияет (probe 2026-11-21: RouterAI молча игнорирует, HTTP 200, жёсткого 400 нет) | (1) `model_catalog._refresh_endpoints`/`_merge_endpoints`: capability-флаги считаются по ПЕРЕСЕЧЕНИЮ endpoint'ов (балансировщик может увести на любой), `web_search` — по объединению (важно, есть ли поиск хоть где-то); graceful failure — недоступность endpoints не роняет каталог; (2) `client._supports(model, cap)` + гейт `reasoning_effort`/`verbosity` в `_build_request_kwargs` (`None` = модель неизвестна → legacy); (3) UI: меню/команда `/reasoning` и кнопка сессии гейтятся по `reasoning_effort`, а не `reasoning` | **done** (`tests/test_model_catalog.py`, `tests/test_model_profiles.py`, `tests/test_web_search.py`) | session 2026-11-21 |
| A19 | major | Long-context тир был захардкожен под одну модель: `TIER_THRESHOLD_TOKENS=100_000`, `TIER_FACTOR=5.0` применялись ко ВСЕМ моделям. Реальные тиры разные: haiku >100K вход и выход ×5; gpt-6-luna/terra/sol >272K вход ×2, а ВЫХОД только ×1.5; у deepseek-*, sonnet-5.5, opus-5.5 тиров нет вовсе. Оценка стоимости на gpt-6* завышалась в 2.5 раза, на deepseek — в 5 раз выше реального порога | тиры парсятся из `/models/{id}/endpoints` → `variable_pricings[type=prompt-threshold]` в `ModelInfo.tier_threshold`/`tier_input_factor`/`tier_output_factor` (три состояния: `None` = endpoints нет → legacy-пара; `0` = тира нет; `>0` = порог). `compression._tier_factors()` применяет множители раздельно ко входу и выходу. `_FALLBACK_CATALOG` заполнен реальными значениями из API | **done** (`tests/test_cost_growth.py`, `tests/test_compression.py`; матрица и ловушки — `docs/ROUTERAI_API.md`) | session 2026-11-21 |
| A20 | major | сессионный «🌐 Web search» (`/web_toggle`, `session.web_search`) не работал вовсе: `_build_request_kwargs` клал `plugins` в top-level kwargs, а `AsyncCompletions.create` такого параметра не знает → `TypeError: unexpected keyword argument 'plugins'` на каждом запросе с включённым поиском. Баг маскировался тестом: `tests/test_client_kwargs_compat.py` держал `KNOWN_EXTENSIONS = {"plugins"}` с комментарием «проверено на проде» — проверка сигнатуры SDK просто исключала этот ключ из проверки. Найден живым прогоном нового инструмента `web_search`, не тестами (1360 passed) | `plugins` → `kwargs.setdefault("extra_body", {})["plugins"]` (провайдерские поля только через `extra_body`); `KNOWN_EXTENSIONS = set()` — белый список убран, теперь любой модельный kwarg проверяется по сигнатуре SDK; 3 теста в `tests/test_llm_client.py` переведены на `extra_body.plugins`. Замер: `engine=native` 20 329 input-токенов (~0.26₽), `engine=exa` 818 (~0.04₽), `web_search_options={}` 1 284 (~0.05₽) — native дороже в 16 раз при том же ответе | **done** (`tests/test_llm_client.py`, `tests/test_client_kwargs_compat.py`, `tests/test_web_search.py`; замеры — `docs/ROUTERAI_API.md` §5) | session 2026-11-21 |
| A17 | minor | `sessions/archive/` накапливает файлы, которых `Session.delete()` не видит: (а) 155 сиротских `{id}.jsonl` от сессий, удалённых до A15 — по 0–2 КБ, mtime ≤ 11-20 14:31; (б) 9 файлов `{id}_{model}.jsonl` (12 МБ) от прогонов 10-22 — `history_archive.archive_path()` строит путь только как `{id}.jsonl`, поэтому `delete()` их не берёт, а повтор вызова не идемпотентен по ним; (в) 2 не-hex файла `s1.jsonl`, `testsess.jsonl` — следы старых тестов. Плюс к этому `Session.delete()` не чистит `sessions/*.json` для id, которых нет в индексе | не чинилось: требует решения по политике (снежный ком на архив vs. отдельный `--prune-archive` в скриптах чистки). Диск не горит: 0.3 МБ сирот + 12 МБ дублей | **open** | session 2026-11-20 |

## Backlog agent-coder (B-серия)

Не баги, а упрощения/долги, отложенные осознанно. Отдельно от A (баги
агентного цикла) и F (стандарт/CI), чтобы не смешивать приоритеты.

| ID | sev | what | fix | status | source |
|---|---|---|---|---|---|
| B1 | minor | Три источника моделей: `CURATED_MODEL_IDS` (UI), `CODING_MODEL_IDS`/`AUDIT_MODEL_IDS` (роутер), `MODEL_MAIN`/`DEFAULT_MODEL_ID`. Модель без «режима» доступна в UI, но не в группе роутера | Единый `MODEL_IDS`; `RECOMMENDED_MODEL_BY_MODE` — рекомендация (не ограничение); `resolve` = recommended-first; `group_for_mode`/`default_for_mode` удалены; `test_router.py` переписан (24 → 20 тестов) | closed (`6319d38`, 5 коммитов) | session 2026-10-19 |
| B2 | minor | `session_index`: OSError в `load()` не сбрасывал `_loaded`; NaN/Inf cost ломал сортировку | `_loaded=False` + `_sessions={}`; `math.isfinite` | **closed** (`9cd079d`: +3 теста, `test_session_index.py`) | аудит 2026-10-19 |
| B3 | minor | `compression._fail_streak`/`_fail_until` — глобальные dict без блокировки | — | **wontfix** (single-loop asyncio: `_cooldown_active`/`_register_failure`/`_register_success` — sync без await, гонок нет; multi-process на одну сессию — вне дизайна) | аудит 2026-10-19 |
| B4 | minor | `Usage.cost` — float для денег. <1 коп/сессия | Decimal + миграция JSON-сессий (меняет формат) | **deferred** (не блокер; делать при появлении задачи с накоплением) | аудит 2026-10-19 |
| B5 | minor | `Session.save()` на каждую tool-итерацию + atomic replace | Отложенная запись + принудительный save на аварийных выходах | **deferred** (SSD ok; NFS/SD — отдельная задача) | аудит 2026-10-19 |

## Стандарт и проверки

| ID | sev | what | fix | status | source |
|---|---|---|---|---|---|
| F3 | major | `ruff format` не применялся: ai_image_bot 49/66, agent-coder 74/106, rectifyme_bot 134, builder_bot 82 | `ruff format .` отдельным style-коммитом | **done**: ai_image_bot `07923d8`, agent-coder `bc9af70`, rectifyme_bot `a8facd4` (134 файла), builder_bot `a8facd4`-серия (82 файла). **open**: futures_3.0 (280, src-layout — отдельно), ai_bot_2.0 (43, deprecated), crypto_bot (11, deprecated) | drift |
| F3b | major | agent-coder сам дрейфанул на 74 файла — стандарт не поймал | см. F16 | **done** `bc9af70` | drift |
| F4/D6 | minor | `E501` в lint ignore vs `ruff format line-length=88` | убрать `E501` из ignore; либо документировать зачем | **done (documented)** `257b112` (ai_image_bot): 24 случая, 22 — string-литералы данных; ignore оставлен с обоснованием | ai_image_bot |
| F6/D1 | major | стандарт требует `license = "<SPDX>"`, про proprietary молчит | правило: closed-source → `LicenseRef-Proprietary` | **done** `b1720c7` | ai_image_bot `705054b` |
| F7/D2 | major | `_shared-dev` требует `uv`, ai_image_bot на `pip-tools`; «aiogram приватный» — неверно | `requirements-dev.in` без pip-tools, `uv pip compile --universal`, CI на uv | **done** `1a35b9c` (lock) + `badbf65` (CI) | ai_image_bot |
| F16 | major | `check_project_standard` не проверяет `ruff format --check` | новый issue id `ruff_format_not_applied`: subprocess `ruff format --check .` в base | **done** `944e30f` | agent-coder drift |
| F17 | minor | два источника шаблонов | канон — `agent_coder/templates/` (scaffold читает только оттуда); `_shared-dev/project-template` — legacy-репо (weather-bot/futures_3.0), вне зоны agent-coder | **closed** (`test_templates_sync.py`; rules/project_standard.md → scaffold; SHARED_DEV_DIR удалён) | step 2 |
| F18 | minor | `ci-template.yml` — `pip install`, не `uv` | обновить шаблон на `uv pip compile --universal` (связано с F7) | **done** `6409403` | step 2 |
| F19 | minor | `pyproject-snippet.toml` содержит `source = ["app"]` placeholder | `scaffold_project` обязан заменить на имя пакета; тест scaffold проверяет отсутствие `app` | **done** `d95afd7` (scaffold заменяет; `test_pyproject_no_app_placeholder`) | step 2 |
| F23 | major | `_migrations` без git remote: трекер миграций и `notes.md` (история ручных решений) только локально | создать remote-репо (GitHub, приватный) + `git remote add` + push; либо влить `_migrations/` в agent-coder как `state/` и пушить с ним | **done** `git@github.com:Kashintsev-cmyk/migrations.git` (private), push `d500b07` | 2026-10-18 |
| F24 | minor | `_migrations/README.md` описывает старый `generate.py`; новый путь — `agent-coder-migrate --project X`, `.md` = view, `.notes.md` = manual | переписать README: usage, layout (state в ~/.agent-coder, notes в _migrations), откат | **done** `436fe91` (_migrations) | 2026-10-18 |
| F26 | minor | `check_project_standard` применяется к самому agent-coder, но tool — не проект под bots/: 4 major (нет requirements*.in, root AGENTS.md) + 3 minor, шум в PLAN/AUDIT | (a) исключить agent-coder в tool (сейчас только в `_migrations/generate.py`); (b) или документировать "tool не под стандартом" в README/AGENTS | **done** `e998305`: EXCLUDED_NAMES + score="skipped" с reason | self-check 2026-10-18 |
| F22 | minor | Model 'x-ai/grok-4.20' отсутствует в каталоге: warning при `import config` → placeholder без метаданных (pytest output, 2026-10-18) | (a) добавить модель в `_FALLBACK_CATALOG`; (b) логировать только при первом использовании, не на import; (c) убрать из .env | **done** `c720bee`: grok-4.20 в fallback (реальные данные), `_make_placeholder(warn=...)`, init тихий | pytest |
| F21 | major | локальный pytest не ловит файлы, отсутствующие в git: `templates/AGENTS.md` был на диске, но gitignored → локально 3 passed, CI 2 failed (2026-10-18) | (a) `test_templates.py` дополнительно проверяет `git ls-files agent_coder/templates/` (skip вне git-репо); (b) pre-push CI-симуляция `git clone /tmp && pytest` | **done** `5966543` (file) + `4562084` (rule) + `00885a1` (test) | CI 2026-10-18 |
| F20 | minor | `[tool.mypy] python_version = "3.10"` в agent-coder при `requires-python >=3.12` | выровнять до `"3.12"` | **done** (отдельный коммит вместе с этим патчем FINDINGS) | peek pyproject |
| F27 | major | `standard/state.py: state_dir()` пишет в `~/.agent-coder/projects`, которого не было в `ReadWritePaths=` → `ProtectHome=read-only` давал `EROFS` (errno 30) на `mkdir`; `migrate_one` не ловил `OSError`, `/migrate apply` падал исключением в `dp.errors()` вместо сообщения. State `ai_image_bot` не обновлялся с 18 окт | `ReadWritePaths=-/home/botuser/.agent-coder` в юните; `StateWriteError(RuntimeError)` с путём+причиной; `migrate_one` → `{error: state_write_failed|md_write_failed, path, reason}`; `_error_text()` в хендлере; +6 тестов | **done** | ручной аудит 2026-11-19 |


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

Закрыто в сессии 2026-10-19 (агентный цикл и память): A1–A10. A9 — защита от сжигания токенов; A10 — рекомендованная модель по режиму (рассинхрон UI/истории). Открыто: единый `MODEL_IDS` вместо групп CODING/AUDIT (архитектурный рефакторинг, отдельный ACT) — не баг, упрощение.

## B9.1 — принудительный update_work_state в AUDIT (closed 2026-10-20)

**Вердикт: PASS** на свежих сессиях, **PARTIAL** на длинных продолжениях.

- **Промпт-правило** (`audit_standard.md` + `_WORK_STATE_RULE`): работает
  в коротких сессиях. bb4d5b19 (weather-bot, свежая): findings
  0→20→24 **до** compress #3, 33 в конце.
- **Reminder N=5** (`loop.py`): сработал в bb4d5b19 (idx 7 → реакция
  idx 8, `update_work_state` 24→28). В ca53ed2f (9 compress,
  продолжение) reminder на idx 31 **проигнорирован** — известное
  ограничение для длинных сессий, не блокер.
- **Post-audit guard** (`loop.py`): не понадобился в обоих замерах
  (findings непуст к natural exit).
- **B9.1c** (`compression.py:554-559`): `work_state` детерминированно
  в suffix summary. Работает: `summary #3` (bb4d5b19) содержит
  «findings: 20 → 24», `summary #9` (ca53ed2f) — 10 findings.
  Модель после continue **не перечитывает** покрытые файлы.

**Замеры:**
| Сессия | Findings | Стоимость | Compress | Тип |
|---|---|---|---|---|
| f0fbbe9a (эталон) | 14 | 7.68₽ | 4 | продолжение |
| ca53ed2f | 16 | 22.25₽ | 9 | длинное продолжение |
| bb4d5b19 (PASS) | 33 | 12.52₽ | 3 | свежая, cache 70% |

**Ограничение для будущего (B9.1d, не блокер):** в длинных сессиях
продолжения (≥5 compress) reminder в user-message «тонет». Возможные
фиксы: N=3, или reminder в system prompt (обновляется каждый turn),
или один принудительный `update_work_state` после каждого compress.

## B9.4 — дедуп findings по «файл:строка» (closed 2026-10-20)

**Вердикт: PASS** (прямое доказательство: f0fbbe9a 25 → 18).

- `work_state.py`: `_FILE_LINE_RE` (`\S+\.\w+:\d+`), strip префиксов
  (`services/`, `app/`, `src/`, `lib/`, `core/`) — только для сравнения.
  При коллизии (норм.путь, строка) — более информативный: severity >
  длина; при равенстве — первый.
- Дедуп в `WorkStateHandle.update()` и `render_for_prompt()` (legacy).
- `normalize()` не тронут — дедуп по точному тексту остаётся.
- `config.WORK_STATE_DEDUP` (default 1, env `WORK_STATE_DEDUP=0`).

**Замеры:**

| Сессия | findings | cost | комментарий |
|---|---|---|---|
| f0fbbe9a (с дублями) | 25 → **18** | 7.68₽ | 7 дублей схлопнуто (−28%) |
| bb4d5b19 (без дублей) | 33 → 33 | 12.52₽ | safety net, не активировался |
| 3e2513be (без дублей, после B9.4) | 33 → 33 | **11.34₽** (−9.4%) | cache 77% vs 70% |

**f0fbbe9a — прямое доказательство:** 7 дублей схлопнуто, паттерн ровно
из промта (разные префиксы `services/` vs без + разные форматы:
`float для денег` vs `float для денег, BLOCKER`).

**bb4d5b19 / 3e2513be — safety net:** дублей нет, дедуп не активировался,
регресса нет. Стоимость на 3e2513be ниже (cache 77%, input 1.06M vs 1.10M)
— косвенный эффект краткого work_state в summary.

**Наблюдение (B9.5, не блокер):** `list_files` без `.gitignore`-фильтра
раздувает контекст (weather-bot: `.mypy_cache/`, `.venv310/` — 30K+
символов в одном tool-результате). Кандидат в отдельный коммит после
B9.1e/B9.2.

## B9.1e — reminder в PLAN (closed 2026-10-20)

**Вердикт: PASS** (замер 2edb9607: todo=5, 2 reminder'а, оба сработали).

PLAN — read-only, как AUDIT: модель читает 10-15 файлов, держит план
в голове, при compress теряет. B9.1 закрыл AUDIT, PLAN оставался без
защиты.

- `loop.py`: условие reminder → `Mode.AUDIT | Mode.PLAN`. Метод
  `_inject_audit_reminder` → `_inject_work_state_reminder(mode, iters)`:
  в AUDIT просит findings, в PLAN — todo.
- `prompts.py`: `_WORK_STATE_RULE` — абзац про PLAN (пиши todo по ходу,
  батч по пачке прочитанного).
- Guard (`_post_audit_guard`) — **AUDIT-only**, в PLAN не срабатывает
  (todo может быть пуст легитимно).

**Замер 2edb9607 (--plan weather-bot):**
- `todo: 5`, `done: 3`, `findings: 7` (побочно) — state записан.
- 2 reminder (idx 22, idx 42), оба модель отреагировала
  `update_work_state` (idx 23, idx 43-45).
- `summary_count: 1` (компактно), cost 4.41₽, cache 66%.
- Guard не срабатывал — правильно.

**Известное ограничение (то же, что B9.1):** в длинных продолжениях
(≥5 compress) reminder может «утонуть». B9.1d (N=3 / system-reminder)
— если понадобится, отдельная задача.

## B9.2 — /patch по confirmed findings (closed 2026-10-20, core + handler)

**Вердикт: PASS на уровне тестов и интеграции** (замер на реальных
данных — опционально, после B10).

Цепочка audit → /verify → /patch: confirmed findings из
`session.verify_results` превращаются в задачу для модели-исполнителя.

- `agent/patch.py`: `run_patch(client, session, model, registry)`,
  `PatchResult`. Свой цикл (не AgentLoop): compression не нужна,
  confirmed findings точные, read offset/limit.
- `PATCH_TOOLS` whitelist: read_file/list_files/search/write_file/
  edit_file/run_command/git_status/git_diff/git_log. **git_commit НЕ
  включён** — коммит делает пользователь после ревью.
- `_collect_confirmed`: только `status=confirmed`, дедуп по
  `(text, evidence)` — 3 /verify подряд не дают 3×patch.
- Свой watchdog: `MAX_CONSECUTIVE_ERRORS=5`. Бюджет
  `MAX_COST_PATCH=50₽`, итерации `MAX_PATCH_ITERATIONS=30`.
- `files_changed` — из tool-calls write_file/edit_file (уникальные
  пути) + `git diff --stat` через subprocess (детерминированно).
- `session.patch_results` append (как verify_results), переживает
  compress истории.
- `rules/patch_standard.md`: только confirmed, не рефактори, не трогай
  rejected/uncertain, read offset/limit, git_commit не вызывай.
- `config.py`: `PATCH_MODEL_ID` (default = `DEFAULT_MODEL_ID`),
  `MAX_COST_PATCH=50₽`, `MAX_PATCH_ITERATIONS=30`.
- Telegram: `cmd_patch` (`ops.py`), `BotCommand("patch", ...)` (меню
  22→23). CLI: `/patch` (`__main__.py`), `/help` — строка про /patch.
- `/yolo` **игнорируется** — сам вызов `/patch` = подтверждение.

**Границы:**
- Не запускается автоматически после `/verify` — явная команда.
- Только `confirmed`, не `rejected`/`uncertain`.
- `git_commit` не доступен — коммит пользователь.

**Тесты:** `tests/test_patch.py` — 7 тестов (no_confirmed, только
confirmed в task, dedup, whitelist без git_commit, files_changed,
budget_exceeded, max_iterations). 721 → 728 passed.
