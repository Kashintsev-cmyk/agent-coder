import sys
from pathlib import Path

root = Path(sys.argv[1])
(root / "benchmark_target.py").write_text('''import logging\nimport os\n\nlogger = logging.getLogger(__name__)\nAPI_TOKEN = os.getenv("API_TOKEN", "development-token")\n\ndef log_request(headers):\n    logger.info("request headers=%r", headers)\n''', encoding="utf-8")
