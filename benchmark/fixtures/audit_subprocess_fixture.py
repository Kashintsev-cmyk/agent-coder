import sys
from pathlib import Path

root = Path(sys.argv[1])
(root / "benchmark_target.py").write_text('''import subprocess\n\ndef show_file(path):\n    return subprocess.run("cat " + path, shell=True, capture_output=True, text=True)\n''', encoding="utf-8")
