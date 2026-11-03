"""Содержимое verify-промта: правило by-design обязано быть в стандарте.

`verify.py:168` грузит `agent_coder/rules/verify_standard.md` и подставляет
`{workspace}`. Эти тесты страхуют контракт: правило by-design нельзя
случайно удалить, плейсхолдер {workspace} не должен исчезнуть.
"""

from agent_coder.rules import load_verify_standard


def test_verify_standard_loaded_and_non_empty() -> None:
    text = load_verify_standard()
    assert text
    assert len(text) > 1000  # файл не обрезан


def test_verify_standard_has_by_design_rule() -> None:
    """Задокументированное поведение = REJECTED с evidence 'by design: <цитата>'."""
    low = load_verify_standard().lower()
    assert "by-design" in low or "by design" in low
    # где искать подтверждение by-design
    assert "readme" in low or ".env.example" in low or "docstring" in low
    # формат evidence
    assert "by design:" in low


def test_verify_standard_keeps_workspace_placeholder() -> None:
    """`verify.py:171` делает .replace("{workspace}", ...) — плейсхолдер нужен."""
    assert "{workspace}" in load_verify_standard()
