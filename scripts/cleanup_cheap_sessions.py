#!/usr/bin/env python3
"""Удалить дешёвые сессии из config.SESSIONS_DIR (cost < --max-cost, по умолчанию 1.0₽).

Критерий: total_usage.cost < --max-cost.
Защита ценных данных: сессии с непустым work_state (findings/todo/user_requests)
или verify_results НЕ удаляются независимо от цены (--no-keep-valuable отключает).

Происхождение: тесты (до фикса в tests/conftest.py) писали фикстурные
сессии прямо в боевой sessions/ (254 /tmp-сессии 17–20.11.2026) — они
заполняли топ /sessions и вытесняли содержательные.

Использование:
    python scripts/cleanup_cheap_sessions.py --dry-run   # только показать
    python scripts/cleanup_cheap_sessions.py             # удалить

Удаление идёт через Session.delete() — как в UI: чистит json,
audit-отчёты, media, архив истории и sidecar-индекс.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agent_coder.agent.session import Session


def _is_valuable(s: Session) -> bool:
    """Сессия содержит результаты, которые дороже своей цены."""
    ws = s.work_state or {}
    return bool(
        ws.get("findings")
        or ws.get("todo")
        or ws.get("user_requests")
        or s.verify_results
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--max-cost",
        type=float,
        default=1.0,
        help="порог цены в рублях (по умолчанию 1.0)",
    )
    parser.add_argument(
        "--no-keep-valuable",
        action="store_true",
        help="удалять даже сессии с findings/verify_results (осторожно!)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="только показать, что будет удалено (ничего не менять)",
    )
    args = parser.parse_args()

    # ВАЖНО: Session.list_all() возвращает меты БЕЗ messages (list_all
    # не грузит полные JSON ради скорости). Критерий проверяем по полной
    # сессии из Session.load().
    all_sessions = Session.list_all()
    candidates: list[Session] = []
    skipped_valuable = 0
    for meta in all_sessions:
        loaded = Session.load(meta.id)
        if loaded is None:
            continue
        if loaded.total_usage.cost >= args.max_cost:
            continue
        if not args.no_keep_valuable and _is_valuable(loaded):
            skipped_valuable += 1
            continue
        candidates.append(loaded)

    if not candidates:
        print(
            f"Сессий с cost < {args.max_cost} не найдено "
            f"(пропущено ценных: {skipped_valuable}) — чистить нечего."
        )
        return 0

    print(
        f"Сессий с cost < {args.max_cost}: {len(candidates)} "
        f"из {len(all_sessions)} (пропущено ценных: {skipped_valuable})\n"
    )
    for s in candidates:
        title = s.title or "(без названия)"
        print(f"  {s.id}  {s.total_usage.cost:6.2f}₽  {s.workspace}  {title[:40]}")

    if args.dry_run:
        print(f"\n[dry-run] Ничего не удалено. К удалению: {len(candidates)}")
        return 0

    removed_files = 0
    errors = 0
    for s in candidates:
        try:
            removed_files += s.delete()
        except OSError as e:
            errors += 1
            print(f"  ! ошибка удаления {s.id}: {e}", file=sys.stderr)

    print(f"\nУдалено сессий: {len(candidates) - errors}, файлов: {removed_files}")
    if errors:
        print(f"Ошибок: {errors}", file=sys.stderr)
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
