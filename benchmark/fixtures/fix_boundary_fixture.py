import sys
from pathlib import Path

root = Path(sys.argv[1])
(root / "benchmark_target.py").write_text(
    """def chunk(items, size):\n    return [items[i:i + size] for i in range(0, len(items), size)]\n""",
    encoding="utf-8",
)
(root / "test_benchmark_target.py").write_text(
    """from benchmark_target import chunk

def test_chunk_empty_input():
    assert chunk([], 3) == []

def test_chunk_normal_chunking_preserved():
    assert chunk([1, 2, 3, 4], 2) == [[1, 2], [3, 4]]
    assert chunk([1, 2, 3], 2) == [[1, 2], [3]]

def test_chunk_rejects_zero_size():
    try:
        chunk([1], 0)
    except ValueError:
        return
    assert False, "expected ValueError for size=0"

def test_chunk_rejects_negative_size():
    try:
        chunk([1], -1)
    except ValueError:
        return
    assert False, "expected ValueError for size<0"
""",
    encoding="utf-8",
)
