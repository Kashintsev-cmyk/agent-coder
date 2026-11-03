"""Guard: env-дефолты в коде agent_coder обязаны совпадать с .env.example.

Парсим исходники через ast, а не импортируем: Config читает переменные
в момент объявления класса, и на машине с заполненным .env тест видел бы
локальные оверрайды, а не дефолты кода.

Покрыто: 1) прямой os.getenv("VAR", "literal");
2) хелперы вида def _env_int(key, default): ... os.getenv(key, default) —
дефолт берём с места вызова хелпера.
Не покрыто (осознанно): динамические имена os.getenv(f"TOOL_MAX_CHARS_..."),
os.environ.get с именем-константой в standard/.
"""

import ast
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
PKG = ROOT / "agent_coder"
CONFIG_PY = PKG / "config.py"
ENV_EXAMPLE = ROOT / ".env.example"

# Переменные, которые сознательно не документируем в .env.example
ALLOWED_NOT_IN_EXAMPLE: set[str] = {"SESSIONS_DIR"}

# Дефолт вычисляется в рантайме, сравнивать не с чем
COMPUTED_DEFAULTS: set[str] = {"CURATED_MODEL_IDS", "MODELS_CACHE_DIR"}


def _is_os_getenv(node: ast.AST) -> bool:
    return (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "getenv"
        and isinstance(node.func.value, ast.Name)
        and node.func.value.id == "os"
    )


def _default_of(call: ast.Call) -> str | None:
    """str-дефолт из второго аргумента; нет или не константа -> None."""
    if len(call.args) > 1 and isinstance(call.args[1], ast.Constant):
        return str(call.args[1].value)
    return None


def _direct_getenv_defaults(tree: ast.Module) -> dict[str, str | None]:
    out: dict[str, str | None] = {}
    for node in ast.walk(tree):
        if not _is_os_getenv(node):
            continue
        if not (
            node.args
            and isinstance(node.args[0], ast.Constant)
            and isinstance(node.args[0].value, str)
        ):
            continue
        out.setdefault(node.args[0].value, _default_of(node))
    return out


def _helper_names(tree: ast.Module) -> set[str]:
    """Функции, пробрасывающие первый аргумент в os.getenv(key, ...)."""
    names: set[str] = set()
    for fn in ast.walk(tree):
        if (
            not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef))
            or not fn.args.args
        ):
            continue
        first = fn.args.args[0].arg
        for sub in ast.walk(fn):
            if (
                _is_os_getenv(sub)
                and sub.args
                and isinstance(sub.args[0], ast.Name)
                and sub.args[0].id == first
            ):
                names.add(fn.name)
                break
    return names


def _helper_call_defaults(tree: ast.Module, helpers: set[str]) -> dict[str, str | None]:
    out: dict[str, str | None] = {}
    for node in ast.walk(tree):
        if not (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id in helpers
            and node.args
            and isinstance(node.args[0], ast.Constant)
            and isinstance(node.args[0].value, str)
        ):
            continue
        out.setdefault(node.args[0].value, _default_of(node))
    return out


def _collect_code_defaults() -> dict[str, str | None]:
    defaults: dict[str, str | None] = {}
    sources = [CONFIG_PY, *(p for p in sorted(PKG.rglob("*.py")) if p != CONFIG_PY)]
    for py in sources:
        tree = ast.parse(py.read_text(encoding="utf-8"))
        helpers = _helper_names(tree)
        for var, d in _direct_getenv_defaults(tree).items():
            defaults.setdefault(var, d)
        for var, d in _helper_call_defaults(tree, helpers).items():
            defaults.setdefault(var, d)
    return defaults


@pytest.fixture(scope="session")
def code_defaults() -> dict[str, str | None]:
    return _collect_code_defaults()


@pytest.fixture(scope="session")
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
    assert not unknown, (
        f".env.example содержит переменные, которые код не читает: {unknown}"
    )


def test_code_vars_are_documented(
    code_defaults: dict[str, str | None],
    example_values: dict[str, str],
) -> None:
    undocumented = sorted(
        var
        for var in code_defaults
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
        and var not in COMPUTED_DEFAULTS
        and example_values[var] != default
    }
    assert not mismatch, "Дефолты кода разошлись с .env.example:\n" + "\n".join(
        f"  {k}: код={v[0]!r}, .env.example={v[1]!r}" for k, v in mismatch.items()
    )
