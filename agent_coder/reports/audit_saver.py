"""Сохранение audit-отчёта в Markdown.

Используется после завершения задачи в Audit-режиме. Извлекает финальный
отчёт из истории сессии, сохраняет в .md, возвращает путь.
"""
from __future__ import annotations

import logging
import time
from pathlib import Path

from agent_coder.agent.session import Session, content_to_text
from agent_coder.config import config

logger = logging.getLogger(__name__)


def extract_audit_report(session: Session) -> str:
    """Достаёт финальный отчёт из истории сессии.

    Стратегия: идём с конца до последнего user. Берём первое assistant-сообщение
    с длинным content (>500). Если такого нет — склеиваем все assistant после
    последнего user.
    """
    # Первое крупное assistant-сообщение после последнего user
    for i in range(len(session.messages) - 1, -1, -1):
        msg = session.messages[i]
        role = msg.get("role")
        if role == "user":
            break
        if role == "assistant":
            content = content_to_text(msg.get("content")).strip()
            if len(content) > 500:
                return content

    # Fallback — склеить все непустые assistant после последнего user
    parts: list[str] = []
    for i in range(len(session.messages) - 1, -1, -1):
        msg = session.messages[i]
        role = msg.get("role")
        if role == "user":
            break
        if role == "assistant":
            content = content_to_text(msg.get("content")).strip()
            if content:
                parts.append(content)
    parts.reverse()
    return "\n\n---\n\n".join(parts) if parts else "(пусто)"


def save_audit_report(
    session: Session,
    also_in_workspace: bool = False,
) -> Path:
    """Сохранить audit-отчёт в .md. Возвращает путь к .md.

    Файл: sessions/audits/<session_id>_<YYYYMMDD_HHMMSS>.md
    Если also_in_workspace=True — дополнительно копирует .md в workspace проекта.
    """
    report_text = extract_audit_report(session)

    audits_dir = config.SESSIONS_DIR / "audits"
    audits_dir.mkdir(parents=True, exist_ok=True)

    ts = time.strftime("%Y%m%d_%H%M%S")
    stem = f"{session.id}_{ts}"

    md_path = audits_dir / f"{stem}.md"

    # Markdown
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
    md_path.write_text(header + report_text, encoding="utf-8")

    # Опционально — в workspace проекта
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
