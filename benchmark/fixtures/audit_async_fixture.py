import sys
from pathlib import Path

root = Path(sys.argv[1])
(root / "benchmark_target.py").write_text('''import asyncio\n\nasync def fetch(client):\n    while True:\n        try:\n            return await client.get("/data")\n        except Exception:\n            await asyncio.sleep(1)\n''', encoding="utf-8")
