"""Сохранение audit-отчёта в Markdown и PDF.

Используется после завершения задачи в Audit-режиме. Извлекает финальный
отчёт из истории сессии, сохраняет в двух форматах, возвращает пути.
"""
from __future__ import annotations

import logging
import time
from pathlib import Path

import markdown
from fpdf import FPDF

from agent_coder.agent.session import Session
from agent_coder.config import config

logger = logging.getLogger(__name__)


# DejaVu — стандартный шрифт Ubuntu, поддерживает кириллицу
_DEJAVU_PATHS = [
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "/usr/share/fonts/TTF/DejaVuSans.ttf",
    "/usr/share/fonts/dejavu/DejaVuSans.ttf",
]
_DEJAVU_MONO_PATHS = [
    "/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf",
    "/usr/share/fonts/TTF/DejaVuSansMono.ttf",
]


def _find_font(paths: list[str]) -> str | None:
    for p in paths:
        if Path(p).exists():
            return p
    return None


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
            content = (msg.get("content") or "").strip()
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
            content = (msg.get("content") or "").strip()
            if content:
                parts.append(content)
    parts.reverse()
    return "\n\n---\n\n".join(parts) if parts else "(пусто)"


def _make_pdf(md_text: str, title: str, out_path: Path) -> None:
    """MD → HTML → PDF через fpdf2.write_html."""
    html = markdown.markdown(
        md_text,
        extensions=["tables", "fenced_code", "sane_lists", "nl2br"],
    )

    styled = f"""<html><body>
    <h1>{title}</h1>
    {html}
    </body></html>"""

    pdf = FPDF(orientation="P", unit="mm", format="A4")
    pdf.set_auto_page_break(auto=True, margin=15)

    regular = _find_font(_DEJAVU_PATHS)
    mono = _find_font(_DEJAVU_MONO_PATHS) or regular
    bold = _find_font([
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        "/usr/share/fonts/TTF/DejaVuSans-Bold.ttf",
    ])

    if regular:
        # Регистрируем все 4 стиля. Отсутствующие — fallback на regular,
        # иначе write_html падает на первом же <b> или <i>.
        pdf.add_font("DejaVu", "", regular)
        pdf.add_font("DejaVu", "B", bold or regular)
        pdf.add_font("DejaVu", "I", regular)         # italic-fallback
        pdf.add_font("DejaVu", "BI", bold or regular)  # bold-italic-fallback

        pdf.add_font("Mono", "", mono)
        pdf.add_font("Mono", "B", mono)
        pdf.add_font("Mono", "I", mono)
        pdf.add_font("Mono", "BI", mono)
        family = "DejaVu"
    else:
        logger.warning("DejaVu не найден, PDF будет без кириллицы")
        family = "Helvetica"

    pdf.add_page()
    pdf.set_font(family, size=11)

    try:
        pdf.write_html(styled, font_family=family)
    except Exception as e:
        logger.warning("write_html упал, plain fallback: %s", e)
        # Восстанавливаем состояние: при падении внутри write_html
        # fpdf мог оставить pdf без открытой страницы.
        try:
            if not getattr(pdf, "pages", None):
                pdf.add_page()
        except Exception:
            pass
        try:
            pdf.set_font(family, size=10)
            pdf.multi_cell(0, 5, md_text[:10000])
        except Exception as e2:
            logger.error("plain fallback тоже упал: %s", e2)
            # Последний шанс — минимальный PDF с сообщением
            try:
                pdf.add_page()
                pdf.set_font("Helvetica", size=11)
                pdf.multi_cell(0, 5, "PDF generation failed.")
            except Exception:
                pass

    pdf.output(str(out_path))


def save_audit_report(
    session: Session,
    also_in_workspace: bool = False,
) -> tuple[Path, Path]:
    """Сохранить audit-отчёт в .md и .pdf. Возвращает пути (md, pdf).

    Файлы: sessions/audits/<session_id>_<YYYYMMDD_HHMMSS>.{md,pdf}
    Если also_in_workspace=True — дополнительно копирует .md в workspace проекта.
    """
    report_text = extract_audit_report(session)

    audits_dir = config.SESSIONS_DIR / "audits"
    audits_dir.mkdir(parents=True, exist_ok=True)

    ts = time.strftime("%Y%m%d_%H%M%S")
    stem = f"{session.id}_{ts}"

    md_path = audits_dir / f"{stem}.md"
    pdf_path = audits_dir / f"{stem}.pdf"

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

    # PDF
    title = f"Аудит: {Path(session.workspace).name}"
    _make_pdf(md_path.read_text(encoding="utf-8"), title, pdf_path)

    # Опционально — в workspace проекта
    if also_in_workspace:
        try:
            ws = Path(session.workspace)
            if ws.is_dir():
                (ws / "AUDIT_REPORT.md").write_text(
                    md_path.read_text(encoding="utf-8"), encoding="utf-8"
                )
                (ws / "AUDIT_REPORT.pdf").write_bytes(pdf_path.read_bytes())
        except Exception as e:
            logger.warning("не смог скопировать отчёт в workspace: %s", e)

    return md_path, pdf_path
