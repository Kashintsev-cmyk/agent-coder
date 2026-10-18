"""Telegram-хендлеры миграции: /migrate + callbacks mig:*.

Двухшаговый UX: dry-run отчёт -> inline [✅ Применить] [❌ Отмена].
После apply — [📊 diff] [↩️ откат] [ok]. .notes.md не трогается.
Core — agent_coder.migrate.migrate_one; здесь только UI.
"""

from __future__ import annotations

import logging

from aiogram import F
from aiogram.filters import Command
from aiogram.types import CallbackQuery, Message

from agent_coder.migrate import migrate_one
from agent_coder.shell_runner import run_shell
from agent_coder.standard import (
    bots_dir,
    collect_issues,
    notes_dir,
    project_dir,
    read_notes,
    render_markdown,
    summarize,
)
from agent_coder.telegram import keyboards
from agent_coder.telegram.renderer import html_escape

from .project_utils import _validate_project_name
from .shared import router

logger = logging.getLogger(__name__)


def _list_migratable() -> list[str]:
    """Проекты в bots/ с pyproject.toml. Только для /migrate без аргумента."""
    root = bots_dir()
    if not root.is_dir():
        return []
    out: list[str] = []
    for child in root.iterdir():
        if not child.is_dir() or child.name.startswith((".", "_")):
            continue
        if (child / "pyproject.toml").is_file():
            out.append(child.name)
    return sorted(out)


def _dry_run_report(name: str) -> str:
    summary = summarize(collect_issues(project_dir(name)))
    notes = read_notes(name)
    md = render_markdown(name, summary, notes_md=notes)
    md_path = notes_dir() / f"{name}.md"
    would_change = not (md_path.is_file() and md_path.read_text(encoding="utf-8") == md)
    md_status = "will change" if would_change else "unchanged"
    notes_status = f"{len(notes.splitlines())} lines" if notes.strip() else "absent"
    return (
        f"🔧 <b>Миграция {html_escape(name)}</b>\n\n"
        f"Score: <b>{summary['score']}</b> "
        f"(major={summary['majors']}, minor={summary['minors']})\n"
        f"state: <code>{html_escape(str(project_dir(name)))}</code>\n"
        f"md: <code>{html_escape(str(md_path))}</code> ({md_status})\n"
        f"notes: {notes_status}\n\n"
        f"Записать state и перегенерировать .md?"
    )


def _chunks(text: str, n: int = 3500) -> list[str]:
    return [text[i : i + n] for i in range(0, len(text), n)] or [""]


@router.message(Command("migrate"))
async def cmd_migrate(message: Message) -> None:
    parts = (message.text or "").split(maxsplit=1)
    if len(parts) == 1 or not parts[1].strip():
        names = _list_migratable()
        if not names:
            await message.answer(
                "❌ Нет проектов с pyproject.toml в bots/", parse_mode="HTML"
            )
            return
        await message.answer(
            "🔧 <b>Миграция</b>\nВыбери проект:",
            reply_markup=keyboards.migrate_projects_keyboard(names),
            parse_mode="HTML",
        )
        return

    name = parts[1].strip().split()[0]
    err = _validate_project_name(name)
    if err:
        await message.answer(f"❌ {html_escape(err)}", parse_mode="HTML")
        return
    if not project_dir(name).is_dir():
        await message.answer(
            f"❌ Нет проекта: <code>{html_escape(name)}</code>", parse_mode="HTML"
        )
        return
    await message.answer(
        _dry_run_report(name),
        reply_markup=keyboards.migrate_confirm_keyboard(name),
        parse_mode="HTML",
    )


@router.callback_query(F.data.startswith("mig:dry:"))
async def cb_migrate_dry(query: CallbackQuery) -> None:
    name = query.data.split(":", 2)[2]
    err = _validate_project_name(name)
    if err:
        await query.answer("Некорректное имя", show_alert=True)
        return
    try:
        await query.message.edit_text(
            _dry_run_report(name),
            reply_markup=keyboards.migrate_confirm_keyboard(name),
            parse_mode="HTML",
        )
    except Exception as e:
        logger.warning("mig:dry edit failed: %s", e)
        await query.answer("Не удалось обновить", show_alert=True)
    await query.answer()


@router.callback_query(F.data.startswith("mig:apply:"))
async def cb_migrate_apply(query: CallbackQuery) -> None:
    name = query.data.split(":", 2)[2]
    err = _validate_project_name(name)
    if err:
        await query.answer("Некорректное имя", show_alert=True)
        return
    await query.answer("Применяю...")
    r = migrate_one(name, write=True)
    if "error" in r:
        await query.message.edit_text(
            f"❌ Ошибка: {html_escape(r['error'])} "
            f"(<code>{html_escape(r.get('path', '?'))}</code>)",
            parse_mode="HTML",
        )
        return
    changed = "changed" if r["md_changed"] else "unchanged"
    await query.message.edit_text(
        f"✅ <b>{html_escape(name)}</b>\n\n"
        f"Score: <b>{r['score']}</b> "
        f"(major={r['majors']}, minor={r['minors']})\n"
        f"state: <code>{html_escape(r['state'])}</code>\n"
        f"md: <code>{html_escape(r['md'])}</code> ({changed})",
        reply_markup=keyboards.migrate_report_keyboard(name),
        parse_mode="HTML",
    )


@router.callback_query(F.data.startswith("mig:no"))
async def cb_migrate_no(query: CallbackQuery) -> None:
    try:
        await query.message.edit_text("❌ Отменено", reply_markup=None)
    except Exception:
        pass
    await query.answer()


@router.callback_query(F.data.startswith("mig:diff:"))
async def cb_migrate_diff(query: CallbackQuery) -> None:
    name = query.data.split(":", 2)[2]
    err = _validate_project_name(name)
    if err:
        await query.answer("Некорректное имя", show_alert=True)
        return
    await query.answer()
    r = await run_shell(f"git diff -- {name}.md", cwd=notes_dir(), timeout=15)
    text = (r.stdout or r.stderr or "(нет изменений)").strip() or "(нет изменений)"
    chunks = _chunks(text)
    for chunk in chunks[:3]:
        await query.message.answer(
            f"<pre>{html_escape(chunk)}</pre>", parse_mode="HTML"
        )
    if len(chunks) > 3:
        await query.message.answer(
            f"… ещё {len(chunks) - 3} частей; полный diff — "
            f"<code>git -C {html_escape(str(notes_dir()))} diff -- {html_escape(name)}.md</code>",
            parse_mode="HTML",
        )


@router.callback_query(F.data.startswith("mig:revert:"))
async def cb_migrate_revert(query: CallbackQuery) -> None:
    name = query.data.split(":", 2)[2]
    err = _validate_project_name(name)
    if err:
        await query.answer("Некорректное имя", show_alert=True)
        return
    await query.answer("Откатываю...")
    r = await run_shell(f"git checkout -- {name}.md", cwd=notes_dir(), timeout=15)
    if r.ok and not r.stderr.strip():
        await query.message.edit_text(
            f"↩️ <b>Откат {html_escape(name)}.md</b>\n"
            f"<code>{html_escape(r.stdout.strip() or 'ok')}</code>",
            reply_markup=keyboards.migrate_report_keyboard(name),
            parse_mode="HTML",
        )
    else:
        err_text = (r.stderr or r.output or r.error or "?")[:300]
        await query.message.edit_text(
            f"❌ Откат не удался: <code>{html_escape(err_text)}</code>",
            parse_mode="HTML",
        )
