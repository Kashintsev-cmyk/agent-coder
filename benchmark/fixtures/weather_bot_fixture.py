"""Create the same empty weather-bot starter project for each model case."""
import sys
from pathlib import Path

root = Path(sys.argv[1])
(root / "pyproject.toml").write_text('''[project]
name = "weather-telegram-bot"
version = "0.1.0"
requires-python = ">=3.11"
dependencies = ["httpx>=0.27", "python-telegram-bot>=21,<23"]

[project.optional-dependencies]
test = ["pytest>=8", "pytest-asyncio>=0.23"]

[tool.pytest.ini_options]
asyncio_mode = "auto"
''', encoding="utf-8")
(root / "README.md").write_text('''# Weather Telegram Bot

Starter repository for a small Telegram bot that replies with a current weather
forecast. Implement the application according to the benchmark task.
''', encoding="utf-8")
(root / "src").mkdir(exist_ok=True)
(root / "tests").mkdir(exist_ok=True)
(root / "src" / "__init__.py").write_text("", encoding="utf-8")
(root / "tests" / "test_placeholder.py").write_text('''def test_placeholder():
    assert True
''', encoding="utf-8")
