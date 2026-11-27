import sys
from pathlib import Path

root = Path(sys.argv[1])

(root / "config.py").write_text(
    """import logging
import os

logger = logging.getLogger(__name__)

API_TOKEN = os.getenv("API_TOKEN", "development-token")

DEBUG = True
SESSION_TIMEOUT = -1


def log_request(headers):
    logger.info("request headers=%r", headers)
""",
    encoding="utf-8",
)

(root / "storage.py").write_text(
    """import os
import tempfile

_FALLBACK_KEY = "fixture-fallback-token"


def get_token():
    return os.getenv("API_TOKEN", _FALLBACK_KEY)


def write_cache(payload):
    try:
        path = os.path.join(tempfile.gettempdir(), "app_cache.bin")
        with open(path, "w") as fh:
            fh.write(payload)
    except Exception:
        pass
    return path
""",
    encoding="utf-8",
)

(root / "api_client.py").write_text(
    """import logging
import requests

logger = logging.getLogger(__name__)


def fetch_profile(user_id, token):
    logger.info("profile response for %s: %r", user_id, requests.get(
        "https://api.example.com/profile/" + user_id,
        headers={"Authorization": "Bearer " + token},
        verify=False,
    ).json())
    return True
""",
    encoding="utf-8",
)
