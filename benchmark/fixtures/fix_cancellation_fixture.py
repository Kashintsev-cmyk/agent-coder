import sys
from pathlib import Path

root = Path(sys.argv[1])
(root / "benchmark_target.py").write_text('''import asyncio\n\nasync def wait_and_close(resource):\n    try:\n        await asyncio.sleep(60)\n    except asyncio.CancelledError:\n        return None\n    finally:\n        await resource.close()\n''', encoding="utf-8")
(root / "test_benchmark_target.py").write_text('''import asyncio\nfrom benchmark_target import wait_and_close\n\nclass Resource:\n    def __init__(self): self.closed = False\n    async def close(self): self.closed = True\n\ndef test_cancel_propagates_and_closes():\n    async def run():\n        resource = Resource()\n        task = asyncio.create_task(wait_and_close(resource))\n        await asyncio.sleep(0)\n        task.cancel()\n        try:\n            await task\n        except asyncio.CancelledError:\n            pass\n        else:\n            assert False, "cancellation did not propagate"\n        assert resource.closed\n    asyncio.run(run())\n''', encoding="utf-8")
