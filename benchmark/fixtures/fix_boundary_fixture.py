import sys
from pathlib import Path

root = Path(sys.argv[1])
(root / "benchmark_target.py").write_text('''def chunk(items, size):\n    return [items[i:i + size] for i in range(0, len(items), size)]\n''', encoding="utf-8")
(root / "test_benchmark_target.py").write_text('''from benchmark_target import chunk\n\ndef test_chunk_empty_input():\n    assert chunk([], 3) == []\n\ndef test_chunk_rejects_zero_size():\n    try:\n        chunk([1], 0)\n    except ValueError:\n        return\n    assert False, "expected ValueError"\n''', encoding="utf-8")
