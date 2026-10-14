"""Telegram-хендлеры: команды, задачи, callback-кнопки."""
from __future__ import annotations

import logging

from agent_coder.config import config as config

logger = logging.getLogger(__name__)

# Роутер, утилиты и глобальное состояние — в shared.py (единая точка).
# Импорт подмодулей — регистрация хендлеров на router (side-effect).
# Импорт обязателен: без него хендлеры не активируются.
from . import approval, commands, github, ops, session_menu, task  # noqa: F401
from .approval import (
    cb_approve as cb_approve,
)
from .approval import (
    cb_deny as cb_deny,
)
from .commands import (
    _send_history_preview as _send_history_preview,
)
from .commands import (
    cb_back as cb_back,
)
from .commands import (
    cb_cost as cb_cost,
)
from .commands import (
    cb_delete_cancel as cb_delete_cancel,
)
from .commands import (
    cb_delete_confirm as cb_delete_confirm,
)
from .commands import (
    cb_history as cb_history,
)
from .commands import (
    cb_history_md as cb_history_md,
)
from .commands import (
    cb_mode as cb_mode,
)
from .commands import (
    cb_new as cb_new,
)
from .commands import (
    cb_new_project_hint as cb_new_project_hint,
)
from .commands import (
    cb_open as cb_open,
)
from .commands import (
    cb_project_create_no as cb_project_create_no,
)
from .commands import (
    cb_project_create_yes as cb_project_create_yes,
)
from .commands import (
    cb_sessions as cb_sessions,
)
from .commands import (
    cmd_balance as cmd_balance,
)
from .commands import (
    cmd_cancel as cmd_cancel,
)
from .commands import (
    cmd_chat as cmd_chat,
)
from .commands import (
    cmd_cost as cmd_cost,
)
from .commands import (
    cmd_current as cmd_current,
)
from .commands import (
    cmd_delete as cmd_delete,
)
from .commands import (
    cmd_help as cmd_help,
)
from .commands import (
    cmd_history as cmd_history,
)
from .commands import (
    cmd_mode as cmd_mode,
)
from .commands import (
    cmd_model as cmd_model,
)
from .commands import (
    cmd_new as cmd_new,
)
from .commands import (
    cmd_projects as cmd_projects,
)
from .commands import (
    cmd_reasoning as cmd_reasoning,
)
from .commands import (
    cmd_sessions as cmd_sessions,
)
from .commands import (
    cmd_start as cmd_start,
)
from .github import (
    _clone_repo as _clone_repo,
)
from .github import (
    _normalize_github_url as _normalize_github_url,
)
from .project_utils import (
    _PROJECTS_DEFAULT_DIR as _PROJECTS_DEFAULT_DIR,
)
from .project_utils import (
    _create_project_on_disk as _create_project_on_disk,
)
from .project_utils import (
    _create_session as _create_session,
)
from .project_utils import (
    _validate_project_name as _validate_project_name,
)
from .runner import (
    _join_buffer as _join_buffer,
)
from .runner import (
    _split_for_telegram as _split_for_telegram,
)
from .runner import (
    _TaskRunner as _TaskRunner,
)
from .shared import (
    _audit_reasoning_warning as _audit_reasoning_warning,
)
from .shared import (
    _get_active_session as _get_active_session,
)
from .shared import (
    _pending as _pending,
)
from .shared import (
    _running as _running,
)
from .shared import (
    _should_preserve_history as _should_preserve_history,
)
from .shared import (
    _shutting_down as _shutting_down,
)
from .shared import (
    router as router,
)
from .shared import (
    shutdown as shutdown,
)
from .task import (
    _answer_question as _answer_question,
)
from .task import (
    _generate_and_send_audit as _generate_and_send_audit,
)
from .task import (
    _is_question as _is_question,
)
from .task import (
    _run_task as _run_task,
)
from .task import (
    _start_task as _start_task,
)
from .task import (
    cb_continue_task as cb_continue_task,
)
from .task import (
    handle_task as handle_task,
)
from .task import (
    handle_task_document as handle_task_document,
)
from .task import (
    handle_task_photo as handle_task_photo,
)
