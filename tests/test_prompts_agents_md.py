"""Сквозные тесты: AGENTS.md попадает в system_prompt для PLAN/ACT."""

from __future__ import annotations

from pathlib import Path

import pytest

from agent_coder.agent import workspace_rules
from agent_coder.agent.prompts import (
    SYSTEM_ACT,
    SYSTEM_PLAN,
    Mode,
    system_prompt,
)
from agent_coder.agent.workspace_rules import (
    MAX_AGENTS_MD_BYTES,
    SECTION_FOOTER,
    SECTION_HEADER,
    TRUNCATION_MARKER,
    clear_cache,
)
from agent_coder.rules import append_command_style


@pytest.fixture(autouse=True)
def _clean_cache():
    clear_cache()
    yield
    clear_cache()


def _write(tmp_path: Path, text: str) -> None:
    (tmp_path / "AGENTS.md").write_text(text, encoding="utf-8")


# (а) есть AGENTS.md — содержимое в промте PLAN, ACT
@pytest.mark.parametrize("mode", [Mode.PLAN, Mode.ACT])
def test_agents_md_added_to_prompt(tmp_path: Path, mode: Mode) -> None:
    _write(tmp_path, "use ruff and pytest")
    out = system_prompt(mode, str(tmp_path))
    assert "use ruff and pytest" in out
    assert SECTION_HEADER.strip() in out
    assert SECTION_FOOTER.strip() in out


# (б) нет файла — промт как раньше, байт-в-байт
@pytest.mark.parametrize(
    "mode,template",
    [
        (Mode.PLAN, SYSTEM_PLAN),
        (Mode.ACT, SYSTEM_ACT),
    ],
)
def test_no_rules_backward_compat(
    tmp_path: Path,
    mode: Mode,
    template: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Без AGENTS.md и builtin project standard всё равно есть command style."""
    from agent_coder.agent.prompts import _WORK_STATE_RULE
    from agent_coder.rules import append_command_style

    monkeypatch.setattr(workspace_rules, "append_builtin_standard", lambda p: p)
    ws = str(tmp_path)
    expected = template.format(workspace=ws)
    expected = expected + "\n\n" + _WORK_STATE_RULE
    expected = append_command_style(expected)
    assert system_prompt(mode, ws) == expected


def test_builtin_standard_present_when_no_agents_md(tmp_path: Path) -> None:
    ws = str(tmp_path)
    out = system_prompt(Mode.PLAN, ws)
    assert "--- PROJECT STANDARD (builtin) ---" in out
    assert "Стандарт оформления" in out


def test_builtin_standard_absent_when_agents_md_present(tmp_path: Path) -> None:
    _write(tmp_path, "project-specific rule")
    ws = str(tmp_path)
    out = system_prompt(Mode.PLAN, ws)
    assert "--- PROJECT STANDARD (builtin) ---" not in out
    assert "project-specific rule" in out
    assert "--- COMMAND STYLE (builtin) ---" in out
    assert "собери минимально необходимый контекст" in out


# (в) файл больше лимита — обрезан + маркер, сквозь промт
def test_truncated_through_prompt(tmp_path: Path) -> None:
    _write(tmp_path, "a" * (MAX_AGENTS_MD_BYTES + 1))
    ws = str(tmp_path)
    base = SYSTEM_PLAN.format(workspace=ws)
    out = system_prompt(Mode.PLAN, ws)
    assert TRUNCATION_MARKER.strip() in out
    assert SECTION_HEADER.strip() in out
    assert SECTION_FOOTER.strip() in out
    assert "a" * MAX_AGENTS_MD_BYTES in out
    assert "a" * (MAX_AGENTS_MD_BYTES + 1) not in out
    from agent_coder.agent.prompts import _WORK_STATE_RULE

    upper = (
        len(base.encode("utf-8"))
        + len(SECTION_HEADER)
        + MAX_AGENTS_MD_BYTES
        + len(TRUNCATION_MARKER)
        + len(SECTION_FOOTER)
        + len(("\n\n" + _WORK_STATE_RULE).encode("utf-8"))
        + len("\n\n--- COMMAND STYLE (builtin) ---\n\n")
        + len("\n--- end COMMAND STYLE ---\n")
        + len(
            (Path(__file__).parents[1] / "agent_coder/rules/command_style.md")
            .read_text(encoding="utf-8")
            .rstrip()
            .encode("utf-8")
        )
    )
    assert len(out.encode("utf-8")) <= upper


# AUDIT не подмешивает AGENTS.md, но получает общий командный стандарт.
def test_audit_ignores_agents_md_and_has_command_style(tmp_path: Path) -> None:
    _write(tmp_path, "SECRET_RULE_XYZ")
    ws = str(tmp_path)
    from agent_coder.agent.audit_prompt import build_audit_prompt
    from agent_coder.agent.prompts import _WORK_STATE_RULE
    from agent_coder.rules import append_command_style

    expected = append_command_style(build_audit_prompt(workspace=ws))
    assert system_prompt(Mode.AUDIT, ws) == expected + "\n\n" + _WORK_STATE_RULE
    assert "SECRET_RULE_XYZ" not in system_prompt(Mode.AUDIT, ws)
    assert "сохраняй и проверяй её exit code отдельно" in system_prompt(Mode.AUDIT, ws)


# (д) падение workspace_rules -> откат на base, для PLAN/ACT
@pytest.mark.parametrize(
    "mode,template",
    [
        (Mode.PLAN, SYSTEM_PLAN),
        (Mode.ACT, SYSTEM_ACT),
    ],
)
def test_failure_falls_back_to_base(
    tmp_path: Path,
    mode: Mode,
    template: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _write(tmp_path, "x")

    def boom(*_a: object, **_kw: object) -> str:
        raise RuntimeError("boom")

    monkeypatch.setattr(workspace_rules, "append_agents_md", boom)
    ws = str(tmp_path)
    out = system_prompt(mode, ws)
    from agent_coder.agent.prompts import _WORK_STATE_RULE

    expected = template.format(workspace=ws)
    expected = expected + "\n\n" + _WORK_STATE_RULE
    expected = append_command_style(expected)
    assert out == expected


def test_work_state_rule_is_shared_across_modes() -> None:
    """Единое правило structured memory инжектится в PLAN/ACT/AUDIT."""
    from agent_coder.agent.prompts import _WORK_STATE_RULE

    assert "update_work_state" in _WORK_STATE_RULE
    for mode in (Mode.PLAN, Mode.ACT, Mode.AUDIT):
        assert "update_work_state" in system_prompt(mode, "/tmp")


def test_plan_work_state_rule_is_for_substantial_unfinished_plans() -> None:
    from agent_coder.agent.prompts import _WORK_STATE_RULE

    assert (
        "короткие исследовательские запросы не требуют записи `todo`"
        in _WORK_STATE_RULE
    )
    assert "существенный план" in SYSTEM_PLAN
    assert "незавершённой многошаговой задачи" in _WORK_STATE_RULE
    assert "Не записывай каждый промежуточный вывод" in _WORK_STATE_RULE


def test_plan_response_format_matches_user_request() -> None:
    assert "для исследования — выводы" in SYSTEM_PLAN
    assert "со ссылками на код" in SYSTEM_PLAN
    assert "только если пользователь" in SYSTEM_PLAN
    assert "В конце выдай пронумерованный план" not in SYSTEM_PLAN


def test_system_act_no_duplicate_rule() -> None:
    """Правило в _WORK_STATE_RULE, не в SYSTEM_ACT (один источник)."""
    from agent_coder.agent.prompts import SYSTEM_ACT

    assert "update_work_state" not in SYSTEM_ACT


def test_agents_md_fits_prompt_limit_without_truncation(tmp_path: Path) -> None:
    content = "project-specific rules" * 10
    agents_md = tmp_path / "AGENTS.md"
    agents_md.write_text(content, encoding="utf-8")
    assert len(content.encode("utf-8")) <= MAX_AGENTS_MD_BYTES

    out = system_prompt(Mode.ACT, str(tmp_path))
    assert TRUNCATION_MARKER.strip() not in out
    assert content in out


def test_workspace_command_rules_are_canonical() -> None:
    command_style = (
        Path(__file__).parents[1] / "agent_coder/rules/command_style.md"
    ).read_text(encoding="utf-8")
    assert "собери минимально необходимый контекст" in command_style
    assert "сохраняй и проверяй её exit code отдельно" in command_style
    assert "Не теряй ошибки за pipe" in command_style
    assert "Сначала сохрани статус команды" in command_style
    assert "Не объединяй команды для обхода разрешений" in command_style


def test_command_style_defines_adaptive_project_research() -> None:
    command_style = " ".join(
        (Path(__file__).parents[1] / "agent_coder/rules/command_style.md")
        .read_text(encoding="utf-8")
        .split()
    )
    assert "независимо от наличия" in command_style
    assert "Если пользователь указал конкретный файл" in command_style
    assert "не делай полный обзор репозитория" in command_style
    assert "Если scope неясен" in command_style
    assert "Не выводи полное дерево" in command_style
    assert "Читай их выборочно" in command_style
    assert "изучай только относящиеся к задаче участки" in command_style
    assert "Группируй независимые безопасные поиски и чтения" in command_style
    assert "После изменений выбери проверки по scope" in command_style
    assert "Не требуй читать все Markdown-файлы" in command_style


@pytest.mark.parametrize("mode", [Mode.PLAN, Mode.ACT, Mode.AUDIT])
def test_builtin_command_style_is_available_without_agents_md(
    tmp_path: Path, mode: Mode
) -> None:
    assert not (tmp_path / "AGENTS.md").exists()
    out = system_prompt(mode, str(tmp_path))
    assert "--- COMMAND STYLE (builtin) ---" in out
    assert "Если пользователь указал конкретный файл" in out


def test_system_act_references_builtin_command_rules() -> None:
    assert "Следуй встроенному командному стилю" in SYSTEM_ACT
    assert (
        "командному стилю workspace и правилам проверок из `AGENTS.md`"
        not in SYSTEM_ACT
    )
    assert "объединяй независимые чтения и поиски" not in SYSTEM_ACT
    assert "exit code" not in SYSTEM_ACT
    assert "ruff check" not in SYSTEM_ACT


def test_system_act_protects_user_changes_and_limits_git_publication() -> None:
    assert "проверь имеющиеся незакоммиченные правки и учитывай их" in SYSTEM_ACT
    assert (
        "Не перезаписывай, не откатывай и не удаляй пользовательские изменения"
        in SYSTEM_ACT
    )
    assert "если пользователь явно этого не попросил" in SYSTEM_ACT


def test_system_act_clarifies_high_impact_ambiguity() -> None:
    assert "существенно влияет на результат, данные, безопасность" in SYSTEM_ACT
    assert (
        "Для безопасного,\n   обратимого выбора прими совместимое решение" in SYSTEM_ACT
    )
    assert "укажи допущение в отчёте" in SYSTEM_ACT
