"""Fixture: копирует futures_3.0 (без venv/data/.git) в worktree для аудита."""
import shutil
import sys
from pathlib import Path

_SRC = Path("/home/botuser/bots/futures_3.0")
_IGNORE = shutil.ignore_patterns(
    "venv", ".venv", "__pycache__", ".git", "data", "catboost_info",
    "*.tar.gz", "*.pdf", "logs", ".pytest_cache", "manifest_server.txt",
)


def main() -> int:
    if not _SRC.is_dir():
        print(f"futures_3.0 not found: {_SRC}", file=sys.stderr)
        return 1
    ws = Path(sys.argv[1])
    for child in ws.iterdir():
        if child.name == ".git":
            continue
        if child.is_dir():
            shutil.rmtree(child)
        else:
            child.unlink()
    shutil.copytree(_SRC, ws, dirs_exist_ok=True, ignore=_IGNORE)
    (ws / ".gitignore").write_text("*.log\n__pycache__/\n.venv/\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
