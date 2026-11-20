#!/usr/bin/env python3
"""Удалить сессии-фикстуры pytest (workspace в tempdir) из SESSIONS_DIR.

Признак мусора — не цена, а происхождение: `session_index.is_scrap_workspace()`
(workspace в системном tempdir или /tmp). Так ловятся все 203 фикстуры,
включая те, что `cleanup_cheap_sessions.py --max-cost 1.0` пропускает:
196 из них имеют непустой work_state (`findings: f0,f1,f2`) и выглядят
«ценными», хотя это литералы из фикстур, а не работа пользователя.

Почему отдельный скрипт, а не `--no-keep-valuable`: там критерий —
цена, и с этим флагом под нож пошли бы и настоящие дешёвые сессии
пользователя. Здесь критерий — tempdir, к цене отношения не имеет.

Происхождение мусора: `run_checks` запускает pytest с `--noconftest`,
conftest не изолирует SESSIONS_DIR, и фикстуры писали сессии прямо в
боевой каталог (A11). Код закрыт (config._resolve_sessions_dir), но
файлы на диске остались.

Использование:
    python scripts/cleanup_scrap_sessions.py --dry-run   # только показать
    python scripts/cleanup_scrap_sessions.py             # удалить (с бэкапом)

Удаление идёт через Session.delete() — как в UI: json, audit-отчёты,
media, архив истории и sidecar-индекс. Перед удалением json кладётся
в --backup-dir (по умолчанию sessions/.cleanup-backup-<ts>/), чтобы
прогон можно было откатить.
"""

from __future__ import annotations

import argparse
import shutil
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agent_coder.agent.session import Session
from agent_coder.agent.session_index import is_scrap_workspace
from agent_coder.config import config


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="только показать, что будет удалено (ничего не менять)",
    )
    parser.add_argument(
        "--backup-dir",
        default="",
        help="куда сохранить json перед удалением "
        "(по умолчанию SESSIONS_DIR/.cleanup-backup-<ts>)",
    )
    parser.add_argument(
        "--no-backup",
        action="store_true",
        help="не сохранять копии (необратимо)",
    )
    args = parser.parse_args()

    all_meta = Session.list_all()
    scrap = [m for m in all_meta if is_scrap_workspace(m.workspace)]
    kept = len(all_meta) - len(scrap)

    if not scrap:
        print(f"Мусорных сессий не найдено (всего {len(all_meta)}) — чистить нечего.")
        return 0

    print(
        f"Каталог: {config.SESSIONS_DIR}\n"
        f"Всего сессий: {len(all_meta)}\n"
        f"Мусор (workspace в tempdir): {len(scrap)}\n"
        f"Останется: {kept}\n"
    )
    for m in sorted(scrap, key=lambda x: x.created_at)[:10]:
        print(f"  {m.id}  {m.workspace}  {(m.title or '(без названия)')[:40]}")
    if len(scrap) > 10:
        print(f"  … и ещё {len(scrap) - 10}")

    if args.dry_run:
        print(f"\n[dry-run] Ничего не удалено. К удалению: {len(scrap)}")
        return 0

    backup_dir: Path | None = None
    if not args.no_backup:
        backup_dir = (
            Path(args.backup_dir)
            if args.backup_dir
            else config.SESSIONS_DIR / f".cleanup-backup-{int(time.time())}"
        )
        backup_dir.mkdir(parents=True, exist_ok=True)
        # .cleanup-backup-* лежит в SESSIONS_DIR, но не *.json в корне —
        # индекс сканирует только glob("*.json"), подкаталог ему не мешает.

    removed = 0
    removed_files = 0
    errors = 0
    for meta in scrap:
        loaded = Session.load(meta.id)
        if loaded is None:
            # Битый json: Session.load вернул None, но файл на диске есть —
            # удаляем напрямую, иначе он навсегда останется невидимым мусором.
            path = config.SESSIONS_DIR / f"{meta.id}.json"
            try:
                if path.exists():
                    if backup_dir is not None:
                        shutil.copy2(path, backup_dir / path.name)
                    path.unlink()
                    removed += 1
                    removed_files += 1
            except OSError as e:
                errors += 1
                print(f"  ! {meta.id}: {e}", file=sys.stderr)
            continue
        try:
            if backup_dir is not None:
                shutil.copy2(
                    config.SESSIONS_DIR / f"{meta.id}.json",
                    backup_dir / f"{meta.id}.json",
                )
            removed_files += loaded.delete()
            removed += 1
        except OSError as e:
            errors += 1
            print(f"  ! ошибка удаления {meta.id}: {e}", file=sys.stderr)

    print(f"\nУдалено сессий: {removed}, файлов: {removed_files}")
    if backup_dir is not None:
        print(f"Бэкап json: {backup_dir}")
        print(f"Откат: скопировать *.json обратно в {config.SESSIONS_DIR}")
    if errors:
        print(f"Ошибок: {errors}", file=sys.stderr)
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
