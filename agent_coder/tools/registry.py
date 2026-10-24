"""Реестр инструментов: схема для LLM + выполнение + режимы."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from agent_coder.agent.prompts import Mode

ToolExecutor = Callable[[dict[str, Any], "ToolContext"], Awaitable[str]]
SafeCheck = Callable[[dict[str, Any]], bool]


@dataclass
class ToolContext:
    """Что видит инструмент при выполнении.

    work_state — узкий handle над Session.work_state (не вся Session),
    по образцу Cline TaskState / Codex ToolInvocation. None — для tools,
    которым state не нужен (все, кроме update_work_state).
    """

    workspace: str
    mode: Mode
    yolo: bool = False
    session_id: str = ""  # для search_history (архив сжатой истории)
    work_state: Any | None = None  # WorkStateHandle; Any — без цикла импортов
    # R13: уже прочитанные файлы {path: digest}. read_file с полным чтением
    # по ним отвергается (только offset/limit). None — для tools/адаптеров,
    # которым state не нужен (patch, telegram).
    read_paths: dict[str, str] | None = None
    # B11.3: область аудита (относительные пути от workspace). Только AUDIT;
    # None/[] — без ограничений (ACT/PLAN/PATCH/VERIFY).
    audit_scopes: list[str] | None = None


@dataclass
class Tool:
    name: str
    description: str
    parameters: dict[str, Any]
    executor: ToolExecutor
    requires_approval: bool = False
    read_only: bool = False
    is_safe_call: SafeCheck | None = None

    def needs_approval(self, args: dict[str, Any]) -> bool:
        """Нужно ли подтверждение для этого конкретного вызова."""
        if not self.requires_approval:
            return False
        if self.is_safe_call is not None:
            try:
                if self.is_safe_call(args):
                    return False
            except Exception:
                pass  # любая ошибка в проверке = требуем подтверждение
        return True

    def to_openai_schema(self) -> dict[str, Any]:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters,
            },
        }


class ToolRegistry:
    def __init__(self) -> None:
        self._tools: dict[str, Tool] = {}

    def register(self, tool: Tool) -> None:
        if tool.name in self._tools:
            raise ValueError(f"Инструмент '{tool.name}' уже зарегистрирован")
        self._tools[tool.name] = tool

    def get(self, name: str) -> Tool | None:
        return self._tools.get(name)

    def for_mode(self, mode: Mode) -> list[Tool]:
        """Набор инструментов под режим.

        - PLAN / AUDIT: read-only + run_command (whitelisted-вызовы)
        - ACT: все инструменты
        """
        if mode in (Mode.PLAN, Mode.AUDIT):
            out = [t for t in self._tools.values() if t.read_only]
            rc = self._tools.get("run_command")
            if rc and rc not in out:
                out.append(rc)
            return out
        return list(self._tools.values())

    def is_call_allowed(self, mode: Mode, tool: Tool, args: dict[str, Any]) -> bool:
        """Разрешён ли вызов инструмента в этом режиме."""
        if mode == Mode.ACT:
            return True
        # Plan и Audit
        if tool.read_only:
            return True
        if tool.name == "run_command" and tool.is_safe_call:
            try:
                return tool.is_safe_call(args)
            except Exception:
                return False
        return False

    def openai_schemas(self, mode: Mode) -> list[dict[str, Any]]:
        return [t.to_openai_schema() for t in self.for_mode(mode)]
