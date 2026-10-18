"""Рендер <project>.md из summary. Чистая функция, детерминированная.

Без timestamp — иначе повторный прогон migrate всегда даёт diff
(F13). Timestamp живёт в state.json, не в .md.

Ручные заметки приходят строкой (notes_md), не читаются здесь:
источник — <project>.notes.md (см. standard/notes.py), его ведёт
человек, генератор его не трогает (F1/S2).
"""

from __future__ import annotations

from typing import Any

from agent_coder.standard.fixes import fix_for


def _escape_md(s: str) -> str:
    """Экранировать обратные бэктики, чтобы не ломать code-span."""
    return s.replace("`", "\\`")


def _row(i: dict[str, Any]) -> str:
    exp = _escape_md(i["expected"])
    fnd = _escape_md(i["found"])
    line = f"- [ ] `{i['path']}` — {i['issue']}: expected `{exp}`, found `{fnd}`"
    spec = fix_for(i["issue"])
    if spec is not None:
        line += f"\n  - **fix:** {spec.hint}"
    return line


def render_markdown(
    project: str,
    summary: dict[str, Any],
    *,
    notes_md: str = "",
    standard_path: str | None = None,
) -> str:
    """Сгенерировать markdown-вид миграции. Детерминирован."""
    issues: list[dict[str, Any]] = summary["issues"]
    score = summary["score"]
    majors = summary["majors"]
    minors = summary["minors"]

    critical = [i for i in issues if i["severity"] == "major"]
    standard = [i for i in issues if i["severity"] == "minor"]

    std_line = "Standard: " + (
        standard_path
        or "/home/botuser/bots/agent-coder/agent_coder/rules/project_standard.md"
    )

    lines: list[str] = []
    lines.append(f"# Migration: {project} → bots/ standard")
    lines.append("")
    lines.append(std_line)
    lines.append(f"Score: **{score}** (majors={majors}, minors={minors})")
    lines.append("")
    lines.append(f"Manual notes: see `{project}.notes.md` (не перетирается `migrate`).")
    lines.append("")

    if critical:
        lines.append("## Critical (major — ломает CI / install / EOL)")
        lines.append("")
        for i in critical:
            lines.append(_row(i))
        lines.append("")
    else:
        lines.append("## Critical (major)")
        lines.append("")
        lines.append("- (нет)")
        lines.append("")

    if standard:
        lines.append("## Standard (minor)")
        lines.append("")
        for i in standard:
            lines.append(_row(i))
        lines.append("")
    else:
        lines.append("## Standard (minor)")
        lines.append("")
        lines.append("- (нет)")
        lines.append("")

    lines.append("## Notes (manual — проверить руками)")
    lines.append("")
    if notes_md.strip():
        lines.append(notes_md.rstrip())
    else:
        lines.append(f"- (нет заметок; добавь в `{project}.notes.md`)")
    lines.append("")

    return "\n".join(lines)
