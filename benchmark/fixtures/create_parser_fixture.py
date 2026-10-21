import sys
from pathlib import Path

root = Path(sys.argv[1])
(root / "benchmark_target.py").write_text('''def existing(value):\n    return value.strip()\n''', encoding="utf-8")
(root / "test_benchmark_target.py").write_text('''from benchmark_target import existing\n\ndef test_existing():\n    assert existing(" ok ") == "ok"\n''', encoding="utf-8")
