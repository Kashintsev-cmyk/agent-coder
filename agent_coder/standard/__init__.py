"""Публичный API стандарта bots/."""

from agent_coder.standard.collect import collect_issues, summarize
from agent_coder.standard.fixes import FIX_SPECS, FixSpec, fix_for
from agent_coder.standard.issue import Issue, Severity
from agent_coder.standard.notes import (
    ENV_NOTES_DIR,
    notes_dir,
    notes_path,
    read_notes,
)
from agent_coder.standard.paths import ENV_BOTS_DIR, bots_dir, project_dir
from agent_coder.standard.render import render_markdown
from agent_coder.standard.state import (
    ENV_STATE_DIR,
    build_state,
    read_state,
    state_dir,
    state_path,
    write_if_changed,
    write_state,
)

__all__ = [
    "ENV_BOTS_DIR",
    "ENV_NOTES_DIR",
    "ENV_STATE_DIR",
    "FIX_SPECS",
    "FixSpec",
    "Issue",
    "Severity",
    "bots_dir",
    "build_state",
    "collect_issues",
    "fix_for",
    "notes_dir",
    "notes_path",
    "project_dir",
    "read_notes",
    "read_state",
    "render_markdown",
    "state_dir",
    "state_path",
    "summarize",
    "write_if_changed",
    "write_state",
]
