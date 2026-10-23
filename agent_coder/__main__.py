"""CLI: одиночный запуск и REPL."""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from pathlib import Path

from agent_coder import __version__
from agent_coder.agent.loop import AgentLoop, LoopEvents
from agent_coder.agent.prompts import Mode
from agent_coder.agent.session import Session
from agent_coder.config import config
from agent_coder.llm.client import LLMClient
from agent_coder.tools import build_registry
from agent_coder.ui import terminal as ui


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(prog="agent-coder", description="CLI coding agent")
    p.add_argument("task", nargs="?", help="Задача. Если не указана — REPL")
    p.add_argument("--cwd", default=config.DEFAULT_WORKSPACE, help="Рабочая директория")
    p.add_argument(
        "--model",
        default=None,
        help="Модель RouterAI (по умолчанию — config.DEFAULT_MODEL_ID). "
        "Явный --model ставит model_manual=True (роутер не перекрывает).",
    )
    p.add_argument("--plan", action="store_true", help="Plan-режим")
    p.add_argument("--audit", action="store_true", help="Audit-режим: только чтение")
    p.add_argument(
        "--patch",
        action="store_true",
        help="One-shot /patch: починить confirmed findings (требует /verify)",
    )
    p.add_argument(
        "--verify",
        action="store_true",
        help="One-shot /verify: прогнать work_state.findings через VERIFY_MODEL_ID",
    )
    p.add_argument(
        "--resume-audit",
        action="store_true",
        help="Восстановить persistent audit state (.agent-coder/)",
    )
    p.add_argument(
        "--fresh-audit",
        action="store_true",
        help="Сбросить .agent-coder/ и начать аудит с нуля",
    )
    p.add_argument(
        "--scope",
        action="append",
        metavar="PATH",
        help="B11.3: AUDIT только по пути (можно несколько; требует --audit)",
    )
    p.add_argument(
        "--scope-file",
        metavar="FILE",
        help="B11.3: файл со списком путей, по одному в строке (требует --audit)",
    )
    p.add_argument("--yolo", action="store_true", help="Без подтверждений")
    p.add_argument("--resume", metavar="ID", help="Возобновить сессию по ID")
    p.add_argument(
        "--max-iterations",
        type=int,
        default=None,
        help="Переопределить лимит итераций (для всех режимов). "
        "По умолчанию берётся MAX_ITERATIONS_<MODE> из .env",
    )
    p.add_argument("--verbose", "-v", action="store_true")
    p.add_argument("--version", action="version", version=f"agent-coder {__version__}")
    p.add_argument(
        "--continue",
        dest="continue_last",
        action="store_true",
        help="Продолжить последнюю сессию для этой workspace",
    )
    return p.parse_args()


def _events() -> LoopEvents:
    return LoopEvents(
        on_assistant_text=ui.print_assistant_text,
        on_tool_call=ui.print_tool_call,
        on_tool_result=ui.print_tool_result,
        on_usage=ui.print_usage,
        on_error=ui.print_error,
        request_approval=ui.ask_approval,
    )


async def run_once(args: argparse.Namespace, session: Session) -> int:
    client = LLMClient()
    try:
        await client.start()
        loop = AgentLoop(
            client=client,
            registry=build_registry(),
            session=session,
            events=_events(),
            yolo=args.yolo or session.yolo,
        )
        await loop.run(args.task)
        ui.print_total_usage(session.total_usage, session.request_count)
        session.save()
        return 0
    finally:
        try:
            await client.close()
        except Exception as e:
            logging.getLogger(__name__).warning("client.close() failed: %s", e)


async def run_repl(args: argparse.Namespace, session: Session) -> int:
    client = LLMClient()
    try:
        await client.start()
        registry = build_registry()
        ui.print_banner(
            session_id=session.id,
            workspace=session.workspace,
            model=session.model,
            mode=session.mode.value,
        )
        ui.print_current_session(session)
        ui.print_info("Напиши задачу или /help для списка команд.")

        while True:
            try:
                line = ui.print_user_prompt(session.mode.value)
            except (EOFError, KeyboardInterrupt):
                break

            line = line.strip()
            if not line:
                continue

            if line.startswith("/"):
                if await _handle_slash(line, session):
                    break
                continue

            loop = AgentLoop(
                client=client,
                registry=registry,
                session=session,
                events=_events(),
                yolo=args.yolo or session.yolo,
            )
            try:
                await loop.run(line)
            except KeyboardInterrupt:
                ui.print_info("Прервано.")
            session.save()

        ui.print_info(f"\nСессия сохранена: {session.id}")
        ui.print_info(f"Возобновить: agent-coder --resume {session.id}")
        return 0
    finally:
        try:
            await client.close()
        except Exception as e:
            logging.getLogger(__name__).warning("client.close() failed: %s", e)


async def _handle_slash(line: str, session: Session) -> bool:
    parts = line[1:].split(maxsplit=1)
    cmd = parts[0].lower()
    arg = parts[1] if len(parts) > 1 else ""

    if cmd in ("exit", "quit", "q"):
        return True
    if cmd == "help":
        _print_help()
    elif cmd == "plan":
        session.mode = Mode.PLAN
        session.messages = []
        ui.print_success("Режим: plan (только чтение)")
    elif cmd == "act":
        session.mode = Mode.ACT
        session.messages = []
        ui.print_success("Режим: act (можно менять)")
    elif cmd == "audit":
        session.mode = Mode.AUDIT
        session.messages = []
        ui.print_success("Режим: audit (строгий аудитор, только чтение)")
    elif cmd == "cost":
        ui.print_total_usage(session.total_usage, session.request_count)
    elif cmd == "session":
        ui.print_current_session(session)
    elif cmd == "sessions":
        ui.print_sessions_list(Session.list_all())
    elif cmd == "title":
        if not arg:
            ui.print_info(f"Текущий заголовок: {session.title}")
        else:
            session.rename(arg)
            session.save()
            ui.print_success(f"Заголовок: {session.title}")
    elif cmd == "switch":
        if not arg:
            ui.print_error("Укажи ID: /switch <id>")
        else:
            other = Session.load(arg.strip())
            if other is None:
                ui.print_error(f"Сессия не найдена: {arg}")
            else:
                session.__dict__.update(other.__dict__)
                ui.print_success(f"Переключено на {session.id}: {session.title}")
                ui.print_current_session(session)
    elif cmd == "model":
        _model_picker(session)
    elif cmd == "reasoning":
        _reasoning_picker(session)
    elif cmd == "yolo":
        if arg and arg.strip().lower() not in ("on", "off"):
            ui.print_error("Использование: /yolo (toggle) или /yolo on|off")
        else:
            session.yolo = (arg.strip().lower() == "on") if arg else not session.yolo
            session.save()
            if session.yolo:
                ui.print_success("⚡ YOLO включён: опасные операции без подтверждения.")
            else:
                ui.print_success("🔒 YOLO выключен: подтверждения включены.")
    elif cmd == "verify":
        from agent_coder.agent.verify import run_verify
        from agent_coder.llm.client import LLMClient
        from agent_coder.tools import build_registry

        if not session.work_state or not any(session.work_state.values()):
            ui.print_error("work_state пуст — сначала /audit или update_work_state")
        else:
            arg_model = arg.strip() if arg else ""
            if arg_model and arg_model not in config.MODEL_IDS:
                ui.print_error(f"Модель {arg_model} не в MODEL_IDS")
            else:
                used = arg_model or config.VERIFY_MODEL_ID
                ui.print_info(f"Верифицирую work_state на {used}...")
                client = LLMClient()
                await client.start()
                try:
                    result = await run_verify(
                        client=client,
                        session=session,
                        model=used,
                        registry=build_registry(),
                    )
                finally:
                    await client.close()
                if result.error:
                    ui.print_error(result.error)
                else:
                    ui.print_success(
                        f"Готово: {result.iterations} итераций, {result.cost:.2f}₽"
                    )
                    for k, v in (result.counts or {}).items():
                        ui.print_info(f"  {k}: {v}")
    elif cmd == "patch":
        from agent_coder.agent.patch import run_patch
        from agent_coder.llm.client import LLMClient
        from agent_coder.tools import build_registry

        confirmed = [
            v for v in session.verify_results if v.get("status") == "confirmed"
        ]
        if not confirmed:
            ui.print_error("Нет confirmed findings — сначала /verify")
        else:
            arg_model = arg.strip() if arg else ""
            if arg_model and arg_model not in config.MODEL_IDS:
                ui.print_error(f"Модель {arg_model} не в MODEL_IDS")
            else:
                used = arg_model or config.PATCH_MODEL_ID
                ui.print_info(f"Патчу {len(confirmed)} confirmed на {used}...")
                client = LLMClient()
                await client.start()
                try:
                    result = await run_patch(
                        client=client,
                        session=session,
                        model=used,
                        registry=build_registry(),
                    )
                finally:
                    await client.close()
                if result.error:
                    ui.print_error(result.error)
                else:
                    ui.print_success(
                        f"Готово: {result.iterations} итераций, {result.cost:.2f}₽"
                    )
                    if result.files_changed:
                        ui.print_info(f"Изменено файлов: {len(result.files_changed)}")
                        for fp in result.files_changed:
                            ui.print_info(f"  {fp}")
                    if result.git_diff_stat:
                        ui.print_info(result.git_diff_stat)
                    if result.budget_exceeded:
                        ui.print_info(
                            f"⚠ Бюджет patch исчерпан "
                            f"({config.MAX_COST_PATCH:.0f}₽), правки частичные."
                        )
    elif cmd == "clear":
        session.messages = []
        ui.print_success("История очищена (стоимость сохранена).")
    elif cmd == "history":
        _print_history(session)
    else:
        ui.print_error(f"Неизвестная команда: /{cmd}. /help для справки.")
    return False


def _print_help() -> None:
    ui.print_info(
        "Режимы:\n"
        "  /plan          — Plan (только чтение)\n"
        "  /act           — Act (можно менять)\n"
        "  /audit         — Audit (строгий аудитор)\n"
        "\n"
        "Сессия:\n"
        "  /session       — текущая сессия\n"
        "  /sessions      — список всех сессий\n"
        "  /title <text>  — задать заголовок\n"
        "  /switch <id>   — переключиться на сессию\n"
        "  /clear         — очистить историю\n"
        "  /cost          — стоимость сессии\n"
        "\n"
        "Настройки:\n"
        "  /model         — выбрать модель\n"
        "  /reasoning     — уровень thinking\n"
        "  /yolo [on|off] — без подтверждений опасных операций\n"
        "\n"
        "Прочее:\n"
        "  /verify [model] — проверить findings аудита (haiku-5.5)\n"
        "  /patch [model]  — починить confirmed findings (deepseek)\n"
        "  /history       — последние сообщения\n"
        "  /help          — справка\n"
        "  /exit          — выход"
    )


def _print_history(session: Session) -> None:
    for msg in session.messages[-20:]:
        role = msg.get("role")
        content = str(msg.get("content") or "")[:200]
        ui.print_info(f"[{role}] {content}")


def _model_picker(session: Session) -> None:
    from agent_coder.model_catalog import catalog
    from agent_coder.models import get_model_name

    models = catalog.all()
    ui.print_info(
        f"\nТекущая модель: {get_model_name(session.model)} ({session.model})\n"
    )
    ui.print_info("Доступные модели:")
    for i, m in enumerate(models, 1):
        mark = "*" if m.id == session.model else " "
        ui.print_info(
            f"  {mark} {i:2}. {m.name:<25} "
            f"· {m.input_price:.1f}/{m.output_price:.0f}₽ · {m.context // 1000}k · {m.tag}"
        )
    choice = ui.console.input("\nНомер модели [Enter — отмена]: ").strip()
    if not choice:
        return
    try:
        idx = int(choice) - 1
        m = models[idx]
    except (ValueError, IndexError):
        ui.print_error("Неверный номер")
        return
    session.model = m.id
    session.messages = []
    session.save()
    ui.print_success(f"Модель: {m.name} ({m.id}), история очищена")


def _reasoning_picker(session: Session) -> None:
    from agent_coder.models import REASONING_LEVELS, find_model

    m = find_model(session.model)
    if m and not m.reasoning:
        ui.print_error(f"Модель {session.model} не поддерживает reasoning")
        return
    ui.print_info(f"\nТекущий reasoning: {session.reasoning_effort}\n")
    for i, level in enumerate(REASONING_LEVELS, 1):
        mark = "*" if level == session.reasoning_effort else " "
        ui.print_info(f"  {mark} {i}. {level}")
    choice = ui.console.input("\nНомер [Enter — отмена]: ").strip()
    if not choice:
        return
    try:
        idx = int(choice) - 1
        level = REASONING_LEVELS[idx]
    except (ValueError, IndexError):
        ui.print_error("Неверный номер")
        return
    session.reasoning_effort = level
    session.save()
    ui.print_success(f"Reasoning: {level}")


def _validate_audit_flags(args) -> str | None:
    """Валидация audit-флагов (B11.2b, B11.3).

    None — ОК, иначе текст ошибки. --resume-audit / --fresh-audit /
    --scope / --scope-file без --audit бессмысленны; взаимоисключающие
    пары: resume↔fresh, scope↔scope-file.
    """
    resume = getattr(args, "resume_audit", False)
    fresh = getattr(args, "fresh_audit", False)
    scope = getattr(args, "scope", None) or []
    scope_file = getattr(args, "scope_file", None)
    if resume and fresh:
        return "--resume-audit и --fresh-audit взаимоисключающие"
    if (resume or fresh) and not getattr(args, "audit", False):
        return "--resume-audit / --fresh-audit требуют --audit"
    if (scope or scope_file) and not getattr(args, "audit", False):
        return "--scope / --scope-file требуют --audit"
    if scope and scope_file:
        return "--scope и --scope-file взаимоисключающие"
    if getattr(args, "patch", False) and (
        getattr(args, "audit", False) or getattr(args, "plan", False)
    ):
        return "--patch несовместим с --audit/--plan"
    return None


def _resolve_scopes(
    workspace: str,
    args,
    *,
    current: list[str] | None = None,
) -> tuple[list[str] | None, str | None]:
    """B11.3: --scope/--scope-file → относительные пути внутри workspace.

    Возвращает (scopes, error). Если флаги не заданы — (current, None).
    Пути резолвятся; выход за workspace и несуществующие — ошибка.
    """
    raw = list(getattr(args, "scope", None) or [])
    scope_file = getattr(args, "scope_file", None)
    if scope_file is None and not raw:
        return list(current or []), None
    if scope_file is not None:
        try:
            text = Path(scope_file).read_text(encoding="utf-8")
        except OSError as exc:
            return None, f"--scope-file: {exc}"
        for line in text.splitlines():
            s = line.strip()
            if not s or s.startswith("#"):
                continue
            raw.append(s)
    ws = Path(workspace).resolve()
    out: list[str] = []
    for item in raw:
        p = Path(item)
        resolved = p.resolve() if p.is_absolute() else (ws / p).resolve()
        try:
            rel = resolved.relative_to(ws)
        except ValueError:
            return None, f"--scope: путь вне workspace: {item}"
        if not resolved.exists():
            return None, f"--scope: не существует: {item}"
        rel_str = rel.as_posix()
        if rel_str in ("", ".") or rel_str in out:
            continue
        out.append(rel_str)
    return out, None


def _start_audit(workspace: str, session, *, fresh: bool) -> dict:
    """Старт AUDIT: prepare + merge в session (B11.2b).

    ``fresh=True`` — .agent-coder/ удаляется (--fresh-audit). Иначе —
    load существующего state. Findings/todo мержатся через
    WorkStateHandle.update (дедуп B9.4), coverage — в read_paths.
    """
    from agent_coder.agent import audit_storage as _audit_storage
    from agent_coder.agent.work_state import WorkStateHandle

    loaded = _audit_storage.prepare(workspace, fresh=fresh)
    # R15: всего .py в scope — для прогресса «покрыто X/Y».
    loaded["total_files"] = _audit_storage._count_py_files(
        workspace, session.audit_scopes or None
    )
    handle = WorkStateHandle(session.work_state)
    if loaded["findings"]:
        handle.update({"findings+": loaded["findings"]})
    if loaded["todo"]:
        handle.update({"todo+": loaded["todo"]})
    session.work_state = handle.get()
    if loaded["coverage"]:
        session.read_paths.update(loaded["coverage"])
    covered = len(loaded["coverage"])
    remaining = max(0, loaded["total_files"] - covered)
    ui.print_info(
        f"AUDIT state: findings={len(loaded['findings'])}, "
        f"coverage={covered}/{loaded['total_files']} "
        f"(осталось {remaining}), todo={len(loaded['todo'])}"
    )
    return loaded


async def _run_patch_cli(args, session) -> int:
    """One-shot /patch: confirmed findings через PATCH_MODEL_ID (B9.2g).

    Без REPL: загружает сессию (--resume/--continue/latest), берёт
    verify_results со status=confirmed, зовёт run_patch. Результат
    дописывается в session.patch_results. Используется benchmark'ом и
    из терминала: `agent-coder --patch --cwd na_shtile --model deepseek/deepseek-v4.1-flash`.
    """
    from agent_coder.agent.patch import run_patch
    from agent_coder.llm.client import LLMClient

    confirmed = [
        v for v in (session.verify_results or []) if v.get("status") == "confirmed"
    ]
    if not confirmed:
        ui.print_error("нет confirmed findings — сначала /verify")
        return 2
    model = args.model or config.PATCH_MODEL_ID
    ui.print_info(f"Патчу {len(confirmed)} confirmed на {model}...")
    client = LLMClient()
    await client.start()
    try:
        result = await run_patch(client, session, model=args.model or "")
    finally:
        close = getattr(client, "close", None)
        if close is not None:
            await close()
    ui.print_info(
        f"patch: {result.iterations} iter, {result.cost:.2f}₽, "
        f"files={len(result.files_changed)}, "
        f"error={result.error or 'нет'}"
    )
    if result.error:
        ui.print_error(result.error)
        return 1
    return 0


async def _run_verify_cli(args, session) -> int:
    """One-shot /verify: прогнать work_state.findings через VERIFY_MODEL_ID.

    Без REPL: загружает сессию (--resume/--continue/latest), зовёт
    run_verify. Результат дописывается в session.verify_results.
    Используется benchmark'ом и из терминала.
    """
    from agent_coder.agent.verify import run_verify
    from agent_coder.llm.client import LLMClient

    findings = (session.work_state or {}).get("findings") or []
    if not findings:
        ui.print_error("work_state пуст — сначала /audit или update_work_state")
        return 2
    model = args.model or config.VERIFY_MODEL_ID
    ui.print_info(f"Верифицирую {len(findings)} findings на {model}...")
    client = LLMClient()
    await client.start()
    try:
        result = await run_verify(client, session, model=args.model or "")
    finally:
        close = getattr(client, "close", None)
        if close is not None:
            await close()
    ui.print_info(
        f"verify: {result.iterations} iter, {result.cost:.2f}₽, "
        f"confirmed={result.counts.get('confirmed', 0)}, "
        f"error={result.error or 'нет'}"
    )
    if result.error:
        ui.print_error(result.error)
        return 1
    return 0


def main() -> int:
    args = parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.WARNING,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    config.validate()
    if args.audit and args.plan:
        ui.print_error("--audit и --plan нельзя использовать одновременно")
        return 2
    _audit_err = _validate_audit_flags(args)
    if _audit_err:
        ui.print_error(_audit_err)
        return 2
    # --max-iterations применяется ко всем режимам, ТОЛЬКО если указан явно.
    # Иначе уважаем MAX_ITERATIONS_PLAN/ACT/AUDIT из .env.
    if args.max_iterations is not None:
        if args.max_iterations < 1:
            ui.print_error("--max-iterations должен быть >= 1")
            return 2
        config.MAX_ITERATIONS_PLAN = args.max_iterations
        config.MAX_ITERATIONS_ACT = args.max_iterations
        config.MAX_ITERATIONS_AUDIT = args.max_iterations

    workspace = str(Path(args.cwd).resolve())
    if not Path(workspace).is_dir():
        ui.print_error(f"Директория не найдена: {workspace}")
        return 2

    if args.resume:
        session = Session.load(args.resume)
        if session is None:
            ui.print_error(f"Сессия не найдена: {args.resume}")
            return 2
        if args.audit:
            session.mode = Mode.AUDIT
        elif args.plan:
            session.mode = Mode.PLAN
        ui.print_info(f"Возобновлена сессия {session.id}: {session.title}")
    elif args.continue_last:
        session = Session.latest(workspace=workspace)
        if session is None:
            ui.print_error(f"Нет сессий для {workspace}. Создаю новую.")
            mode = Mode.AUDIT if args.audit else (Mode.PLAN if args.plan else Mode.ACT)
            session = Session.new(
                workspace=workspace,
                model=args.model or config.DEFAULT_MODEL_ID,
                model_manual=args.model is not None,
                mode=mode,
            )
        else:
            ui.print_info(f"Продолжаем сессию {session.id}: {session.title}")
    else:
        mode = Mode.AUDIT if args.audit else (Mode.PLAN if args.plan else Mode.ACT)
        session = Session.new(
            workspace=workspace,
            model=args.model or config.DEFAULT_MODEL_ID,
            model_manual=args.model is not None,
            mode=mode,
        )

    if args.verify:
        return asyncio.run(_run_verify_cli(args, session))

    if args.patch:
        return asyncio.run(_run_patch_cli(args, session))

    audit_loaded = None
    if args.audit and session.mode == Mode.AUDIT:
        scopes, scope_err = _resolve_scopes(
            workspace, args, current=session.audit_scopes
        )
        if scope_err:
            ui.print_error(scope_err)
            return 2
        session.audit_scopes = scopes or []
        audit_loaded = _start_audit(workspace, session, fresh=args.fresh_audit)

    try:
        if args.task:
            rc = asyncio.run(run_once(args, session))
        else:
            rc = asyncio.run(run_repl(args, session))
    except KeyboardInterrupt:
        ui.print_info("\nПрервано.")
        rc = 130

    if audit_loaded is not None:
        try:
            from agent_coder.agent import audit_storage as _audit_storage

            _audit_storage.save(workspace, session, audit_loaded)
        except Exception as exc:
            ui.print_error(f"audit save: {exc}")

    return rc


if __name__ == "__main__":
    sys.exit(main())
