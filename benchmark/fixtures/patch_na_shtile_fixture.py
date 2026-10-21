"""patch_na_shtile_fixture.py — подготовка workspace для /patch.

Usage: python patch_na_shtile_fixture.py <workspace> [<sessions_dir>]

Копирует na_shtile в workspace, git init + commit, кладёт эталонную
session (34 confirmed findings из audit-A) в sessions_dir и перезаписывает
в JSON workspace на worktree-путь. Без перезаписи run_patch писал бы
в /home/botuser/bots/na_shtile — реальный проект.

Источник session: /home/botuser/bots/agent-coder/sessions/a6a74ebf.json
(na_shtile, deepseek/deepseek-v4-flash, 34 confirmed).
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

_SRC = Path("/home/botuser/bots/na_shtile")
_SESSION_SRC = Path("/home/botuser/bots/agent-coder/sessions/a6a74ebf.json")
_SESSION_ID = "a6a74ebf"
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


def _git(*args: str, cwd: Path) -> str:
    return subprocess.run(
        ["git", *args],
        cwd=cwd,
        text=True,
        check=True,
        capture_output=True,
    ).stdout


def main(workspace: str, sessions_dir: str | None = None) -> None:
    if not _SRC.is_dir():
        raise SystemExit(f"na_shtile not found: {_SRC}")
    if not _SESSION_SRC.is_file():
        raise SystemExit(f"reference session not found: {_SESSION_SRC}")

    ws = Path(workspace)
    # runner делает `git worktree add` от agent-coder — в worktree уже
    # лежат файлы agent-coder. Очищаем всё кроме .git, иначе na_shtile
    # копируется поверх и patch правит смесь двух проектов.
    for child in ws.iterdir():
        if child.name == ".git":
            continue
        if child.is_dir():
            shutil.rmtree(child)
        else:
            child.unlink()
    shutil.copytree(_SRC, ws, ignore=_IGNORE, dirs_exist_ok=True)
    subprocess.run(["git", "init", "-q"], cwd=ws, check=True)
    _git("add", "-A", cwd=ws)
    subprocess.run(
        [
            "git",
            "commit",
            "-q",
            "-m",
            "benchmark: patch_na_shtile fixture",
            "--no-verify",
        ],
        cwd=ws,
        check=True,
        capture_output=True,
    )

    if sessions_dir is None:
        print(f"fixture: {ws} ({len(list(ws.rglob('*.py')))} py files)")
        return

    sd = Path(sessions_dir)
    sd.mkdir(parents=True, exist_ok=True)
    data = json.loads(_SESSION_SRC.read_text(encoding="utf-8"))
    data["workspace"] = str(ws.resolve())
    confirmed = [
        v for v in data.get("verify_results", []) if v.get("status") == "confirmed"
    ]
    if len(confirmed) < 1:
        raise SystemExit(f"reference session has {len(confirmed)} confirmed, need >= 1")
    (sd / f"{_SESSION_ID}.json").write_text(
        json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(
        f"fixture: {ws} ({len(list(ws.rglob('*.py')))} py files), "
        f"session {_SESSION_ID} → {sd} ({len(confirmed)} confirmed)"
    )


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2] if len(sys.argv) > 2 else None)
