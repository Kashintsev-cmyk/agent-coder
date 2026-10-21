import sys
from pathlib import Path

root = Path(sys.argv[1])
(root / "benchmark_target.py").write_text('''"""Tiny target module for the retry benchmark."""\n\nasync def fetch_value():\n    return "ready"\n''', encoding="utf-8")
(root / "test_benchmark_target.py").write_text('''import asyncio\nfrom benchmark_target import fetch_value\n\ndef test_fetch_value():\n    assert asyncio.run(fetch_value()) == "ready"\n''', encoding="utf-8")
