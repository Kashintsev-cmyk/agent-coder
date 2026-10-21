"""Fixture: аудит agent_coder/ (B11). agent_coder/ уже в worktree из HEAD.

Добавляем только .agent-coder/ в .gitignore, чтобы persist state не попадал
в changed_files. Runner учитывает .gitignore через baseline_changed после fixture.
"""

import sys
from pathlib import Path

root = Path(sys.argv[1])
gi = root / ".gitignore"
line = ".agent-coder/"
if gi.exists():
    text = gi.read_text(encoding="utf-8")
    if line not in text:
        gi.write_text(text.rstrip() + "\n" + line + "\n", encoding="utf-8")
else:
    gi.write_text(line + "\n", encoding="utf-8")
