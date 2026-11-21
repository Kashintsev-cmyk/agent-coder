from __future__ import annotations

from io import BytesIO
from types import SimpleNamespace
from typing import ClassVar

import pytest

from agent_coder.config import Config, _parse_model_history_token_limits


def test_parse_model_history_token_limits():
    assert _parse_model_history_token_limits(
        "anthropic/claude-haiku-5.5=120000, openai/gpt-5.6-luna=180000"
    ) == {
        "anthropic/claude-haiku-5.5": 120_000,
        "openai/gpt-5.6-luna": 180_000,
    }
    assert _parse_model_history_token_limits("") == {}


@pytest.mark.parametrize(
    "raw",
    ["bad-entry", "model/id=abc", "model/id=1000,model/id=2000"],
)
def test_parse_model_history_token_limits_rejects_invalid_input(raw):
    with pytest.raises(ValueError, match="MODEL_HISTORY_TOKEN_LIMITS"):
        _parse_model_history_token_limits(raw)


def test_config_validation_rejects_unknown_limit_model(monkeypatch):
    class _Invalid(Config):
        MODEL_HISTORY_TOKEN_LIMITS: ClassVar[dict[str, int]] = {"unknown/model": 10_000}

    monkeypatch.setattr(_Invalid, "ROUTERAI_API_KEY", "test-key")
    with pytest.raises(ValueError, match="модели вне MODEL_IDS"):
        _Invalid.validate()


def test_config_validation_rejects_too_small_limit(monkeypatch):
    class _Invalid(Config):
        MODEL_HISTORY_TOKEN_LIMITS: ClassVar[dict[str, int]] = {
            Config.DEFAULT_MODEL_ID: 999
        }

    monkeypatch.setattr(_Invalid, "ROUTERAI_API_KEY", "test-key")
    with pytest.raises(ValueError, match="должны быть >= 1000"):
        _Invalid.validate()


@pytest.mark.asyncio
async def test_oversized_document_is_rejected_before_task_start(monkeypatch):
    import agent_coder.telegram.handlers.task as task_handler
    from agent_coder.agent import compression

    session = SimpleNamespace(model=Config.DEFAULT_MODEL_ID, messages=[])
    monkeypatch.setattr(task_handler, "_get_active_session", lambda chat_id: session)
    monkeypatch.setattr(compression, "would_exceed_soft_limit", lambda _s, _text: True)

    started = False

    async def _start_task(*args, **kwargs):
        nonlocal started
        started = True

    monkeypatch.setattr(task_handler, "_start_task", _start_task)

    class _Bot:
        async def download(self, document):
            return BytesIO(b"large enough for a prompt")

    class _Message:
        chat = SimpleNamespace(id=42)
        caption = ""
        document = SimpleNamespace(
            file_name="prompt.txt", file_size=20, mime_type="text/plain"
        )

        def __init__(self):
            self.answers: list[str] = []

        async def answer(self, text, **kwargs):
            self.answers.append(text)

    message = _Message()
    await task_handler.handle_task_document(message, _Bot())

    assert not started
    assert len(message.answers) == 1
    assert "Раздели файл" in message.answers[0]
    assert "Автоматическая" in message.answers[0]


@pytest.mark.asyncio
async def test_document_without_active_session_is_not_downloaded(monkeypatch):
    import agent_coder.telegram.handlers.task as task_handler

    monkeypatch.setattr(task_handler, "_get_active_session", lambda chat_id: None)

    class _Bot:
        downloaded = False

        async def download(self, document):
            self.downloaded = True
            return BytesIO(b"content")

    class _Message:
        chat = SimpleNamespace(id=42)
        document = SimpleNamespace(file_name="prompt.txt", file_size=7)
        caption = ""

        def __init__(self):
            self.answers: list[str] = []

        async def answer(self, text, **kwargs):
            self.answers.append(text)

    bot = _Bot()
    message = _Message()
    await task_handler.handle_task_document(message, bot)

    assert not bot.downloaded
    assert message.answers == ["Сначала выбери проект: /projects"]
