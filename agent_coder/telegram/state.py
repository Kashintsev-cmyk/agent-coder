"""Маппинг chat_id → активная сессия."""

from __future__ import annotations

import json
import os

from agent_coder.config import config

STATE_PATH = config.SESSIONS_DIR / "telegram_state.json"


def get_active_session(chat_id: int) -> str | None:
    return _load().get(str(chat_id))


def set_active_session(chat_id: int, session_id: str) -> None:
    data = _load()
    data[str(chat_id)] = session_id
    _save(data)


def clear_active_session(chat_id: int) -> None:
    data = _load()
    data.pop(str(chat_id), None)
    _save(data)


def remove_session_references(session_ids: set[str]) -> int:
    """Forget active-session pointers whose session files were removed."""
    if not session_ids:
        return 0
    data = _load()
    stale_chats = [
        chat_id for chat_id, session_id in data.items() if session_id in session_ids
    ]
    for chat_id in stale_chats:
        del data[chat_id]
    if stale_chats:
        _save(data)
    return len(stale_chats)


def _load() -> dict:
    if not STATE_PATH.exists():
        return {}
    try:
        return json.loads(STATE_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _save(data: dict) -> None:
    tmp = STATE_PATH.with_suffix(STATE_PATH.suffix + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, STATE_PATH)
