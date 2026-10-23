"""Хендлеры меню /server и /audit (read-only диагностика).

Выполняют shell-команды напрямую, минуя LLM. Сгруппированы по категориям
через server_ops.SERVER_GROUPS и audit_ops.AUDIT_GROUPS.
"""

from __future__ import annotations

import logging
import tempfile

from aiogram import F
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command
from aiogram.types import CallbackQuery, Message

from agent_coder.shell_runner import run_shell
from agent_coder.telegram import keyboards
from agent_coder.telegram.renderer import html_escape

from .shared import _get_active_session, router

logger = logging.getLogger(__name__)


# ---------- Server menu ----------


@router.message(Command("server"))
async def cmd_server(message: Message) -> None:
    """Открывает меню серверной диагностики."""
    await message.answer(
        "🖥 <b>Сервер</b>\n\n"
        "Быстрая диагностика. Команды выполняются мгновенно, "
        "без обращения к модели и без подтверждений.",
        reply_markup=keyboards.server_menu_keyboard(),
        parse_mode="HTML",
    )


@router.callback_query(F.data.startswith("srv_grp:"))
async def cb_server_group(query: CallbackQuery) -> None:
    """Открывает подменю группы серверных команд."""
    group = query.data.split(":", 1)[1]
    from agent_coder.server_ops import ops_in_group

    if not ops_in_group(group):
        await query.answer("Пустая группа", show_alert=True)
        return
    try:
        await query.message.edit_reply_markup(
            reply_markup=keyboards.server_group_keyboard(group),
        )
    except TelegramBadRequest as e:
        if "message is not modified" not in str(e).lower():
            raise
    await query.answer()


@router.callback_query(F.data == "srv_back")
async def cb_server_back(query: CallbackQuery) -> None:
    """Возврат из подменю группы к списку групп."""
    try:
        await query.message.edit_reply_markup(
            reply_markup=keyboards.server_menu_keyboard(),
        )
    except TelegramBadRequest as e:
        if "message is not modified" not in str(e).lower():
            raise
    await query.answer()


@router.callback_query(F.data.startswith("srv:"))
async def cb_server_op(query: CallbackQuery) -> None:
    from agent_coder.server_ops import find_op, op_group

    op_id = query.data.split(":", 1)[1]
    op = find_op(op_id)
    if op is None:
        await query.answer("Неизвестная команда", show_alert=True)
        return

    op_kb = keyboards.server_group_keyboard(op_group(op_id))

    await query.answer(f"Выполняю: {op.label}")
    await query.message.edit_text(
        f"🖥 <b>{op.label}</b> — {op.description}\n\n"
        f"<code>$ {op.cmd}</code>\n\n"
        f"⏳ Выполняю...",
        parse_mode="HTML",
    )

    # Выполняем напрямую, минуя LLM
    result = await run_shell(op.cmd, cwd=tempfile.gettempdir(), timeout=30)
    if result.timed_out:
        await query.message.edit_text(
            f"🖥 <b>{op.label}</b>\n\n⏱ {result.error}",
            reply_markup=op_kb,
            parse_mode="HTML",
        )
        return
    if result.error:
        await query.message.edit_text(
            f"🖥 <b>{op.label}</b>\n\n❌ Ошибка: <code>{html_escape(result.error)}</code>",
            reply_markup=op_kb,
            parse_mode="HTML",
        )
        return

    output = result.output
    if len(output) > 3500:
        output = output[:3500] + "\n... (обрезано)"

    await query.message.edit_text(
        f"🖥 <b>{op.label}</b> — {op.description}\n\n<pre>{html_escape(output)}</pre>",
        reply_markup=op_kb,
        parse_mode="HTML",
    )


# ---------- /verify (B9) ----------


@router.message(Command("verify"))
async def cmd_verify(message: Message) -> None:
    """Верифицировать work_state аудита на модели-фильтре.

    /verify            — config.VERIFY_MODEL_ID (haiku-5.5, дешёво)
    /verify <model_id> — явный override (дорого — осознанно)

    НЕ наследует session.model / model_manual: смысл verify — экономия.
    """
    from agent_coder.agent.verify import run_verify
    from agent_coder.config import config
    from agent_coder.llm.client import LLMClient
    from agent_coder.tools import build_registry

    session = _get_active_session(message.chat.id)
    if not session:
        await message.answer("Сначала выбери проект: /projects")
        return
    if not session.work_state or not any(session.work_state.values()):
        await message.answer(
            "⚠ work_state пуст — сначала /audit (или update_work_state)."
        )
        return

    parts = (message.text or "").split(maxsplit=1)
    arg_model = parts[1].strip() if len(parts) > 1 else ""
    if arg_model and arg_model not in config.MODEL_IDS:
        await message.answer(
            f"❌ Модель <code>{html_escape(arg_model)}</code> не в MODEL_IDS.",
            parse_mode="HTML",
        )
        return
    used_model = arg_model or config.VERIFY_MODEL_ID
    hint = "" if arg_model else " (дефолт; /verify &lt;model&gt; — сменить)"
    status_msg = await message.answer(
        f"🔍 Верифицирую work_state на <code>{html_escape(used_model)}</code>{hint}...",
        parse_mode="HTML",
    )

    client = LLMClient()
    await client.start()
    try:
        result = await run_verify(
            client=client,
            session=session,
            model=used_model,
            registry=build_registry(),
        )
    finally:
        await client.close()

    lines = [f"🔍 <b>Verify</b> · <code>{html_escape(result.model)}</code>"]
    if result.error:
        lines.append(f"❌ {html_escape(result.error)}")
    else:
        lines.append(f"Итераций: {result.iterations} · {result.cost:.2f}₽")
        counts = result.counts or {}
        total = sum(counts.values())
        lines.append(f"Вердиктов: {total}")
        for k in ("confirmed", "rejected", "uncertain", "unparsed"):
            if counts.get(k):
                lines.append(f"  • {k}: {counts[k]}")
        conf = [
            v
            for v in session.verify_results
            if v.get("run_id") == result.run_id and v.get("status") == "confirmed"
        ]
        if conf:
            lines.append("\n<b>Подтверждено:</b>")
            for v in conf[:10]:
                ev = (
                    f" — <code>{html_escape(v['evidence'])}</code>"
                    if v.get("evidence")
                    else ""
                )
                lines.append(f"• {html_escape(v['text'])}{ev}")
            if len(conf) > 10:
                lines.append(f"… ещё {len(conf) - 10}")
        if result.budget_exceeded:
            lines.append("\n⚠ Бюджет verify исчерпан, вердикты частичные.")
    await status_msg.edit_text("\n".join(lines), parse_mode="HTML")


@router.message(Command("patch"))
async def cmd_patch(message: Message) -> None:
    """Починить confirmed findings из /verify.

    /patch            — config.PATCH_MODEL_ID (deepseek, дёшево)
    /patch <model_id> — явный override (дорого — осознанно)

    Не наследует session.model / model_manual — как /verify.
    /yolo игнорируется: сам вызов /patch = подтверждение.
    """
    from agent_coder.agent.patch import run_patch
    from agent_coder.config import config
    from agent_coder.llm.client import LLMClient
    from agent_coder.tools import build_registry

    session = _get_active_session(message.chat.id)
    if not session:
        await message.answer("Сначала выбери проект: /projects")
        return
    confirmed = [v for v in session.verify_results if v.get("status") == "confirmed"]
    if not confirmed:
        await message.answer(
            "⚠ Нет confirmed findings — сначала /verify (после /audit)."
        )
        return

    parts = (message.text or "").split(maxsplit=1)
    arg_model = parts[1].strip() if len(parts) > 1 else ""
    if arg_model and arg_model not in config.MODEL_IDS:
        await message.answer(
            f"❌ Модель <code>{html_escape(arg_model)}</code> не в MODEL_IDS.",
            parse_mode="HTML",
        )
        return
    used_model = arg_model or config.PATCH_MODEL_ID
    hint = "" if arg_model else " (дефолт; /patch &lt;model&gt; — сменить)"
    status_msg = await message.answer(
        f"🔧 Патчу <b>{len(confirmed)}</b> confirmed на "
        f"<code>{html_escape(used_model)}</code>{hint}...",
        parse_mode="HTML",
    )

    client = LLMClient()
    await client.start()
    try:
        result = await run_patch(
            client=client,
            session=session,
            model=used_model,
            registry=build_registry(),
        )
    finally:
        await client.close()

    lines = [f"🔧 <b>Patch</b> · <code>{html_escape(result.model)}</code>"]
    if result.error:
        lines.append(f"❌ {html_escape(result.error)}")
    else:
        lines.append(f"Итераций: {result.iterations} · {result.cost:.2f}₽")
        if result.files_changed:
            lines.append(f"\n<b>Изменено файлов:</b> {len(result.files_changed)}")
            for fp in result.files_changed[:10]:
                lines.append(f"  • <code>{html_escape(fp)}</code>")
            if len(result.files_changed) > 10:
                lines.append(f"  … ещё {len(result.files_changed) - 10}")
        else:
            lines.append(
                "\n⚠ Модель не изменила файлов (возможно, finding не "
                "подтвердился при чтении)."
            )
        if result.git_diff_stat:
            lines.append(
                f"\n<b>git diff:</b>\n<pre>{html_escape(result.git_diff_stat[:3000])}</pre>"
            )
        if result.budget_exceeded:
            lines.append(
                f"\n⚠ Бюджет patch исчерпан "
                f"(лимит {config.MAX_COST_PATCH:.0f}₽), правки частичные."
            )
        if result.raw_text:
            lines.append(
                f"\n<b>Отчёт:</b>\n<pre>{html_escape(result.raw_text[:1500])}</pre>"
            )
    await status_msg.edit_text("\n".join(lines), parse_mode="HTML")


# ---------- Audit menu ----------

AUDIT_TIMEOUT_SEC = 180


@router.message(Command("audit"))
async def cmd_audit(message: Message) -> None:
    session = _get_active_session(message.chat.id)
    if not session:
        await message.answer("Сначала выбери проект: /projects")
        return
    await message.answer(
        f"🔒 <b>Аудит проекта</b>\n\n"
        f"<code>{session.workspace}</code>\n\n"
        f"Бесплатные проверки — без обращения к модели.",
        reply_markup=keyboards.audit_menu_keyboard(),
        parse_mode="HTML",
    )


@router.callback_query(F.data.startswith("aud_grp:"))
async def cb_audit_group(query: CallbackQuery) -> None:
    """Открывает подменю группы audit-команд."""
    group = query.data.split(":", 1)[1]
    from agent_coder.audit_ops import ops_in_group

    if not ops_in_group(group):
        await query.answer("Пустая группа", show_alert=True)
        return
    try:
        await query.message.edit_reply_markup(
            reply_markup=keyboards.audit_group_keyboard(group),
        )
    except TelegramBadRequest as e:
        if "message is not modified" not in str(e).lower():
            raise
    await query.answer()


@router.callback_query(F.data == "aud_back")
async def cb_audit_back(query: CallbackQuery) -> None:
    """Возврат из подменю группы к списку групп."""
    try:
        await query.message.edit_reply_markup(
            reply_markup=keyboards.audit_menu_keyboard(),
        )
    except TelegramBadRequest as e:
        if "message is not modified" not in str(e).lower():
            raise
    await query.answer()


@router.callback_query(F.data.startswith("aud:"))
async def cb_audit_op(query: CallbackQuery) -> None:
    from agent_coder.audit_ops import find_op, op_group

    session = _get_active_session(query.message.chat.id)
    if not session:
        await query.answer("Нет сессии", show_alert=True)
        return

    op_id = query.data.split(":", 1)[1]
    op = find_op(op_id)
    if op is None:
        await query.answer("Неизвестная команда", show_alert=True)
        return

    op_kb = keyboards.audit_group_keyboard(op_group(op_id))
    cmd = op.cmd.replace("{workspace}", session.workspace)

    await query.answer(f"Выполняю: {op.label}")
    await query.message.edit_text(
        f"🔒 <b>{op.label}</b> — {op.description}\n\n⏳ Выполняю...",
        parse_mode="HTML",
    )

    result = await run_shell(cmd, cwd=session.workspace, timeout=AUDIT_TIMEOUT_SEC)
    if result.timed_out:
        await query.message.edit_text(
            f"🔒 <b>{op.label}</b>\n\n⏱ {result.error}",
            reply_markup=op_kb,
            parse_mode="HTML",
        )
        return
    if result.error:
        await query.message.edit_text(
            f"🔒 <b>{op.label}</b>\n\n❌ Ошибка: <code>{html_escape(result.error)}</code>",
            reply_markup=op_kb,
            parse_mode="HTML",
        )
        return

    output = result.output
    if len(output) > 3500:
        output = output[:3500] + "\n... (обрезано)"

    await query.message.edit_text(
        f"🔒 <b>{op.label}</b> — {op.description}\n\n<pre>{html_escape(output)}</pre>",
        reply_markup=op_kb,
        parse_mode="HTML",
    )
