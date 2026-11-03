"""Guard: дефолты в agent_coder/config.py обязаны совпадать с .env.example.

Парсим исходник через ast, а не импортируем модуль: Config читает env
в момент объявления класса, и на машине с заполненным .env тест видел бы
не дефолты кода, а локальные оверрайды.
"""
import ast
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
CONFIG_SRC = ROOT / "agent_coder" / "config.py"
ENV_EXAMPLE = ROOT / ".env.example"

# Переменные, которые сознательно не пишем в .env.example (внутренние/служебные)
ALLOWED_NOT_IN_EXAMPLE: set[str] = set()


def _getenv_defaults(source: str) -> dict[str, str | None]:
    """{VAR: default} для каждого os.getenv('VAR', 'default') в исходнике.

    getenv без дефолта -> None (обязательная переменная, сравнивать нечего).
    Вложенные вызовы (Path(os.getenv(...))) находятся тем же walk().
    """
    defaults: dict[str, str | None] = {}
    for node in ast.walk(ast.parse(source)):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if not (
            isinstance(func, ast.Attribute)
            and func.attr == "getenv"
            and isinstance(func.value, ast.Name)
            and func.value.id == "os"
        ):
            continue
        if not (node.args and isinstance(node.args[0], ast.Constant)):
            continue
        var = node.args[0].value
        if len(node.args) > 1 and isinstance(node.args[1], ast.Constant):
            defaults[var] = node.args[1].value
        else:
            defaults.setdefault(var, None)
    return defaults


@pytest.fixture(scope="module")
def code_defaults() -> dict[str, str | None]:
    return _getenv_defaults(CONFIG_SRC.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def example_values() -> dict[str, str]:
    values: dict[str, str] = {}
    for line in ENV_EXAMPLE.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, raw = line.partition("=")
        values[key.strip()] = raw.strip()
    return values


def test_env_example_exists() -> None:
    assert ENV_EXAMPLE.exists(), (
        ".env.example отсутствует — шаг 'cp .env.example .env' в README сломан"
    )


def test_example_has_no_unknown_vars(
    code_defaults: dict[str, str | None],
    example_values: dict[str, str],
) -> None:
    unknown = sorted(set(example_values) - set(code_defaults))
    assert not unknown, f".env.example содержит переменные, которые код не читает: {unknown}"


def test_code_vars_are_documented(
    code_defaults: dict[str, str | None],
    example_values: dict[str, str],
) -> None:
    undocumented = sorted(
        var for var in code_defaults
        if var not in example_values and var not in ALLOWED_NOT_IN_EXAMPLE
    )
    assert not undocumented, (
        f"config.py читает переменные, которых нет в .env.example: {undocumented}. "
        "Добавь их туда или внеси в ALLOWED_NOT_IN_EXAMPLE."
    )


def test_defaults_match_example(
    code_defaults: dict[str, str | None],
    example_values: dict[str, str],
) -> None:
    mismatch = {
        var: (default, example_values[var])
        for var, default in sorted(code_defaults.items())
        if default is not None
        and var in example_values
        and example_values[var] != default
    }
    assert not mismatch, (
        "Дефолты config.py разошлись с .env.example:\n"
        + "\n".join(
            f"  {k}: код={v[0]!r}, .env.example={v[1]!r}" for k, v in mismatch.items()
        )
    )
    