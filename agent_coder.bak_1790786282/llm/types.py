"""Типы для LLM-слоя."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


def format_cost(value: float) -> str:
    """Показать стоимость с максимальной точностью, без лишних нулей.

    0.0        → "0.0000 ₽"
    0.6126     → "0.6126 ₽"
    0.08491812 → "0.08491812 ₽"
    0.00001234 → "0.00001234 ₽"
    """
    if value == 0:
        return "0.0000 ₽"
    s = f"{value:.10f}".rstrip("0").rstrip(".")
    return f"{s} ₽"


@dataclass
class Usage:
    """Нормализованный usage из ответа RouterAI."""
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0
    cost: float = 0.0

    def __add__(self, other: "Usage") -> "Usage":
        return Usage(
            input_tokens=self.input_tokens + other.input_tokens,
            output_tokens=self.output_tokens + other.output_tokens,
            cache_read_tokens=self.cache_read_tokens + other.cache_read_tokens,
            cache_write_tokens=self.cache_write_tokens + other.cache_write_tokens,
            cost=self.cost + other.cost,
        )

    def format_short(self) -> str:
        parts = [f"in {self.input_tokens} / out {self.output_tokens}"]
        if self.cache_read_tokens:
            parts.append(f"cache {self.cache_read_tokens}")
        parts.append(format_cost(self.cost))
        return " · ".join(parts)


@dataclass
class ToolCall:
    id: str
    name: str
    arguments: dict[str, Any] = field(default_factory=dict)


@dataclass
class AssistantMessage:
    content: str | None
    tool_calls: list[ToolCall] = field(default_factory=list)
    reasoning: str | None = None


@dataclass
class ChatResponse:
    message: AssistantMessage
    usage: Usage
    raw: Any = None