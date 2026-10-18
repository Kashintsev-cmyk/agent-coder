"""Регистрация всех инструментов."""

from agent_coder.tools.check_project_standard import check_project_standard_tool
from agent_coder.tools.edit_file import edit_file_tool
from agent_coder.tools.git import (
    git_commit_tool,
    git_diff_tool,
    git_log_tool,
    git_status_tool,
)
from agent_coder.tools.list_files import list_files_tool
from agent_coder.tools.read_file import read_file_tool
from agent_coder.tools.registry import Tool, ToolContext, ToolRegistry
from agent_coder.tools.run_command import run_command_tool
from agent_coder.tools.search import search_tool
from agent_coder.tools.search_history import search_history_tool
from agent_coder.tools.work_state import update_work_state_tool
from agent_coder.tools.write_file import write_file_tool


def build_registry() -> ToolRegistry:
    registry = ToolRegistry()
    # Чтение
    registry.register(read_file_tool)
    registry.register(list_files_tool)
    registry.register(search_tool)
    registry.register(search_history_tool)
    registry.register(check_project_standard_tool)
    # Правка
    registry.register(write_file_tool)
    registry.register(edit_file_tool)
    # Терминал
    registry.register(run_command_tool)
    # Git
    registry.register(git_status_tool)
    registry.register(git_diff_tool)
    registry.register(git_log_tool)
    registry.register(git_commit_tool)
    # Рабочая память (structured memory, переживает compress)
    registry.register(update_work_state_tool)
    return registry


__all__ = ["Tool", "ToolContext", "ToolRegistry", "build_registry"]
