"""Сборка system prompt для режима AUDIT.

Текст аудиторского промта живёт в agent_coder/rules/audit_standard.md
(редактируется как обычный markdown, ревьюится в PR). Здесь — только
загрузка и подстановка плейсхолдеров.
"""

from __future__ import annotations

from agent_coder.rules import load_audit_standard


def _fill(template: str, **kwargs: str) -> str:
    """Подставить {name} через str.replace, а не str.format.

    str.format падает на любых других { } в тексте (например, в
    примерах кода внутри markdown). replace безопасен и предсказуем.
    """
    out = template
    for key, value in kwargs.items():
        out = out.replace("{" + key + "}", value)
    return out


def build_audit_prompt(
    workspace: str,
    project_name: str = "",
    entrypoint: str = "—",
    python_version: str = "—",
    dependencies: str = "—",
    domain: str = "—",
    requirements: str = "—",
    priorities: str = "—",
) -> str:
    template = load_audit_standard()
    return _fill(
        template,
        workspace=workspace,
        project_name=project_name or workspace.rsplit("/", 1)[-1],
        entrypoint=entrypoint,
        python_version=python_version,
        dependencies=dependencies,
        domain=domain,
        requirements=requirements,
        priorities=priorities,
    )
