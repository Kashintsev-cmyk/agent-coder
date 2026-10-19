"""MAX_ITERATIONS_AUDIT: дефолт 200 (Риск 3 — стоимость аудита).

AUDIT × MAX_AUTO_CONTINUE+1 = макс. итераций за запуск. 300×4=1200
было слишком дорого (93108d9a: ~1200 tool-раундов за 4 прогона).
200×4=800 — аудит идёт волнами с автопродолжением.
"""

from __future__ import annotations

import subprocess
import sys


def test_audit_iterations_default_200() -> None:
    """Чистый env (без MAX_ITERATIONS_AUDIT) → дефолт 200."""
    code = "from agent_coder.config import config; print(config.MAX_ITERATIONS_AUDIT)"
    env = {
        k: v for k, v in __import__("os").environ.items() if k != "MAX_ITERATIONS_AUDIT"
    }
    out = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
        env=env,
        check=True,
    )
    assert out.stdout.strip() == "200", (
        f"дефолт MAX_ITERATIONS_AUDIT={out.stdout.strip()}, ожидался 200"
    )
