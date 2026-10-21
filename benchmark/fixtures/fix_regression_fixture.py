import sys
from pathlib import Path

root = Path(sys.argv[1])
(root / "benchmark_target.py").write_text('''def normalize_name(value):\n    return value.lower()\n''', encoding="utf-8")
(root / "test_benchmark_target.py").write_text('''from benchmark_target import normalize_name\n\ndef test_normalize_name_trims_whitespace():\n    assert normalize_name("  Ada ") == "ada"\n''', encoding="utf-8")
