"""Патчинг confirmed findings (B9.2): правки по /verify confirmed.

Не путать с AgentLoop:
- локальные messages, не session.messages (история сессии не трогается);
- whitelist tools с write/edit/run, но без git_commit (коммит — пользователь);
- свой watchdog (consecutive_errors) и бюджет MAX_COST_PATCH;
- без compression: confirmed findings точные, read_file — offset/limit.

Модель по умолчанию — config.PATCH_MODEL_ID (= DEFAULT_MODEL_ID, deepseek).
НЕ наследует session.model: patch — механическая задача, экономия.
Для явной модели — /patch <model_id>.
"""

from __future__ import annotations

import json
import logging
import subprocess
import time
import uuid
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any

from agent_coder.agent.prompts import Mode
from agent_coder.agent.session import Session
from agent_coder.agent.work_state import render_for_prompt
from agent_coder.config import config
from agent_coder.tools import build_registry
from agent_coder.tools.registry import ToolContext, ToolRegistry

logger = logging.getLogger(__name__)

# Whitelist: read + write + git read. git_commit НЕ включён — коммит
# делает пользователь после ревью diff'а. update_work_state НЕ включён:
# patch меняет код, а не state сессии.
PATCH_TOOLS: tuple[str, ...] = (
    "read_file",
    "list_files",
    "search",
    "write_file",
    "edit_file",
    "run_command",
    "git_status",
    "git_diff",
    "git_log",
)

# Ошибок подряд до остановки (как MAX_CONSECUTIVE_ERRORS в AgentLoop).
MAX_CONSECUTIVE_ERRORS = 5


@dataclass
class PatchResult:
    """Итог одного прогона /patch."""

    run_id: str = ""
    model: str = ""
    cost: float = 0.0
    iterations: int = 0
    files_changed: list[str] = field(default_factory=list)
    git_diff_stat: str = ""
    raw_text: str = ""
    no_confirmed: bool = False
    budget_exceeded: bool = False
    error: str = ""

    @property
    def ok(self) -> bool:
        return not self.error and not self.no_confirmed


@lru_cache(maxsize=1)
def load_patch_standard() -> str:
    """Прочитать patch_standard.md (кэш на процесс)."""
    path = Path(__file__).parent.parent / "rules" / "patch_standard.md"
    try:
        return path.read_text(encoding="utf-8")
    except OSError:
        logger.exception("patch: не удалось прочитать %s", path)
        return ""


def _collect_confirmed(verify_results: list[dict[str, Any]]) -> list[dict[str, str]]:
    """Собрать confirmed из session.verify_results, дедуп по (text, evidence).

    Возвращает [{text, evidence}]. Дедуп: пользователь мог сделать 3 /verify
    подряд — один finding подтвердится трижды. Оставляем один.
    """
    seen: set[tuple[str, str]] = set()
    out: list[dict[str, str]] = []
    for v in verify_results or []:
        if v.get("status") != "confirmed":
            continue
        key = (v.get("text", ""), v.get("evidence", ""))
        if key in seen:
            continue
        seen.add(key)
        out.append({"text": v.get("text", ""), "evidence": v.get("evidence", "")})
    return out


def _build_task(confirmed: list[dict[str, str]], session: Session) -> str:
    """Сформировать user-message: confirmed findings + work_state.todo."""
    lines = ["CONFIRMED FINDINGS (починить только это):", ""]
    for i, f in enumerate(confirmed, 1):
        ev = f" | {f['evidence']}" if f["evidence"] else ""
        lines.append(f"{i}. {f['text']}{ev}")
    ws_text = render_for_prompt(session.work_state)
    if ws_text:
        lines.append("")
        lines.append(ws_text)
    lines.append("")
    lines.append(
        "Действуй по patch_standard.md: минимальные правки, без "
        "рефакторинга, git_commit не вызывай."
    )
    return "\n".join(lines)


def _git_diff_stat(workspace: str) -> str:
    """git diff --stat + git status --short для workspace.

    Детерминированно, не через LLM. Пустая строка, если workspace не
    git-репозиторий или git недоступен.
    """
    parts: list[str] = []
    for cmd in (["git", "diff", "--stat"], ["git", "status", "--short"]):
        try:
            proc = subprocess.run(
                cmd,
                cwd=workspace,
                capture_output=True,
                text=True,
                timeout=10,
                check=False,
            )
            if proc.returncode == 0 and proc.stdout.strip():
                parts.append(proc.stdout.strip())
        except (OSError, subprocess.SubprocessError):
            logger.debug("patch: %r failed", cmd, exc_info=True)
    return "\n".join(parts)


async def run_patch(
    client: Any,
    session: Session,
    model: str = "",
    registry: ToolRegistry | None = None,
) -> PatchResult:
    """Прогнать confirmed findings через модель-исполнителя.

    client — LLMClient (или совместимый: async chat(messages, **kw)).
    model — явный override; пусто → config.PATCH_MODEL_ID.
    registry — для тестов; None → build_registry().

    Возвращает PatchResult. Результат дописывается в session.patch_results
    (append), session.save() вызывается.
    """
    model = model or config.PATCH_MODEL_ID
    res = PatchResult(run_id=uuid.uuid4().hex[:8], model=model)

    confirmed = _collect_confirmed(session.verify_results)
    # B9.2e: вычесть findings, которые УЖЕ были переданы модели в
    # предыдущих patch-прогонах. Без этого повторный /patch снова
    # берёт уже обработанные (в т.ч. неудачно) — тратит итерации.
    # Exclusion по (text, evidence), как в _collect_confirmed.
    already = {
        (f.get("text", ""), f.get("evidence", ""))
        for pr in (session.patch_results or [])
        for f in (pr.get("findings") or [])
    }
    confirmed = [c for c in confirmed if (c["text"], c["evidence"]) not in already]
    if not confirmed:
        res.no_confirmed = True
        res.error = "все confirmed уже были в предыдущих /patch"
        return res

    system = load_patch_standard()
    if not system:
        res.error = "patch_standard.md не загружен (см. agent_coder/rules/)"
        return res
    system = system.replace("{workspace}", session.workspace)

    reg = registry if registry is not None else build_registry()
    tools = [t for t in (reg.get(n) for n in PATCH_TOOLS) if t is not None]
    if not tools:
        res.error = "в registry нет доступных tools для patch"
        return res
    tool_map = {t.name: t for t in tools}
    schemas = [t.to_openai_schema() for t in tools]

    task = _build_task(confirmed, session)
    messages: list[dict[str, Any]] = [
        {"role": "system", "content": system},
        {"role": "user", "content": task},
    ]
    ctx = ToolContext(
        workspace=session.workspace,
        mode=Mode.ACT,
        yolo=True,
        session_id=session.id,
        work_state=None,
    )

    files_changed: set[str] = set()
    consecutive_errors = 0
    final_parts: list[str] = []

    while res.iterations < config.MAX_PATCH_ITERATIONS:
        res.iterations += 1
        try:
            # B9.2e: patch — механическая задача (найти, поправить, тесты).
            # reasoning="low": deepseek v4-flash на medium уходил в 10-15K
            # reasoning-токенов на сложных BLOCKER'ах (_span, _direction_consensus)
            # и не писал файлы (замер: 26 итераций, 2.5₽, 0 write).
            # timeout=180: без него при зависшем провайдере цикл ждал 15 мин.
            import asyncio as _aio

            response = await _aio.wait_for(
                client.chat(
                    messages=messages,
                    model=model,
                    tools=schemas,
                    reasoning_effort=config.PATCH_REASONING_EFFORT,
                ),
                timeout=config.PATCH_CHAT_TIMEOUT,
            )
        except TimeoutError:
            logger.warning("patch: chat timeout %.0fs", config.PATCH_CHAT_TIMEOUT)
            res.error = f"LLM timeout {config.PATCH_CHAT_TIMEOUT:.0f}s"
            break
        except Exception as e:
            logger.exception("patch: chat failed")
            res.error = f"LLM: {e}"
            break

        # B9.2c: сессионный учёт — MAX_COST_PER_SESSION видит траты patch.
        session.total_usage = session.total_usage + response.usage
        res.cost += response.usage.cost
        msg = response.message
        if msg.content:
            final_parts.append(msg.content)

        if not msg.tool_calls:
            break

        messages.append(
            {
                "role": "assistant",
                "content": msg.content or "",
                "tool_calls": [
                    {
                        "id": tc.id,
                        "type": "function",
                        "function": {
                            "name": tc.name,
                            "arguments": json.dumps(tc.arguments, ensure_ascii=False),
                        },
                    }
                    for tc in msg.tool_calls
                ],
            }
        )

        for tc in msg.tool_calls:
            if tc.name not in tool_map:
                result = (
                    f"Ошибка: инструмент '{tc.name}' недоступен в patch. "
                    f"Разрешены: {', '.join(PATCH_TOOLS)}."
                )
                consecutive_errors += 1
            else:
                try:
                    result = await tool_map[tc.name].executor(tc.arguments, ctx)
                    if tc.name in ("write_file", "edit_file"):
                        path = str(tc.arguments.get("path") or "").strip()
                        if path:
                            files_changed.add(path)
                    if result.lstrip().startswith("Ошибка"):
                        consecutive_errors += 1
                    else:
                        consecutive_errors = 0
                except Exception as e:
                    logger.exception("patch: tool %s failed", tc.name)
                    result = f"Ошибка выполнения инструмента: {e}"
                    consecutive_errors += 1
            messages.append({"role": "tool", "tool_call_id": tc.id, "content": result})

        if consecutive_errors >= MAX_CONSECUTIVE_ERRORS:
            res.error = (
                f"{consecutive_errors} ошибок подряд — останавливаюсь, "
                f"чтобы не зациклиться"
            )
            logger.warning("patch: %s", res.error)
            break

        if res.cost >= config.MAX_COST_PATCH:
            res.budget_exceeded = True
            logger.warning(
                "patch: бюджет %.2f₽ исчерпан (лимит %.2f₽)",
                res.cost,
                config.MAX_COST_PATCH,
            )
            break

    res.raw_text = "\n".join(final_parts).strip()
    res.files_changed = sorted(files_changed)
    res.git_diff_stat = _git_diff_stat(session.workspace)

    if not res.raw_text and not res.error and not res.files_changed:
        res.error = "модель не вернула ни текста, ни правок"

    session.patch_results.append(
        {
            "run_id": res.run_id,
            "ts": time.time(),
            "model": model,
            "iterations": res.iterations,
            "cost": res.cost,
            "files_changed": res.files_changed,
            "git_diff_stat": res.git_diff_stat,
            "error": res.error,
            "budget_exceeded": res.budget_exceeded,
            # B9.2d: список findings, переданных модели. Для диагностики
            # (что вошло в прогон) и будущего exclusion (не сейчас —
            # модель может «попробовать и не справиться», exclusion
            # такого finding'а не должен блокировать повтор).
            "findings": confirmed,
        }
    )
    session.save()
    return res
