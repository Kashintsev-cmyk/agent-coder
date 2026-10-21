import sys
from pathlib import Path

root = Path(sys.argv[1])
(root / "benchmark_target.py").write_text('''def run_task(task: str, model: str) -> dict:\n    return {"status": "ok", "model": model, "task": task}\n''', encoding="utf-8")
(root / "test_benchmark_target.py").write_text('''from benchmark_target import run_task\n\ndef test_default_result():\n    assert run_task("x", "m")["status"] == "ok"\n''', encoding="utf-8")
