import sys
from pathlib import Path

root = Path(sys.argv[1])
(root / "benchmark_target.py").write_text('''def parse_age(value):\n    if not isinstance(value, int):\n        raise ValueError("age must be an integer")\n    if value < 0:\n        raise ValueError("age must be non-negative")\n    return value\n\ndef parse_score(value):\n    if not isinstance(value, int):\n        raise ValueError("score must be an integer")\n    if value < 0:\n        raise ValueError("score must be non-negative")\n    return value\n''', encoding="utf-8")
(root / "test_benchmark_target.py").write_text('''import pytest\nfrom benchmark_target import parse_age, parse_score\n\ndef test_parsers():\n    assert parse_age(1) == 1\n    assert parse_score(2) == 2\n    with pytest.raises(ValueError, match="age must be an integer"):\n        parse_age("1")\n''', encoding="utf-8")
