"""Rich-вывод и диалоги подтверждения."""
from __future__ import annotations

from rich.console import Console
from rich.panel import Panel
from rich.prompt import Confirm
from rich.text import Text

from agent_coder.agent.session import Session
from agent_coder.llm.types import ToolCall, Usage, format_cost
from agent_coder.telegram.renderer import tool_icon

console = Console()


def print_banner(session_id: str, workspace: str, model: str, mode: str) -> None:
    text = Text()
    text.append("Session:   ", style="dim"); text.append(f"{session_id}\n", style="cyan")
    text.append("Workspace: ", style="dim"); text.append(f"{workspace}\n")
    text.append("Model:     ", style="dim"); text.append(f"{model}\n")
    text.append("Mode:      ", style="dim")
    text.append(mode, style="yellow" if mode == "plan" else "green")
    console.print(Panel(text, title="agent-coder", border_style="cyan"))


def print_user_prompt(mode: str) -> str:
    marker = "plan" if mode == "plan" else "act"
    return console.input(f"\n[bold]Ты ({marker})[/bold] [cyan]›[/cyan] ")


def print_assistant_text(text: str) -> None:
    console.print(f"\n[bold green]⏺[/bold green] {text}")


def print_tool_call(tc: ToolCall) -> None:
    icon = tool_icon(tc.name)
    args = _short_args(tc.arguments)
    console.print(f"  [yellow]{icon}[/yellow] [bold]{tc.name}[/bold]({args})")


def print_tool_result(tc: ToolCall, result: str) -> None:
    preview = _preview(result, 500)
    style = "red" if preview.startswith("Ошибка") else "dim"
    console.print(f"     [{style}]↳ {preview}[/{style}]")


def print_usage(usage: Usage) -> None:
    parts = [f"in {usage.input_tokens}", f"out {usage.output_tokens}"]
    if usage.cache_read_tokens:
        parts.append(f"cache {usage.cache_read_tokens}")
    console.print(f"  [dim]→ {' / '.join(parts)} · {format_cost(usage.cost)}[/dim]")


def print_total_usage(usage: Usage, request_count: int) -> None:
    console.print(Panel(
        f"Запросов: {request_count}\n"
        f"Tokens in:  {usage.input_tokens} (cache {usage.cache_read_tokens})\n"
        f"Tokens out: {usage.output_tokens}\n"
        f"[bold]Стоимость: {format_cost(usage.cost)}[/bold]",
        title="Итого", border_style="green",
    ))


def print_error(message: str) -> None:
    console.print(f"[bold red]✗[/bold red] {message}")


def print_info(message: str) -> None:
    console.print(f"[dim]{message}[/dim]")


def print_success(message: str) -> None:
    console.print(f"[bold green]✓[/bold green] {message}")


async def ask_approval(tc: ToolCall) -> bool:
    console.print()
    console.print(Panel(
        _format_approval(tc),
        title="[yellow]⚠ Требуется подтверждение[/yellow]",
        border_style="yellow",
    ))
    return Confirm.ask("Разрешить?", default=False)


def _format_approval(tc: ToolCall) -> str:
    if tc.name == "run_command":
        return f"[bold]$ {tc.arguments.get('command', '')}[/bold]"
    lines = [f"[bold]{tc.name}[/bold]"]
    for key, value in tc.arguments.items():
        lines.append(f"  [dim]{key}:[/dim] {_preview(str(value), 300)}")
    return "\n".join(lines)



def _short_args(args: dict) -> str:
    parts = []
    for k, v in args.items():
        s = str(v)
        if len(s) > 60:
            s = s[:57] + "..."
        parts.append(f"{k}={s!r}")
    return ", ".join(parts)


def _preview(text: str, limit: int) -> str:
    text = text.strip()
    if len(text) <= limit:
        return text
    return text[:limit] + f"... [+{len(text) - limit} символов]"


def print_sessions_list(sessions: list[Session]) -> None:
    """Список сессий с заголовками и стоимостью."""
    if not sessions:
        console.print("[dim]Сессий нет.[/dim]")
        return

    console.print("\n[bold]Сессии:[/bold]")
    for s in sessions[:20]:
        mode_color = "yellow" if s.mode.value == "plan" else "green"
        title = s.title or "(без названия)"
        console.print(
            f"  [cyan]{s.id}[/cyan]  "
            f"[{mode_color}]{s.mode.value:4}[/{mode_color}]  "
            f"[bold]{title}[/bold]  "
            f"[dim]· {format_cost(s.total_usage.cost)} · {s.age_str()}[/dim]"
        )
        preview = s.preview()
        # Не показываем превью, если оно повторяет заголовок
        if preview and not preview.rstrip("…").startswith(title.rstrip("…")):
            console.print(f"           [dim]{preview}[/dim]")


def print_current_session(session: Session) -> None:
    """Одна строка про текущую сессию."""
    console.print(
        f"[dim]Сессия[/dim] [cyan]{session.id}[/cyan]  "
        f"[bold]{session.title}[/bold]  "
        f"[dim]({session.mode.value}, {format_cost(session.total_usage.cost)})[/dim]"
    )