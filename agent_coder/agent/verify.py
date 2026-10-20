"""Верификация findings аудита (B9): дешёвый фильтр ложных выводов.

Не путать с AgentLoop:
- локальные messages, не session.messages (история scan не трогается);
- только read_file / list_files (whitelist), без approval, без compression,
  без watchdog, без auto-continue;
- бюджет MAX_COST_VERIFY (отдельный, не MAX_COST_PER_TASK);
- выход — структурированные вердикты CONFIRMED/REJECTED/UNCERTAIN,
  сохраняются в session.verify_results (append, история прогонов).

Модель по умолчанию — config.VERIFY_MODEL_ID (glm-5.3, дешёвый фильтр).
НЕ наследует session.model / session.model_manual: смысл verify —
экономия, а не «проверить дорогой моделью». Для дорогой проверки —
явно /verify <model_id>.
"""

from __future__ import annotations

import json
import logging
import re
import time
import uuid
from dataclasses import dataclass, field
from typing import Any

from agent_coder.agent.prompts import Mode
from agent_coder.agent.session import Session
from agent_coder.agent.work_state import render_for_prompt
from agent_coder.config import config
from agent_coder.rules import load_verify_standard
from agent_coder.tools import build_registry
from agent_coder.tools.registry import ToolContext, ToolRegistry

logger = logging.getLogger(__name__)

# Whitelist: verify — read-only, без мутаций и без run_command.
VERIFY_TOOLS: tuple[str, ...] = ("read_file", "list_files")

# Потолок итераций: verify не должен зацикливаться (обычно 1–3).
MAX_VERIFY_ITERATIONS = 10
# Последние N итераций — tools=[]: модель физически не может читать,
# обязана выдать вердикты текстом. Без этого glm-5.3 уходит в
# read_file-loop до лимита и возвращает 0 вердиктов (замер 3e2513be:
# 10 итераций, 10₽, 0 вердиктов).
FINAL_FORCE_ITERATIONS = 2

_VERDICT_RE = re.compile(
    r"^\s*[-*]?\s*[*_`]*(CONFIRMED|REJECTED|UNCERTAIN)[*_`]*\s*:\s*(.+)$",
    re.IGNORECASE,
)
_EMPTY_RE = re.compile(r"^\s*[-*]?\s*[*_`]*EMPTY[*_`]*\s*$", re.IGNORECASE)


@dataclass
class VerifyResult:
    """Итог одного прогона /verify."""

    run_id: str = ""
    model: str = ""
    cost: float = 0.0
    iterations: int = 0
    counts: dict[str, int] = field(default_factory=dict)
    raw_text: str = ""
    empty_work_state: bool = False
    budget_exceeded: bool = False
    error: str = ""

    @property
    def ok(self) -> bool:
        return not self.error and not self.empty_work_state


def parse_verdicts(text: str) -> list[dict[str, str]]:
    """Распарсить ответ модели.

    Строки по формату CONFIRMED/REJECTED/UNCERTAIN (с ``|`` или без)
    → статус + текст + evidence. EMPTY — игнорируется. Остальное —
    status="unparsed" (сохраняем raw, не теряем).

    Возвращает [{status, text, evidence}].
    """
    out: list[dict[str, str]] = []
    if not text or not text.strip():
        return out
    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            continue
        if _EMPTY_RE.match(line):
            continue
        m = _VERDICT_RE.match(line)
        if m:
            status = m.group(1).lower()
            body = m.group(2).strip()
            if "|" in body:
                txt, evidence = body.split("|", 1)
                out.append(
                    {
                        "status": status,
                        "text": txt.strip(),
                        "evidence": evidence.strip(),
                    }
                )
            else:
                out.append({"status": status, "text": body, "evidence": ""})
        else:
            out.append({"status": "unparsed", "text": line, "evidence": ""})
    return out


async def run_verify(
    client: Any,
    session: Session,
    model: str = "",
    registry: ToolRegistry | None = None,
) -> VerifyResult:
    """Прогнать work_state через модель-верификатор.

    client — LLMClient (или совместимый: async chat(messages, **kw)).
    model — явный override; пусто → config.VERIFY_MODEL_ID.
    registry — для тестов; None → build_registry().

    Возвращает VerifyResult. Результаты вердиктов дописываются в
    session.verify_results (append), session.save() вызывается.
    """
    model = model or config.VERIFY_MODEL_ID
    res = VerifyResult(run_id=uuid.uuid4().hex[:8], model=model)

    ws_text = render_for_prompt(session.work_state)
    if not ws_text:
        res.empty_work_state = True
        res.error = "work_state пуст — сначала /audit или update_work_state"
        return res

    system = load_verify_standard()
    if not system:
        res.error = "verify_standard.md не загружен (см. agent_coder/rules/)"
        return res
    system = system.replace("{workspace}", session.workspace)

    reg = registry if registry is not None else build_registry()
    tools = [t for t in (reg.get(n) for n in VERIFY_TOOLS) if t is not None]
    if not tools:
        res.error = "в registry нет read_file/list_files"
        return res
    tool_map = {t.name: t for t in tools}
    schemas = [t.to_openai_schema() for t in tools]

    messages: list[dict[str, Any]] = [
        {"role": "system", "content": system},
        {"role": "user", "content": ws_text},
    ]
    ctx = ToolContext(
        workspace=session.workspace,
        mode=Mode.AUDIT,
        yolo=True,  # read-only, approval не нужен
        session_id=session.id,
        work_state=None,
    )

    final_parts: list[str] = []

    force_injected = False
    while res.iterations < MAX_VERIFY_ITERATIONS:
        res.iterations += 1
        # На последних FINAL_FORCE_ITERATIONS — отключаем tools и
        # требуем текст. Модель не сможет читать, только вердикты.
        force_final = res.iterations > MAX_VERIFY_ITERATIONS - FINAL_FORCE_ITERATIONS
        if force_final and not force_injected:
            messages.append(
                {
                    "role": "user",
                    "content": (
                        "[ПРИНУДИТЕЛЬНО] СТОП чтение. Инструменты отключены. "
                        "Выдай CONFIRMED/REJECTED/UNCERTAIN по тем findings, "
                        "которые уже проверил, прямо сейчас. Только строки "
                        "вердиктов, без нарратива."
                    ),
                }
            )
            force_injected = True
        try:
            response = await client.chat(
                messages=messages,
                model=model,
                tools=[] if force_final else schemas,
                reasoning_effort=session.reasoning_effort,
            )
        except Exception as e:
            logger.exception("verify: chat failed")
            res.error = f"LLM: {e}"
            break

        # B9.2c: сессионный учёт — MAX_COST_PER_SESSION должен видеть
        # траты verify (раньше cost шёл только в VerifyResult).
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
                    f"Ошибка: инструмент '{tc.name}' недоступен в verify. "
                    f"Разрешены: {', '.join(VERIFY_TOOLS)}."
                )
            else:
                try:
                    result = await tool_map[tc.name].executor(tc.arguments, ctx)
                except Exception as e:
                    logger.exception("verify: tool %s failed", tc.name)
                    result = f"Ошибка выполнения инструмента: {e}"
            messages.append({"role": "tool", "tool_call_id": tc.id, "content": result})

        if res.cost >= config.MAX_COST_VERIFY:
            res.budget_exceeded = True
            logger.warning(
                "verify: бюджет %.2f₽ исчерпан (лимит %.2f₽)",
                res.cost,
                config.MAX_COST_VERIFY,
            )
            break

    res.raw_text = "\n".join(final_parts).strip()
    verdicts = parse_verdicts(res.raw_text)
    ts = time.time()
    counts: dict[str, int] = {}
    for v in verdicts:
        counts[v["status"]] = counts.get(v["status"], 0) + 1
        session.verify_results.append(
            {
                "run_id": res.run_id,
                "ts": ts,
                "model": model,
                "text": v["text"],
                "status": v["status"],
                "evidence": v["evidence"],
            }
        )
    res.counts = counts
    if not res.raw_text and not res.error:
        res.error = "модель не вернула ни текста, ни вердиктов"
    elif not verdicts and res.raw_text:
        # B9.2c: модель вернула текст, но без валидных вердиктов —
        # сохраняем как unparsed для диагностики.
        session.verify_results.append(
            {
                "run_id": res.run_id,
                "ts": ts,
                "model": model,
                "text": res.raw_text[:2000],
                "status": "unparsed",
                "evidence": "",
            }
        )
        counts["unparsed"] = 1
        res.counts = counts
    session.save()
    return res
