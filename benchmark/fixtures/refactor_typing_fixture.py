import sys
from pathlib import Path

root = Path(sys.argv[1])
(root / "benchmark_target.py").write_text('''def combine(items, separator):\n    return separator.join(str(item) for item in items)\n''', encoding="utf-8")
(root / "test_benchmark_target.py").write_text('''from benchmark_target import combine\n\ndef test_combine():\n    assert combine([1, "x"], ",") == "1,x"\n''', encoding="utf-8")
