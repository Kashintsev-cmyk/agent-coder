"""CLI: agent-coder-scaffold — bootstrap нового проекта.

Тонкий adapter над scaffold_project(). Primary UI — Telegram
(/scaffold); CLI — для скриптов, CI, отладки и восстановления.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from agent_coder.scaffold import package_name, scaffold_project
from agent_coder.standard.paths import bots_dir


def main() -> int:
    ap = argparse.ArgumentParser(
        prog="agent-coder-scaffold",
        description=(
            "Создать структуру нового Python-проекта из agent_coder/templates/. "
            "Целевой каталог: BOTS_DIR/<project> (env AGENT_CODER_BOTS_DIR) "
            "или --target. Непустой target -> ошибка, файлы не перезаписываются."
        ),
    )
    ap.add_argument("--project", required=True, help="Имя проекта (kebab/snake)")
    ap.add_argument(
        "--target",
        default=None,
        help="Целевой каталог (default: AGENT_CODER_BOTS_DIR/<project>)",
    )
    ap.add_argument("--description", default="", help="Описание для pyproject")
    ap.add_argument(
        "--no-git", action="store_true", help="Не делать git init + initial commit"
    )
    ap.add_argument("--json", action="store_true", help="Только JSON-отчёт")
    args = ap.parse_args()

    try:
        pkg = package_name(args.project)
    except ValueError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 2

    target = Path(args.target) if args.target else bots_dir() / args.project
    r = scaffold_project(
        args.project,
        target=target,
        description=args.description,
        git_init=not args.no_git,
    )

    if args.json:
        print(json.dumps(r, ensure_ascii=False, indent=2))
    else:
        if "error" in r:
            print(
                f"[{args.project}] ERROR: {r['error']} ({r['path']})",
                file=sys.stderr,
            )
            return 1
        print(
            f"[{args.project}] created {len(r['created'])} files, "
            f"skipped {len(r['skipped'])}, git={r.get('git', 'off')}",
            file=sys.stderr,
        )
        print(f"  package: {pkg}")
        print(f"  path:    {r['path']}")

    return 0 if "error" not in r else 1


if __name__ == "__main__":
    sys.exit(main())
