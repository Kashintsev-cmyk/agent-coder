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
    # Pytest в worktree запускается без .venv (в _IGNORE) → ImportError
    # на aiogram/pydantic-settings. Симлинк на venv na_shtile — 0 байт,
    # зависимости резолвятся. Иначе все модели "ломают" тесты на импорте.
    _venv_src = _SRC / ".venv"
    _venv_dst = ws / ".venv"
    if _venv_src.is_dir() and not _venv_dst.exists():
        _venv_dst.symlink_to(_venv_src, target_is_directory=True)
    # pytest импортирует bot/config.py → Settings() требует BOT_TOKEN,
    # ADMIN_ID, WEATHER_API_KEY. Без .env все тесты кроме test_import_bot
    # падают на ValidationError. Копируем .env из na_shtile (в _IGNORE),
    # но дописываем в .gitignore worktree — иначе fixture-commit подхватит
    # секреты и .env попадёт в changed_files модели.
    _env_src = _SRC / ".env"
    if _env_src.is_file():
        shutil.copy2(_env_src, ws / ".env")
        _gi = ws / ".gitignore"
        _gi_text = _gi.read_text(encoding="utf-8") if _gi.is_file() else ""
        if ".env" not in _gi_text.splitlines():
            with _gi.open("a", encoding="utf-8") as _f:
                _f.write("\n.env\n")
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
