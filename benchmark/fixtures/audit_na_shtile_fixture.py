"""Fixture: na_shtile для audit-04 (read-only аудит).

Runner создаёт worktree от agent-coder HEAD — очищаем всё кроме .git,
копируем na_shtile, git init+commit. .env копируем (Settings() при
импорте), .agent-coder/ и .env в .gitignore (persist state не в
changed_files).
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

_SRC = Path("/home/botuser/bots/na_shtile")
_IGNORE = shutil.ignore_patterns(
    ".git",
    ".venv*",
    ".agent-coder",
    "__pycache__",
    "*.pyc",
    "*.db",
    "*.pdf",
    ".env",
)


def main(workspace: str, sessions_dir: str | None = None) -> None:
    ws = Path(workspace)
    # Очистить worktree (в нём файлы agent-coder из git worktree add)
    for child in ws.iterdir():
        if child.name == ".git":
            continue
        if child.is_dir():
            shutil.rmtree(child)
        else:
            child.unlink()
    shutil.copytree(_SRC, ws, ignore=_IGNORE, dirs_exist_ok=True)

    # .env — Settings() требует BOT_TOKEN/ADMIN_ID/WEATHER_API_KEY
    env_src = _SRC / ".env"
    if env_src.is_file():
        shutil.copy2(env_src, ws / ".env")

    # .gitignore: .env + .agent-coder/ (persist state не в changed_files)
    gi = ws / ".gitignore"
    text = gi.read_text(encoding="utf-8") if gi.is_file() else ""
    for line in (".env", ".agent-coder/"):
        if line not in text.splitlines():
            text = text.rstrip() + "\n" + line + "\n"
    gi.write_text(text, encoding="utf-8")

    subprocess.run(["git", "init", "-q"], cwd=ws, check=True)
    subprocess.run(["git", "add", "-A"], cwd=ws, check=True)
    subprocess.run(
        [
            "git",
            "commit",
            "-q",
            "-m",
            "benchmark: audit_na_shtile fixture",
            "--no-verify",
        ],
        cwd=ws,
        check=True,
        capture_output=True,
    )
    print(f"fixture: {ws} ({len(list(ws.rglob('*.py')))} py files)")


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2] if len(sys.argv) > 2 else None)
