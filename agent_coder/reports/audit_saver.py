"""Сохранение audit-отчёта в Markdown.

Используется после завершения задачи в Audit-режиме. Извлекает финальный
отчёт из истории сессии, сохраняет в .md, возвращает путь.

B9.3: .md содержит три секции:
- «Отчёт» — самое длинное assistant-сообщение после последнего user
  (полный отчёт, а не summary);
- «Структурированные findings» — session.work_state["findings"]
  (машинно-читаемо, для будущего /patch);
- «Сводка» — последнее assistant-сообщение, если оно короче полного
  (executive summary).
"""

from __future__ import annotations

import logging
import time
from pathlib import Path

from agent_coder.agent.session import Session, content_to_text
from agent_coder.config import config

logger = logging.getLogger(__name__)


def _assistants_after_last_user(session: Session) -> list[str]:
    """Assistant-сообщения после последнего user (в хронологическом порядке)."""
    last_user = -1
    for i in range(len(session.messages) - 1, -1, -1):
        if session.messages[i].get("role") == "user":
            last_user = i
            break
    out: list[str] = []
    for msg in session.messages[last_user + 1 :]:
        if msg.get("role") != "assistant":
            continue
        text = content_to_text(msg.get("content")).strip()
        if text:
            out.append(text)
    return out


def extract_audit_report(session: Session) -> str:
    """Достаёт полный финальный отчёт из истории сессии.

    B9.3: берём САМОЕ ДЛИННОЕ assistant-сообщение после последнего user
    (раньше брали первое > 500 с конца — попадал summary, 606 вместо 8655).
    Если assistant после последнего user нет — fallback на всю историю.
    """
    parts = _assistants_after_last_user(session)
    if parts:
        return max(parts, key=len)
    # Fallback: если после последнего user пусто — все assistant за сессию.
    parts = [
        content_to_text(m.get("content")).strip()
        for m in session.messages
        if m.get("role") == "assistant"
    ]
    parts = [p for p in parts if p]
    return max(parts, key=len) if parts else "(пусто)"


def extract_audit_summary(session: Session, full_report: str) -> str:
    """Краткая сводка — последнее assistant после последнего user.

    Если совпадает с полным отчётом (или пусто) — возвращает "".
    """
    parts = _assistants_after_last_user(session)
    if not parts:
        return ""
    last = parts[-1]
    if last == full_report or last in full_report:
        return ""
    return last


def format_work_state_findings(session: Session) -> str:
    """Markdown-список findings из work_state (B9.3).

    Машинно-читаемо для будущего /patch: каждая строка — отдельный факт
    (обычно «файл:строка — описание»). Пусто → "".
    """
    findings = (session.work_state or {}).get("findings") or []
    if not findings:
        return ""
    lines = ["## Структурированные findings (work_state)", ""]
    for f in findings:
        lines.append(f"- {f}")
    return "\n".join(lines)


def save_audit_report(
    session: Session,
    also_in_workspace: bool = False,
) -> Path:
    """Сохранить audit-отчёт в .md. Возвращает путь к .md.

    Файл: sessions/audits/<session_id>_<YYYYMMDD_HHMMSS>.md
    Если also_in_workspace=True — дополнительно копирует .md в workspace проекта.

    Структура .md (B9.3): header + «Отчёт» (полный) +
    «Структурированные findings» (work_state) + «Сводка» (последний assistant).
    """
    full_report = extract_audit_report(session)
    summary = extract_audit_summary(session, full_report)
    findings_md = format_work_state_findings(session)

    audits_dir = config.SESSIONS_DIR / "audits"
    audits_dir.mkdir(parents=True, exist_ok=True)

    ts = time.strftime("%Y%m%d_%H%M%S")
    stem = f"{session.id}_{ts}"

    md_path = audits_dir / f"{stem}.md"

    header = (
        f"# Аудит проекта\n\n"
        f"- **Сессия:** `{session.id}`\n"
        f"- **Проект:** `{session.workspace}`\n"
        f"- **Модель:** `{session.model}`\n"
        f"- **Reasoning:** `{session.reasoning_effort}`\n"
        f"- **Дата:** {time.strftime('%Y-%m-%d %H:%M:%S')}\n"
        f"- **Запросов:** {session.request_count}\n"
        f"- **Стоимость:** {session.total_usage.cost:.4f} ₽\n\n"
        f"---\n\n"
    )
    body = f"## Отчёт\n\n{full_report}\n"
    if findings_md:
        body += f"\n{findings_md}\n"
    if summary:
        body += f"\n## Сводка\n\n{summary}\n"
    md_path.write_text(header + body, encoding="utf-8")

    if also_in_workspace:
        try:
            ws = Path(session.workspace)
            if ws.is_dir():
                (ws / "AUDIT_REPORT.md").write_text(
                    md_path.read_text(encoding="utf-8"), encoding="utf-8"
                )
        except Exception as e:
            logger.warning("не смог скопировать отчёт в workspace: %s", e)

    return md_path
