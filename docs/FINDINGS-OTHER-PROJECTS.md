# FINDINGS — другие проекты (staging, перенесено 2026-11-06)

Перенесено из `agent-coder/docs/FINDINGS.md`. Относится к **другим** проектам.
Действие: при работе над проектом — скопировать строку в `<project>/.audit-reference/FINDINGS.md` и удалить из этого файла.

| ID | sev | what | fix | status | source |
|---|---|---|---|---|---|
| F8/D3 | major | `coverage source = ["."]` для flat layout; стандарт требует `["<pkg>"]` | разрешить явный список пакетов (как в `packages.find`) | open | ai_image_bot |
| F28 | minor | `builder_bot`: UP046 в `app/calculators/base.py` — `class CalcResult(BaseModel, Generic[ValuesT])` vs PEP 695. ruff fix помечен **unsafe** (меняет runtime generic pydantic) | снять per-file-ignore в `builder_bot/pyproject.toml` после миграции на `class CalcResult[ValuesT: BaseModel](BaseModel)` + прогон 525 тестов. Не блокирует standard | open | builder_bot 2026-10-18 |
| F29 | major | `futures_3.0`: src-layout (`src/trading/`) без корневых `pyproject.toml`, `README.md`, `tests/`, `requirements*.in`; `D .cursorrules`, `?? .promt` в git status | (a) поднять корневые pyproject/README/tests, код оставить в `src/`; (b) признать особым случаем и не применять стандарт. Решение владельца — структурный ACT | open | 2026-10-18 |
