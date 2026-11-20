"""Backfill chat_id для сессий, созданных до появления поля.

Зачем: поле `chat_id` появилось вместе с фильтром /sessions. У старых
сессий его нет → они читаются как 0 и не попадают в список своего чата
(юзер видел бы пустой /sessions). Скрипт проставляет владельца тем
сессиям, которые действительно содержат работу.

Мусор от pytest (`workspace=/tmp`, пустые прогоны) намеренно остаётся
с chat_id=0: он не должен всплывать в /sessions. Чистит его отдельный
скрипт cleanup.

Правка минимальная: в JSON добавляется одно поле, остальное содержимое
не пере-сериализуется (никакой потери неизвестных полей). Запись
атомарная — tmp + os.replace.

Использование:
    python scripts/backfill_chat_id.py --chat-id 201821839            # dry-run
    python scripts/backfill_chat_id.py --chat-id 201821839 --apply    # записать
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agent_coder.agent.session_index import is_scrap_workspace, is_session_file
from agent_coder.config import config


def _is_scrap(data: dict) -> bool:
    """Мусор от pytest: временный workspace или пустой прогон без работы."""
    workspace = str(data.get("workspace") or "")
    if is_scrap_workspace(workspace):
        return True
    messages = data.get("messages") or []
    if not messages:
        usage = data.get("total_usage") or {}
        cost = float(usage.get("cost") or 0.0)
        if cost <= 0.0:
            return True
    return False


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--chat-id", type=int, required=True, help="владелец сессий")
    ap.add_argument(
        "--apply", action="store_true", help="записать изменения (по умолчанию dry-run)"
    )
    args = ap.parse_args()

    sessions_dir = config.SESSIONS_DIR
    if not sessions_dir.is_dir():
        print(f"Нет каталога сессий: {sessions_dir}")
        return 1

    todo: list[Path] = []
    skipped_owned = 0
    skipped_scrap = 0
    broken = 0

    for path in sorted(sessions_dir.glob("*.json")):
        if not is_session_file(path.name):
            continue
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            broken += 1
            continue
        if int(data.get("chat_id") or 0) != 0:
            skipped_owned += 1
            continue
        if _is_scrap(data):
            skipped_scrap += 1
            continue
        todo.append(path)

    print(f"Каталог: {sessions_dir}")
    print(f"Уже с владельцем:      {skipped_owned}")
    print(f"Мусор (останется 0):   {skipped_scrap}")
    print(f"Битые (пропущены):     {broken}")
    print(f"К проставке:           {len(todo)}")

    if not args.apply:
        for path in todo[:10]:
            print(f"  · {path.name}")
        if len(todo) > 10:
            print(f"  … и ещё {len(todo) - 10}")
        print("\nDry-run. Для записи добавь --apply")
        return 0

    written = 0
    for path in todo:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        data["chat_id"] = args.chat_id
        tmp = path.with_name(path.name + ".tmp")
        try:
            tmp.write_text(
                json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            os.replace(tmp, path)
            written += 1
        except OSError as exc:
            print(f"  ! {path.name}: {exc}")
            tmp.unlink(missing_ok=True)

    print(f"\nЗаписано: {written}")
    print("Индекс пересоберётся сам при первом Session.list_all().")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
