#!/usr/bin/env python3
"""Интерактивная разметка gold set: показывает код na_shtile, спрашивает вердикт."""

import json
import os
import re
import sys
from pathlib import Path

GOLD = Path("benchmark/projects/na_shtile/gold_verify.json")
NA = Path(os.environ.get("NA_SHTILE_PATH", "/home/botuser/bots/na_shtile"))
"""Путь к na_shtile. Override: NA_SHTILE_PATH (Фаза C, 2026-11-04)."""


def show_code(file_line: str) -> str:
    m = re.match(r"([\w./-]+\.\w+):(\d+)(?:-(\d+))?", file_line or "")
    if not m:
        return "(не распознал file:line)"
    path = NA / m.group(1)
    start = int(m.group(2))
    end = int(m.group(3) or m.group(2))
    lo, hi = max(1, start - 5), end + 5
    try:
        lines = path.read_text().splitlines()
    except Exception as e:
        return f"(не открыть {path}: {e})"
    out = []
    for i in range(lo, min(hi, len(lines)) + 1):
        mark = ">>" if start <= i <= end else "  "
        out.append(f"{mark} {i:4d}: {lines[i - 1]}")
    return "\n".join(out)


def main() -> int:
    """Интерактивная разметка gold set. Возвращает rc."""
    if not GOLD.exists():
        sys.exit(f"Нет {GOLD}")

    gold = json.loads(GOLD.read_text())
    KEYMAP = {"c": "confirmed", "r": "rejected", "u": "uncertain"}
    for g in gold:
        if g.get("manual_label") in ("confirmed", "rejected"):
            continue
        print("\n" + "=" * 74)
        print(f"ID {g['id']}   файл: {g['file_line']}")
        print(f"\nFINDING:\n  {g['finding_text']}\n")
        print("КОД (строки с >> — то, о чём говорит finding):")
        print(show_code(g["file_line"]))
        print()
        while True:
            ans = (
                input(
                    "Вердикт? [c]onfirmed / [r]ejected / [u]ncertain / [s]kip / [q]uit: "
                )
                .strip()
                .lower()
            )
            if ans in ("c", "r", "u", "s", "q"):
                break
            print("  только c / r / u / s / q")
        if ans == "q":
            break
        if ans == "s":
            continue
        g["manual_label"] = KEYMAP[ans]
        GOLD.write_text(json.dumps(gold, ensure_ascii=False, indent=2))
        print(f"  → сохранено: {g['manual_label']}")

    done = sum(1 for g in gold if g.get("manual_label") in ("confirmed", "rejected"))
    unc = sum(1 for g in gold if g.get("manual_label") == "uncertain")
    print(f"\nГотово: {done} размечено, {unc} uncertain, из {len(gold)}.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
