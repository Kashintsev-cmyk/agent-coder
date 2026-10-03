"""Единый запуск shell-команд с таймаутом, маскированием, kill-группой.

Заменяет 3 почти одинаковых блока subprocess в:
- tools/run_command.py
- telegram/handlers.py::cb_server_op
- telegram/handlers.py::cb_audit_op
"""
from __future__ import annotations

import asyncio
import logging
import os
import signal
from dataclasses import dataclass
from pathlib import Path

from agent_coder.log_safety import mask_secrets

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ShellResult:
    returncode: int
    stdout: str
    stderr: str
    timed_out: bool = False
    error: str = ""

    @property
    def output(self) -> str:
        """stdout или stderr, или заглушка."""
        return self.stdout or self.stderr or "(пусто)"

    @property
    def ok(self) -> bool:
        return not self.timed_out and not self.error


async def run_shell(
    cmd: str,
    *,
    cwd: str | Path | None = None,
    timeout: float = 300.0,
    max_bytes: int = 30_000,
    mask: bool = True,
    start_new_session: bool = True,
) -> ShellResult:
    """Запустить shell-команду. Никогда не бросает наружу.

    - timeout — секунды; при превышении — kill группы процессов.
    - max_bytes — обрезка каждого потока (0 = без обрезки).
    - mask — прогнать вывод через log_safety.mask_secrets.
    - start_new_session — новый session id у ребёнка, чтобы killpg убил
      и его потомков (важно для shell-пайпов).
    """
    try:
        proc = await asyncio.create_subprocess_shell(
            cmd,
            cwd=str(cwd) if cwd else None,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            start_new_session=start_new_session,
        )
    except Exception as e:
        logger.warning("run_shell: не удалось запустить процесс: %s", e)
        return ShellResult(returncode=-1, stdout="", stderr="", error=str(e))

    try:
        stdout_b, stderr_b = await asyncio.wait_for(
            proc.communicate(), timeout=timeout,
        )
    except asyncio.TimeoutError:
        _kill_proc_tree(proc, use_group=start_new_session)
        try:
            await proc.wait()
        except Exception:
            pass
        return ShellResult(
            returncode=-1, stdout="", stderr="",
            timed_out=True, error=f"таймаут {timeout:.0f}с",
        )

    stdout = stdout_b.decode("utf-8", errors="replace")
    stderr = stderr_b.decode("utf-8", errors="replace")
    if mask:
        stdout = mask_secrets(stdout)
        stderr = mask_secrets(stderr)
    if max_bytes > 0 and len(stdout_b) > max_bytes:
        stdout = stdout[:max_bytes] + f"\n... (обрезано, всего {len(stdout_b)} байт)"
    if max_bytes > 0 and len(stderr_b) > max_bytes:
        stderr = stderr[:max_bytes] + f"\n... (обрезано, всего {len(stderr_b)} байт)"

    return ShellResult(
        returncode=proc.returncode if proc.returncode is not None else 0,
        stdout=stdout.rstrip(),
        stderr=stderr.rstrip(),
    )


def _kill_proc_tree(proc, *, use_group: bool) -> None:
    """Убить процесс; если он в новой сессии — убить всю группу."""
    if use_group and proc.pid:
        try:
            os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
            return
        except (ProcessLookupError, PermissionError, OSError):
            pass
    try:
        proc.kill()
    except Exception:
        pass
