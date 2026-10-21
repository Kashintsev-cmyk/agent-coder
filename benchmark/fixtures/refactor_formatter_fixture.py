import sys
from pathlib import Path

root = Path(sys.argv[1])
(root / "benchmark_target.py").write_text('''def format_user(name, score):\n    return f"{name.strip()}: {score:.2f}"\n\ndef format_admin(name, score):\n    return f"{name.strip()}: {score:.2f}"\n''', encoding="utf-8")
(root / "test_benchmark_target.py").write_text('''from benchmark_target import format_user, format_admin\n\ndef test_formats():\n    assert format_user(" A ", 1) == "A: 1.00"\n    assert format_admin(" B ", 2) == "B: 2.00"\n''', encoding="utf-8")
