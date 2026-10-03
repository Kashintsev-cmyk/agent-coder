"""Аудит Python-проектов: бесплатные проверки (без LLM).

Все команды безопасны (read-only) и работают на сервере. Если инструмент
не установлен — вернётся понятное сообщение.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class AuditOp:
    id: str
    label: str
    cmd: str
    description: str


# {workspace} — единственный допустимый плейсхолдер.
# Прочие {} и {20,} — литералы shell. НЕ применять str.format() к этим строкам.
AUDIT_OPS: list[AuditOp] = [
    AuditOp(
        "ruff",
        "🎨 Ruff (стиль)",
        "cd {workspace} && (ruff check . "
        "--exclude .venv --exclude venv --exclude .backups "
        "--exclude __pycache__ --exclude .git "
        "2>&1 || true) | head -60",
        "Линтер: ошибки, неиспользуемые импорты, стиль",
    ),
    AuditOp(
        "ruff_format",
        "🎨 Ruff (формат)",
        "cd {workspace} && (ruff format --check "
        "--exclude .venv --exclude venv --exclude .backups "
        "--exclude __pycache__ --exclude .git "
        "--diff . 2>&1 || true) | head -60",
        "Проверка форматирования (без изменений)",
    ),
    AuditOp(
        "bandit",
        "🔒 Bandit",
        "cd {workspace} && (bandit -r . "
        "-x .venv,venv,.backups,__pycache__,.git "
        "-q -ll 2>&1 || true) | head -60",
        "Поиск уязвимостей в коде (SQLi, shell injection и т.д.)",
    ),
    AuditOp(
        "pip_audit",
        "📦 pip-audit",
        "cd {workspace} && (pip-audit -r requirements.txt 2>&1 || pip-audit 2>&1 || true) | head -50",
        "Уязвимые зависимости",
    ),
    AuditOp(
        "outdated",
        "📦 Устаревшие пакеты",
        "cd {workspace} && (pip list --outdated 2>&1 || true) | head -40",
        "Пакеты, у которых есть новые версии",
    ),
    AuditOp(
        "mypy",
        "🧠 Mypy",
        "cd {workspace} && (mypy . --exclude '.venv' --exclude 'venv' "
        "--exclude '.backups' --no-error-summary 2>&1 || true) | head -50",
        "Проверка типов",
    ),
    AuditOp(
        "vulture",
        "💀 Vulture",
        "cd {workspace} && (vulture . "
        "--exclude '.venv,venv,.backups,__pycache__,.git' "
        "--min-confidence 80 2>&1 || true) | head -40",
        "Мёртвый код",
    ),
    AuditOp(
        "secrets",
        "🔑 Секреты",
        "cd {workspace} && (grep -rE '(api[_-]?key|secret|token|password)\\s*=\\s*[\"\\x27][A-Za-z0-9_\\-]{20,}' "
        "--include='*.py' --include='*.env' "
        "--exclude-dir=.venv --exclude-dir=venv --exclude-dir=.backups "
        ". 2>/dev/null || echo 'не найдено') | head -20",
        "Поиск захардкоженных секретов (эвристика)",
    ),
    AuditOp(
        "git_env",
        "📜 .env в истории",
        "cd {workspace} && (git log --all --full-history -- .env 2>&1 | head -10 || echo 'чисто')",
        "Попал ли .env в git-историю (важно!)",
    ),
    AuditOp(
        "tests",
        "🧪 Тесты",
        "cd {workspace} && (pytest --collect-only -q 2>&1 || true) | tail -10",
        "Сколько тестов собирается",
    ),
    AuditOp(
        "tree",
        "🌳 Структура",
        "cd {workspace} && (find . -type f -name '*.py' "
        "-not -path './.venv/*' -not -path './venv/*' -not -path './.backups/*' | head -40)",
        "Список Python-файлов",
    ),
    AuditOp(
        "loc",
        "📏 Размер кода",
        "cd {workspace} && (find . -name '*.py' "
        "-not -path './.venv/*' -not -path './venv/*' -not -path './.backups/*' "
        "-exec wc -l {} + 2>/dev/null | tail -1)",
        "Строк Python-кода",
    ),
]


AUDIT_GROUPS: list[tuple[str, str]] = [
    ("codetypes",   "🎨 Код и типы"),
    ("security",    "🔒 Безопасность"),
    ("deps",        "📦 Зависимости"),
    ("testsmetr",   "🧪 Тесты и метрики"),
]

_GROUP_BY_ID: dict[str, str] = {
    "ruff":        "codetypes",
    "ruff_format": "codetypes",
    "mypy":        "codetypes",
    "vulture":     "codetypes",
    "bandit":      "security",
    "secrets":     "security",
    "git_env":     "security",
    "pip_audit":   "deps",
    "outdated":    "deps",
    "tests":       "testsmetr",
    "tree":        "testsmetr",
    "loc":         "testsmetr",
}


def op_group(op_id: str) -> str:
    """Группа op'а. По умолчанию 'codetypes' (не падаем на неизвестном)."""
    return _GROUP_BY_ID.get(op_id, "codetypes")


def ops_in_group(group: str) -> list[AuditOp]:
    """Оп'ы одной группы в порядке AUDIT_OPS."""
    return [op for op in AUDIT_OPS if op_group(op.id) == group]


def find_op(op_id: str) -> AuditOp | None:
    for op in AUDIT_OPS:
        if op.id == op_id:
            return op
    return None
