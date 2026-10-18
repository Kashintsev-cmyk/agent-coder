"""CLI: agent-coder-migrate — трекер миграций проекта под стандарт bots/.

Административная операция, не tool LLM: пишет вне workspace проекта
(state в ~/.agent-coder, .md в _migrations). Запускается человеком
или через run_command в ACT (с подтверждением).

Идемпотентность: повторный прогон с теми же issues не меняет .md
(write_if_changed) и не трогает .notes.md (human-owned).
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from agent_coder.standard import (
    build_state,
    collect_issues,
    notes_dir,
    project_dir,
    read_notes,
    render_markdown,
    summarize,
    write_if_changed,
    write_state,
)


def _parse_projects(raw: list[str]) -> list[str]:
    out: list[str] = []
    for chunk in raw:
        out.extend(x.strip() for x in chunk.split(",") if x.strip())
    return out


def migrate_one(project: str, *, write: bool = True) -> dict:
    """Один проект: collect -> state -> .md. Возвращает отчёт."""
    base: Path = project_dir(project)
    if not base.is_dir():
        return {"project": project, "error": "not_a_dir", "path": str(base)}

    issues = collect_issues(base)
    summary = summarize(issues)
    state = build_state(project, summary)

    report: dict = {
        "project": project,
        "score": summary["score"],
        "majors": summary["majors"],
        "minors": summary["minors"],
    }

    if not write:
        return report

    state_file = write_state(project, state)
    report["state"] = str(state_file)

    notes_md = read_notes(project)
    md = render_markdown(project, summary, notes_md=notes_md)
    md_path = notes_dir() / f"{project}.md"
    changed = write_if_changed(md_path, md)
    report["md"] = str(md_path)
    report["md_changed"] = changed

    return report


def main() -> int:
    ap = argparse.ArgumentParser(
        prog="agent-coder-migrate",
        description=(
            "Собрать state.json и <project>.md из check_project_standard. "
            "Пишет в AGENT_CODER_STATE_DIR (default ~/.agent-coder/projects) "
            "и AGENT_CODER_NOTES_DIR (default /home/botuser/bots/_migrations). "
            "<project>.notes.md не трогает."
        ),
    )
    ap.add_argument(
        "--project",
        action="append",
        default=[],
        help="Проект (можно несколько раз или через запятую). Обязателен.",
    )
    ap.add_argument(
        "--json",
        action="store_true",
        help="Только JSON-отчёт, не писать .md/state.",
    )
    args = ap.parse_args()

    projects = _parse_projects(args.project)
    if not projects:
        ap.error("--project обязателен (без DEFAULT_PROJECTS: явный список)")

    reports: list[dict] = []
    for name in projects:
        r = migrate_one(name, write=not args.json)
        reports.append(r)
        if "error" in r:
            print(f"[{name}] ERROR: {r['error']} ({r['path']})", file=sys.stderr)
        else:
            extra = ""
            if "md_changed" in r:
                extra = (
                    " -> "
                    + r["md"]
                    + (" (changed)" if r["md_changed"] else " (unchanged)")
                )
            print(
                f"[{name}] {r['score']} "
                f"(major={r['majors']}, minor={r['minors']}){extra}",
                file=sys.stderr,
            )

    if args.json:
        print(json.dumps(reports, ensure_ascii=False, indent=2))

    return 0 if all("error" not in r for r in reports) else 1


if __name__ == "__main__":
    sys.exit(main())
